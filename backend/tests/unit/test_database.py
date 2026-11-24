from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AnalysisJob, AnalysisJobStatus, User, Video
from app.repositories.analysis_jobs import (
    create_analysis_job,
    get_analysis_job_by_id,
    update_analysis_job,
)
from app.repositories.users import create_user, get_user_by_email, get_user_by_id
from app.repositories.videos import (
    create_video,
    delete_video,
    get_video_by_id,
    list_videos,
)
from app.schemas import (
    AnalysisJobCreate,
    AnalysisJobStatusUpdate,
    UserCreate,
    UserResponse,
    VideoCreate,
)


def make_user(session: Session, email: str = "owner@example.com") -> User:
    return create_user(
        session,
        UserCreate(email=email, password="unit-test-password", full_name="Video Owner"),
        hashed_password="test-hash",
    )


def make_video(session: Session, user_id, filename: str = "clip.mp4") -> Video:
    return create_video(
        session,
        user_id,
        VideoCreate(
            original_filename=filename,
            storage_key=f"videos/{uuid4()}.mp4",
            file_size=1024,
            target_platform="test",
            content_category="test",
        ),
    )


def test_models_relationships_and_user_response_excludes_password(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    video = make_video(db_session, user.id)
    first_job = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))
    second_job = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))

    assert user.videos == [video]
    assert video.user is user
    assert set(video.analysis_jobs) == {first_job, second_job}
    assert first_job.video is video
    assert user.created_at is not None
    assert User.__table__.c.created_at.type.timezone is True
    assert "hashed_password" not in UserResponse.model_validate(user).model_dump()


def test_unique_email_constraint_is_enforced(db_session: Session) -> None:
    make_user(db_session)

    with pytest.raises(IntegrityError):
        make_user(db_session)
    db_session.rollback()


def test_user_video_and_analysis_job_repository_crud(db_session: Session) -> None:
    user = make_user(db_session)
    assert get_user_by_id(db_session, user.id) is user
    assert get_user_by_email(db_session, user.email) is user

    video = make_video(db_session, user.id)
    assert get_video_by_id(db_session, user.id, video.id) is video
    assert list_videos(db_session, user.id) == [video]

    job = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))
    assert get_analysis_job_by_id(db_session, job.id) is job
    assert delete_video(db_session, user.id, video.id) is True
    assert get_video_by_id(db_session, user.id, video.id) is None
    assert get_analysis_job_by_id(db_session, job.id) is None
    assert delete_video(db_session, user.id, video.id) is False


def test_video_retrieval_and_deletion_enforce_ownership(db_session: Session) -> None:
    owner = make_user(db_session, "owner@example.com")
    other_user = make_user(db_session, "other@example.org")
    video = make_video(db_session, owner.id)

    assert get_video_by_id(db_session, other_user.id, video.id) is None
    assert delete_video(db_session, other_user.id, video.id) is False
    assert get_video_by_id(db_session, owner.id, video.id) is video


def test_multiple_analysis_jobs_preserve_video_history(db_session: Session) -> None:
    user = make_user(db_session)
    video = make_video(db_session, user.id)

    first = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))
    second = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))

    assert first.id != second.id
    assert {job.id for job in video.analysis_jobs} == {first.id, second.id}


def test_analysis_job_status_transitions_and_progress(db_session: Session) -> None:
    user = make_user(db_session)
    video = make_video(db_session, user.id)
    job = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))

    job = update_analysis_job(
        db_session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.QUEUED),
    )
    assert job is not None
    job = update_analysis_job(
        db_session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.PROCESSING, progress=25),
    )
    assert job is not None
    assert job.started_at is not None
    assert job.progress == 25
    job = update_analysis_job(
        db_session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.COMPLETED),
    )
    assert job is not None
    assert job.progress == 100
    assert job.completed_at is not None

    with pytest.raises(ValueError, match="Invalid analysis job transition"):
        update_analysis_job(
            db_session,
            job.id,
            AnalysisJobStatusUpdate(status=AnalysisJobStatus.PROCESSING),
        )


def test_analysis_job_can_fail_with_an_error(db_session: Session) -> None:
    user = make_user(db_session)
    video = make_video(db_session, user.id)
    job = create_analysis_job(db_session, AnalysisJobCreate(video_id=video.id))
    update_analysis_job(
        db_session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.QUEUED),
    )
    update_analysis_job(
        db_session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.PROCESSING),
    )

    failed = update_analysis_job(
        db_session,
        job.id,
        AnalysisJobStatusUpdate(
            status=AnalysisJobStatus.FAILED,
            error_message="Analysis could not be completed",
        ),
    )

    assert failed is not None
    assert failed.status == AnalysisJobStatus.FAILED
    assert failed.error_message == "Analysis could not be completed"
    assert failed.completed_at is not None


@pytest.mark.parametrize("progress", [-1, 101])
def test_analysis_job_schema_rejects_invalid_progress(progress: int) -> None:
    with pytest.raises(ValidationError):
        AnalysisJobStatusUpdate(progress=progress)


def test_database_constraints_reject_invalid_progress(db_session: Session) -> None:
    user = make_user(db_session)
    video = make_video(db_session, user.id)
    db_session.add(
        AnalysisJob(
            video_id=video.id,
            progress=101,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
