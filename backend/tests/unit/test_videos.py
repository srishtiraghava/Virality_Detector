from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from kombu.exceptions import OperationalError as KombuOperationalError
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.analysis_job import AnalysisJob
from app.models.enums import AnalysisJobStatus, VideoStatus
from app.models.user import User
from app.models.video import Video
from app.repositories.users import create_user
from app.repositories.videos import create_video
from app.schemas.user import UserCreate
from app.schemas.video import VideoCreate
from app.services import analysis_queue
from app.services.upload.storage import (
    LocalFilesystemStorage,
    StagedDeletion,
    StorageError,
    get_storage_backend,
)
from app.services.video import service as video_service

_TEST_SECRET = "video-unit-test-jwt-signing-key-with-32-bytes"


@dataclass(frozen=True)
class AuthenticatedVideoUser:
    user: User
    headers: dict[str, str] = field(repr=False)


@pytest.fixture
def configured_video_user(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AuthenticatedVideoUser:
    monkeypatch.setattr(settings, "secret_key", SecretStr(_TEST_SECRET))
    monkeypatch.setattr(settings, "storage_local_path", tmp_path / "storage")
    monkeypatch.setattr(analysis_queue, "dispatch_analysis_job", lambda job_id: None)
    user = create_user(
        db_session,
        UserCreate(email="video-owner@example.com", password="integration-password"),
        hashed_password=hash_password("integration-password"),
    )
    return AuthenticatedVideoUser(
        user=user,
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )


def _create_video(
    session: Session,
    user_id: UUID,
    storage_key: str,
) -> Video:
    return create_video(
        session,
        user_id,
        VideoCreate(
            original_filename="saved.mp4",
            storage_key=storage_key,
            file_size=100,
            duration=1,
            width=64,
            height=48,
            fps=24,
            aspect_ratio=4 / 3,
            video_codec="mpeg4",
            has_audio=False,
            target_platform="test",
            content_category="test",
        ),
        status=VideoStatus.AVAILABLE,
    )


@pytest.mark.anyio
async def test_valid_upload_persists_probe_metadata_and_generated_key(
    client: httpx.AsyncClient,
    configured_video_user: AuthenticatedVideoUser,
    db_session: Session,
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    user, headers = configured_video_user.user, configured_video_user.headers
    with sample_video_file.open("rb") as media:
        response = await client.post(
            "/api/v1/videos/upload",
            data={"target_platform": "shorts", "content_category": "education"},
            files={"file": ("C:\\private\\original.mp4", media, "video/mp4")},
            headers=headers,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(user.id)
    assert body["original_filename"] == "original.mp4"
    assert body["status"] == "available"
    assert body["duration"] > 0
    assert body["width"] == 64
    assert body["height"] == 48
    assert body["fps"] == pytest.approx(24)
    assert body["video_codec"] == "mpeg4"
    assert body["has_audio"] is True
    assert body["storage_key"] != body["original_filename"]
    assert (tmp_path / "storage" / body["storage_key"]).is_file()

    stored_video = db_session.scalar(
        select(Video).where(Video.id == UUID(body["id"]))
    )
    assert stored_video is not None
    assert stored_video.user_id == user.id


@pytest.mark.anyio
async def test_upload_rejects_empty_unsupported_and_invalid_media(
    client: httpx.AsyncClient,
    configured_video_user: AuthenticatedVideoUser,
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    headers = configured_video_user.headers
    form_data = {"target_platform": "shorts", "content_category": "education"}

    empty = await client.post(
        "/api/v1/videos/upload",
        data=form_data,
        files={"file": ("empty.mp4", b"", "video/mp4")},
        headers=headers,
    )
    unsupported = await client.post(
        "/api/v1/videos/upload",
        data=form_data,
        files={"file": ("document.txt", b"content", "text/plain")},
        headers=headers,
    )
    invalid_mime = await client.post(
        "/api/v1/videos/upload",
        data=form_data,
        files={"file": ("video.mp4", b"content", "application/octet-stream")},
        headers=headers,
    )
    invalid_media = await client.post(
        "/api/v1/videos/upload",
        data=form_data,
        files={"file": ("invalid.mp4", b"not video media", "video/mp4")},
        headers=headers,
    )
    with sample_video_file.open("rb") as media:
        mismatched_container = await client.post(
            "/api/v1/videos/upload",
            data=form_data,
            files={"file": ("renamed.mkv", media, "video/x-matroska")},
            headers=headers,
        )

    assert empty.status_code == 400
    assert unsupported.status_code == 415
    assert invalid_mime.status_code == 415
    assert invalid_media.status_code == 422
    assert mismatched_container.status_code == 422
    assert not (tmp_path / "storage").exists()


@pytest.mark.anyio
async def test_upload_rejects_oversized_file_before_media_probe(
    client: httpx.AsyncClient,
    configured_video_user: AuthenticatedVideoUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = configured_video_user.headers
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)

    response = await client.post(
        "/api/v1/videos/upload",
        data={"target_platform": "shorts", "content_category": "education"},
        files={
            "file": (
                "oversized.mp4",
                b"x" * (1024 * 1024 + 1),
                "video/mp4",
            )
        },
        headers=headers,
    )

    assert response.status_code == 413


@pytest.mark.anyio
async def test_storage_failure_is_sanitized_and_does_not_create_video(
    client: httpx.AsyncClient,
    configured_video_user: AuthenticatedVideoUser,
    db_session: Session,
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    class FailingStorage:
        def __init__(self) -> None:
            self.local_storage = LocalFilesystemStorage(tmp_path / "failed-storage")

        def save(self, source_path: Path, storage_key: str) -> None:
            self.local_storage.save(source_path, storage_key)
            raise StorageError("private storage path")

        def stage_delete(self, storage_key: str) -> StagedDeletion:
            return self.local_storage.stage_delete(storage_key)

    user, headers = configured_video_user.user, configured_video_user.headers
    failing_storage = FailingStorage()
    app.dependency_overrides[get_storage_backend] = lambda: failing_storage
    try:
        with sample_video_file.open("rb") as media:
            response = await client.post(
                "/api/v1/videos/upload",
                data={"target_platform": "shorts", "content_category": "education"},
                files={"file": ("upload.mp4", media, "video/mp4")},
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_storage_backend, None)

    assert response.status_code == 503
    assert "private storage path" not in response.text
    assert not list((tmp_path / "failed-storage").rglob("*.mp4"))
    assert db_session.scalar(select(Video).where(Video.user_id == user.id)) is None


@pytest.mark.anyio
async def test_video_ownership_analysis_jobs_and_deletion(
    client: httpx.AsyncClient,
    configured_video_user: AuthenticatedVideoUser,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, owner_headers = configured_video_user.user, configured_video_user.headers
    other_user = create_user(
        db_session,
        UserCreate(email="other-owner@example.com", password="integration-password"),
        hashed_password=hash_password("integration-password"),
    )
    other_headers = {
        "Authorization": f"Bearer {create_access_token(other_user.id)}",
    }
    own_file = tmp_path / "storage" / "own.mp4"
    other_file = tmp_path / "storage" / "other.mp4"
    own_file.parent.mkdir(parents=True)
    own_file.write_bytes(b"own")
    other_file.write_bytes(b"other")
    own_video = _create_video(db_session, owner.id, "own.mp4")
    other_video = _create_video(db_session, other_user.id, "other.mp4")
    monkeypatch.setattr(settings, "storage_local_path", tmp_path / "storage")

    listing = await client.get("/api/v1/videos", headers=owner_headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [str(own_video.id)]

    own_read = await client.get(f"/api/v1/videos/{own_video.id}", headers=owner_headers)
    foreign_read = await client.get(
        f"/api/v1/videos/{other_video.id}",
        headers=owner_headers,
    )
    assert own_read.status_code == 200
    assert foreign_read.status_code == 404

    first_job = await client.post(
        f"/api/v1/videos/{own_video.id}/analyze",
        headers=owner_headers,
    )
    second_job = await client.post(
        f"/api/v1/videos/{own_video.id}/analyze",
        headers=owner_headers,
    )
    assert first_job.status_code == 201
    assert second_job.status_code == 201
    assert first_job.json()["id"] != second_job.json()["id"]
    assert first_job.json()["status"] == "queued"
    assert first_job.json()["progress"] == 0

    job_id = first_job.json()["id"]
    own_job = await client.get(f"/api/v1/jobs/{job_id}", headers=owner_headers)
    foreign_job = await client.get(f"/api/v1/jobs/{job_id}", headers=other_headers)
    foreign_analyze = await client.post(
        f"/api/v1/videos/{other_video.id}/analyze",
        headers=owner_headers,
    )
    assert own_job.status_code == 200
    assert foreign_job.status_code == 404
    assert foreign_analyze.status_code == 404

    foreign_delete = await client.delete(
        f"/api/v1/videos/{other_video.id}",
        headers=owner_headers,
    )
    own_delete = await client.delete(
        f"/api/v1/videos/{own_video.id}",
        headers=owner_headers,
    )
    assert foreign_delete.status_code == 404
    assert other_file.is_file()
    assert own_delete.status_code == 204
    assert not own_file.exists()
    assert db_session.get(Video, own_video.id) is None


@pytest.mark.anyio
async def test_analysis_dispatch_failure_returns_job_id_and_persists_failed_state(
    client: httpx.AsyncClient,
    configured_video_user: AuthenticatedVideoUser,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = configured_video_user.user
    video = _create_video(db_session, user.id, "dispatch-failure.mp4")

    def fail_dispatch(job_id: UUID) -> None:
        raise KombuOperationalError("broker unavailable")

    monkeypatch.setattr(analysis_queue, "dispatch_analysis_job", fail_dispatch)
    response = await client.post(
        f"/api/v1/videos/{video.id}/analyze",
        headers=configured_video_user.headers,
    )

    assert response.status_code == 503
    job_id = UUID(response.json()["detail"]["job_id"])
    job = db_session.get(AnalysisJob, job_id)
    assert job is not None
    assert job.status == AnalysisJobStatus.FAILED
    assert job.error_message == "Analysis task could not be dispatched"


def test_video_delete_restores_file_when_database_delete_fails(
    configured_video_user: AuthenticatedVideoUser,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = configured_video_user.user
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    media_path = storage_root / "rollback.mp4"
    media_path.write_bytes(b"media")
    video = _create_video(db_session, user.id, "rollback.mp4")
    storage = LocalFilesystemStorage(storage_root)

    def fail_delete(_session: Session, _user_id: UUID, _video_id: UUID) -> bool:
        del _session, _user_id, _video_id
        raise OperationalError("DELETE", {}, Exception("database unavailable"))

    monkeypatch.setattr(video_service, "delete_video", fail_delete)

    with pytest.raises(OperationalError):
        video_service.delete_owned_video(db_session, user.id, video.id, storage)

    assert media_path.is_file()
    assert db_session.get(Video, video.id) is not None


@pytest.mark.anyio
async def test_video_and_job_endpoints_require_authentication(
    client: httpx.AsyncClient,
) -> None:
    video_id = UUID("00000000-0000-0000-0000-000000000001")
    job_id = UUID("00000000-0000-0000-0000-000000000002")

    responses = [
        await client.get("/api/v1/videos"),
        await client.post(
            "/api/v1/videos/upload",
            data={"target_platform": "shorts", "content_category": "education"},
            files={"file": ("sample.mp4", b"video", "video/mp4")},
        ),
        await client.get(f"/api/v1/videos/{video_id}"),
        await client.post(f"/api/v1/videos/{video_id}/analyze"),
        await client.get(f"/api/v1/jobs/{job_id}"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401]
