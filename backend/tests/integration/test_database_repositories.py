from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.enums import AnalysisJobStatus
from app.repositories.analysis_jobs import (
    create_analysis_job,
    get_analysis_job_by_id,
    update_analysis_job,
)
from app.repositories.users import create_user
from app.repositories.videos import create_video, get_video_by_id, list_videos
from app.schemas import (
    AnalysisJobCreate,
    AnalysisJobStatusUpdate,
    UserCreate,
    VideoCreate,
)


def test_postgresql_schema_and_repository_operations(
    integration_session: Session,
) -> None:
    assert integration_session.execute(text("SELECT 1")).scalar_one() == 1

    user = create_user(
        integration_session,
        UserCreate(email="integration@example.com", password="integration-password"),
        hashed_password="integration-test-hash",
    )
    video = create_video(
        integration_session,
        user.id,
        VideoCreate(
            original_filename="integration.mp4",
            storage_key="integration/integration.mp4",
            file_size=2048,
            target_platform="test",
            content_category="test",
        ),
    )
    first_job = create_analysis_job(
        integration_session,
        AnalysisJobCreate(video_id=video.id),
    )
    second_job = create_analysis_job(
        integration_session,
        AnalysisJobCreate(video_id=video.id),
    )

    assert get_video_by_id(integration_session, user.id, video.id) is not None
    assert [item.id for item in list_videos(integration_session, user.id)] == [video.id]
    assert get_analysis_job_by_id(integration_session, first_job.id) is not None
    assert first_job.id != second_job.id

    queued = update_analysis_job(
        integration_session,
        first_job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.QUEUED),
    )
    assert queued is not None
    processing = update_analysis_job(
        integration_session,
        first_job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.PROCESSING, progress=50),
    )
    assert processing is not None
    assert processing.progress == 50
