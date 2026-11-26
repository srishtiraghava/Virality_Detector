from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.enums import AnalysisJobStatus, VideoStatus
from app.repositories.analysis_jobs import (
    create_analysis_job,
    get_analysis_job_by_id,
    update_analysis_job,
)
from app.repositories.users import create_user
from app.repositories.videos import create_video
from app.schemas import (
    AnalysisJobCreate,
    AnalysisJobStatusUpdate,
    UserCreate,
    VideoCreate,
)
from app.workers import tasks as worker_tasks


@pytest.mark.anyio
async def test_postgresql_worker_processes_video_and_persists_artifact(
    video_integration_client: httpx.AsyncClient,
    integration_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    user = create_user(
        integration_session,
        UserCreate(
            email=f"{uuid4().hex}@example.com",
            password="worker-integration-password",
        ),
        hashed_password="integration-hash",
    )
    storage_key = f"{uuid4().hex}.mp4"
    video = create_video(
        integration_session,
        user.id,
        VideoCreate(
            original_filename="worker-video.mp4",
            storage_key=storage_key,
            file_size=100,
            target_platform="test",
            content_category="test",
        ),
        status=VideoStatus.AVAILABLE,
    )
    job = create_analysis_job(
        integration_session,
        AnalysisJobCreate(video_id=video.id),
    )
    job = update_analysis_job(
        integration_session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.QUEUED),
    )
    assert job is not None

    media_path = tmp_path / "storage" / storage_key
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(sample_video_file.read_bytes())

    @contextmanager
    def use_isolated_session():
        try:
            yield integration_session
            integration_session.commit()
        except Exception:
            integration_session.rollback()
            raise

    monkeypatch.setattr(worker_tasks, "worker_session", use_isolated_session)
    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    persisted_job = get_analysis_job_by_id(integration_session, job.id)
    assert result["status"] == "completed"
    assert int(result["frame_count"]) > 0
    assert persisted_job is not None
    assert persisted_job.status == AnalysisJobStatus.COMPLETED
    assert persisted_job.progress == 100
    assert (
        tmp_path
        / "storage"
        / "analysis"
        / str(video.id)
        / str(job.id)
        / "v1"
        / "metadata.json"
    ).is_file()
