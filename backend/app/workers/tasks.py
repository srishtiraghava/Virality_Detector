import logging
from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from celery import Task
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.core.logging_config import configure_logging
from app.models.analysis_job import AnalysisJob
from app.models.enums import AnalysisJobStatus, VideoStatus
from app.models.user import User
from app.models.video import Video
from app.repositories.analysis_jobs import apply_analysis_job_update
from app.schemas.analysis_job import AnalysisJobStatusUpdate
from app.services.upload.storage import (
    StorageConfigurationError,
    get_storage_backend,
)
from app.services.video.analysis_pipeline import (
    run_analysis_pipeline,
)
from app.services.video.processing_errors import VideoProcessingError
from app.workers.celery_app import celery_app
from app.workers.database import worker_session

_MISSING_JOB = "Analysis job was not found"
_MISSING_VIDEO = "Video record was not found"
_MISSING_OWNER = "Video owner was not found"
_MISSING_MEDIA = "Video media file is unavailable"
_INVALID_STORAGE_KEY = "Video storage reference is invalid"
configure_logging()
logger = logging.getLogger("viralscope")


def _advisory_lock_key(job_id: UUID) -> tuple[int, int]:
    return (
        int.from_bytes(job_id.bytes[:4], byteorder="big", signed=True),
        int.from_bytes(job_id.bytes[4:8], byteorder="big", signed=True),
    )


@contextmanager
def _job_processing_lock(session: Session, job_id: UUID) -> Generator[bool, None, None]:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield True
        return

    owns_connection = isinstance(bind, Engine)
    # Keep the PostgreSQL session lock pinned while job progress commits.
    lock_connection: Connection = bind.connect() if owns_connection else bind
    lock_key = _advisory_lock_key(job_id)
    acquired = False
    try:
        acquired = bool(
            lock_connection.scalar(select(func.pg_try_advisory_lock(*lock_key)))
        )
        lock_connection.commit()
        yield acquired
    finally:
        try:
            if acquired:
                lock_connection.scalar(select(func.pg_advisory_unlock(*lock_key)))
                lock_connection.commit()
        finally:
            if owns_connection:
                lock_connection.close()


def _fail_job(session: Session, job: AnalysisJob, message: str) -> dict[str, str]:
    apply_analysis_job_update(
        job,
        AnalysisJobStatusUpdate(
            status=AnalysisJobStatus.FAILED,
            error_message=message,
        ),
    )
    session.commit()
    return {"status": AnalysisJobStatus.FAILED.value, "message": message}


@celery_app.task(
    bind=True,
    name="app.workers.tasks.process_analysis_job",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def process_analysis_job(task: Task, analysis_job_id: str) -> dict[str, str]:
    try:
        job_id = UUID(analysis_job_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("analysis_job_id must be a UUID") from exc

    with worker_session() as session:
        with _job_processing_lock(session, job_id) as lock_acquired:
            if not lock_acquired:
                return {
                    "status": "ignored",
                    "message": "Analysis job is already being processed",
                }
            return _execute_analysis_job(session, task, job_id)


def _execute_analysis_job(
    session: Session,
    task: Task,
    job_id: UUID,
) -> dict[str, str]:
    job = session.scalar(
        select(AnalysisJob)
        .where(AnalysisJob.id == job_id)
        .with_for_update()
    )
    if job is None:
        return {"status": "missing", "message": _MISSING_JOB}
    retrying = task.request.retries > 0
    if job.status != AnalysisJobStatus.QUEUED and not (
        retrying and job.status == AnalysisJobStatus.PROCESSING
    ):
        return {"status": "ignored", "job_status": job.status.value}

    video = session.get(Video, job.video_id)
    if video is None:
        return _fail_job(session, job, _MISSING_VIDEO)
    if video.status != VideoStatus.AVAILABLE:
        return _fail_job(session, job, "Video is not available for analysis")
    if video.user_id is None or session.get(User, video.user_id) is None:
        return _fail_job(session, job, _MISSING_OWNER)

    try:
        storage = get_storage_backend()
        media_path = storage.path_for_key(video.storage_key)
    except (StorageConfigurationError, ValueError):
        return _fail_job(session, job, _INVALID_STORAGE_KEY)
    if not media_path.is_file():
        return _fail_job(session, job, _MISSING_MEDIA)

    if job.status == AnalysisJobStatus.QUEUED:
        apply_analysis_job_update(
            job,
            AnalysisJobStatusUpdate(
                status=AnalysisJobStatus.PROCESSING,
                progress=5,
            ),
        )
    session.commit()

    def update_progress(progress: int) -> None:
        if progress <= job.progress:
            return
        apply_analysis_job_update(
            job,
            AnalysisJobStatusUpdate(progress=progress),
        )
        session.commit()

    try:
        artifact = run_analysis_pipeline(
            job,
            video,
            media_path,
            storage,
            update_progress,
        )
    except VideoProcessingError as exc:
        return _fail_job(session, job, str(exc))
    except OperationalError:
        raise
    except Exception as exc:
        logger.error(
            "Analysis pipeline failed",
            extra={"exception_type": type(exc).__name__},
        )
        return _fail_job(session, job, "Analysis could not be completed")

    apply_analysis_job_update(
        job,
        AnalysisJobStatusUpdate(
            status=AnalysisJobStatus.COMPLETED,
            progress=100,
        ),
    )
    session.commit()
    return {
        "status": AnalysisJobStatus.COMPLETED.value,
        "frame_count": str(artifact.summary.frame_count),
        "scene_count": str(artifact.summary.scene_count),
    }


@celery_app.task(name="app.workers.tasks.healthcheck")
def worker_healthcheck() -> dict[str, str]:
    return {"status": "ok"}
