from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.models.video import Video
from app.repositories.users import create_user
from app.schemas.user import UserCreate


@pytest.mark.anyio
async def test_postgresql_video_upload_jobs_ownership_and_deletion(
    video_integration_client: httpx.AsyncClient,
    integration_session: Session,
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    owner = create_user(
        integration_session,
        UserCreate(email="video.integration.owner@example.com", password="integration-password"),
        hashed_password=hash_password("integration-password"),
    )
    other_user = create_user(
        integration_session,
        UserCreate(email="video.integration.other@example.com", password="integration-password"),
        hashed_password=hash_password("integration-password"),
    )
    owner_headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(other_user.id)}"}

    with sample_video_file.open("rb") as media:
        upload = await video_integration_client.post(
            "/api/v1/videos/upload",
            data={"target_platform": "shorts", "content_category": "education"},
            files={"file": ("sample.mp4", media, "video/mp4")},
            headers=owner_headers,
        )

    assert upload.status_code == 201
    upload_body = upload.json()
    video_id = UUID(upload_body["id"])
    stored_path = tmp_path / "storage" / upload_body["storage_key"]
    assert stored_path.is_file()
    assert upload_body["status"] == "available"
    assert upload_body["video_codec"] == "mpeg4"
    assert upload_body["has_audio"] is True

    persisted_video = integration_session.scalar(
        select(Video).where(Video.id == video_id)
    )
    assert persisted_video is not None
    assert persisted_video.video_codec == "mpeg4"
    assert persisted_video.has_audio is True

    own_video = await video_integration_client.get(
        f"/api/v1/videos/{video_id}",
        headers=owner_headers,
    )
    foreign_video = await video_integration_client.get(
        f"/api/v1/videos/{video_id}",
        headers=other_headers,
    )
    foreign_list = await video_integration_client.get(
        "/api/v1/videos",
        headers=other_headers,
    )
    assert own_video.status_code == 200
    assert foreign_video.status_code == 404
    assert foreign_list.json() == []

    first_job = await video_integration_client.post(
        f"/api/v1/videos/{video_id}/analyze",
        headers=owner_headers,
    )
    second_job = await video_integration_client.post(
        f"/api/v1/videos/{video_id}/analyze",
        headers=owner_headers,
    )
    assert first_job.status_code == 201
    assert second_job.status_code == 201
    assert first_job.json()["id"] != second_job.json()["id"]

    job_id = first_job.json()["id"]
    own_job = await video_integration_client.get(
        f"/api/v1/jobs/{job_id}",
        headers=owner_headers,
    )
    foreign_job = await video_integration_client.get(
        f"/api/v1/jobs/{job_id}",
        headers=other_headers,
    )
    assert own_job.status_code == 200
    assert own_job.json()["status"] == "queued"
    assert foreign_job.status_code == 404

    deletion = await video_integration_client.delete(
        f"/api/v1/videos/{video_id}",
        headers=owner_headers,
    )
    assert deletion.status_code == 204
    assert not stored_path.exists()
    assert integration_session.get(Video, video_id) is None
