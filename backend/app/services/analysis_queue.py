from uuid import UUID

from celery.exceptions import CeleryError
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy.orm import Session

from app.models.analysis_job import AnalysisJob
from app.models.enums import AnalysisJobStatus
from app.repositories.analysis_jobs import create_analysis_job, update_analysis_job
from app.schemas.analysis_job import AnalysisJobCreate, AnalysisJobStatusUpdate
from app.workers.tasks import process_analysis_job

_DISPATCH_FAILURE_MESSAGE = "Analysis task could not be dispatched"


class AnalysisDispatchError(RuntimeError):
    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__("Analysis task dispatch failed")


def dispatch_analysis_job(job_id: UUID) -> None:
    process_analysis_job.apply_async(
        args=[str(job_id)],
        task_id=f"analysis-{job_id}",
    )


def enqueue_analysis_job(session: Session, video_id: UUID) -> AnalysisJob:
    job = create_analysis_job(session, AnalysisJobCreate(video_id=video_id))
    queued_job = update_analysis_job(
        session,
        job.id,
        AnalysisJobStatusUpdate(status=AnalysisJobStatus.QUEUED),
    )
    if queued_job is None:
        raise RuntimeError("Newly created analysis job could not be retrieved")

    try:
        dispatch_analysis_job(queued_job.id)
    except (CeleryError, KombuOperationalError):
        failed_job = update_analysis_job(
            session,
            queued_job.id,
            AnalysisJobStatusUpdate(
                status=AnalysisJobStatus.FAILED,
                error_message=_DISPATCH_FAILURE_MESSAGE,
            ),
        )
        if failed_job is None:
            raise RuntimeError("Dispatched analysis job could not be retrieved")
        raise AnalysisDispatchError(queued_job.id) from None
    return queued_job
