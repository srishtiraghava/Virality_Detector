from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_job import AnalysisJob
from app.models.enums import AnalysisJobStatus
from app.models.video import Video
from app.schemas.analysis_job import AnalysisJobCreate, AnalysisJobStatusUpdate

_ALLOWED_TRANSITIONS = {
    AnalysisJobStatus.PENDING: {AnalysisJobStatus.QUEUED},
    AnalysisJobStatus.QUEUED: {
        AnalysisJobStatus.PROCESSING,
        AnalysisJobStatus.FAILED,
    },
    AnalysisJobStatus.PROCESSING: {
        AnalysisJobStatus.COMPLETED,
        AnalysisJobStatus.FAILED,
    },
    AnalysisJobStatus.COMPLETED: set(),
    AnalysisJobStatus.FAILED: set(),
}


def apply_analysis_job_update(
    job: AnalysisJob,
    update: AnalysisJobStatusUpdate,
) -> None:
    new_status = update.status
    if new_status is not None and new_status != job.status:
        if new_status not in _ALLOWED_TRANSITIONS[job.status]:
            raise ValueError(
                f"Invalid analysis job transition: "
                f"{job.status.value} -> {new_status.value}"
            )

    if new_status == AnalysisJobStatus.COMPLETED and update.progress not in (None, 100):
        raise ValueError("A completed analysis job must have progress 100")

    now = datetime.now(UTC)
    if new_status is not None:
        job.status = new_status
        if new_status == AnalysisJobStatus.PROCESSING and job.started_at is None:
            job.started_at = now
        if new_status in (AnalysisJobStatus.COMPLETED, AnalysisJobStatus.FAILED):
            job.completed_at = now
        if new_status == AnalysisJobStatus.COMPLETED:
            job.progress = 100

    if update.progress is not None:
        job.progress = update.progress
    if update.error_message is not None:
        job.error_message = update.error_message


def create_analysis_job(session: Session, job_data: AnalysisJobCreate) -> AnalysisJob:
    job = AnalysisJob(video_id=job_data.video_id)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_analysis_job_by_id(session: Session, job_id: UUID) -> AnalysisJob | None:
    return session.get(AnalysisJob, job_id)


def get_analysis_job_for_user(
    session: Session,
    user_id: UUID,
    job_id: UUID,
) -> AnalysisJob | None:
    statement = (
        select(AnalysisJob)
        .join(Video, AnalysisJob.video_id == Video.id)
        .where(AnalysisJob.id == job_id, Video.user_id == user_id)
    )
    return session.scalar(statement)


def update_analysis_job(
    session: Session,
    job_id: UUID,
    update: AnalysisJobStatusUpdate,
) -> AnalysisJob | None:
    job = session.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
    if job is None:
        return None

    apply_analysis_job_update(job, update)
    session.commit()
    session.refresh(job)
    return job
