import logging
from uuid import UUID

from celery import Task
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

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
    AnalysisPipelineUnavailable,
    run_analysis_pipeline,
)
from app.workers.celery_app import celery_app
from app.workers.database import worker_session

_MISSING_JOB = "Analysis job was not found"
_MISSING_VIDEO = "Video record was not found"
_MISSING_OWNER = "Video owner was not found"
_MISSING_MEDIA = "Video media file is unavailable"
_INVALID_STORAGE_KEY = "Video storage reference is invalid"
configure_logging()
logger = logging.getLogger("viralscope")


def _fail_job(job: AnalysisJob, message: str) -> dict[str, str]:
    apply_analysis_job_update(
        job,
        AnalysisJobStatusUpdate(
            status=AnalysisJobStatus.FAILED,
            error_message=message,
        ),
    )
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
    del task
    try:
        job_id = UUID(analysis_job_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("analysis_job_id must be a UUID") from exc

    with worker_session() as session:
        job = session.scalar(
            select(AnalysisJob)
            .where(AnalysisJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return {"status": "missing", "message": _MISSING_JOB}
        if job.status != AnalysisJobStatus.QUEUED:
            return {"status": "ignored", "job_status": job.status.value}

        video = session.get(Video, job.video_id)
        if video is None:
            return _fail_job(job, _MISSING_VIDEO)
        if video.status != VideoStatus.AVAILABLE:
            return _fail_job(job, "Video is not available for analysis")
        if video.user_id is None or session.get(User, video.user_id) is None:
            return _fail_job(job, _MISSING_OWNER)

        try:
            storage = get_storage_backend()
            media_path = storage.path_for_key(video.storage_key)
        except (StorageConfigurationError, ValueError):
            return _fail_job(job, _INVALID_STORAGE_KEY)
        if not media_path.is_file():
            return _fail_job(job, _MISSING_MEDIA)

        apply_analysis_job_update(
            job,
            AnalysisJobStatusUpdate(
                status=AnalysisJobStatus.PROCESSING,
                progress=0,
            ),
        )
        try:
            run_analysis_pipeline(job, video, media_path)
        except AnalysisPipelineUnavailable as exc:
            return _fail_job(job, str(exc))
        except OperationalError:
            raise
        except Exception as exc:
            logger.error(
                "Analysis pipeline failed",
                extra={"exception_type": type(exc).__name__},
            )
            return _fail_job(job, "Analysis could not be completed")

        raise RuntimeError("Analysis pipeline returned without a terminal result")


@celery_app.task(name="app.workers.tasks.healthcheck")
def worker_healthcheck() -> dict[str, str]:
    return {"status": "ok"}
