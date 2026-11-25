from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from celery import Celery
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AnalysisJob, AnalysisJobStatus, User, Video, VideoStatus
from app.repositories.analysis_jobs import create_analysis_job, get_analysis_job_by_id
from app.repositories.users import create_user
from app.repositories.videos import create_video
from app.schemas import AnalysisJobCreate, UserCreate, VideoCreate
from app.services import analysis_queue
from app.workers import database as worker_database
from app.workers import tasks as worker_tasks
from app.workers.celery_app import celery_app


def _queued_video_job(session: Session, storage_key: str) -> tuple[User, Video, AnalysisJob]:
    user = create_user(
        session,
        UserCreate(email=f"{uuid4().hex}@example.com", password="worker-test-password"),
        hashed_password="unit-test-hash",
    )
    video = create_video(
        session,
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
    job = create_analysis_job(session, AnalysisJobCreate(video_id=video.id))
    job.status = AnalysisJobStatus.QUEUED
    session.commit()
    session.refresh(job)
    return user, video, job


@contextmanager
def _use_session(session: Session):
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def test_celery_app_configuration_and_task_registration() -> None:
    assert isinstance(celery_app, Celery)
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.enable_utc is True
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.task_track_started is True
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend
    assert worker_tasks.process_analysis_job.name in celery_app.tasks
    assert worker_tasks.worker_healthcheck.name in celery_app.tasks


def test_worker_healthcheck_task_returns_operational_status() -> None:
    result = worker_tasks.worker_healthcheck.apply().get()

    assert result == {"status": "ok"}


def test_worker_session_closes_on_success_and_rolls_back_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        committed = False
        rolled_back = False
        closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    success_session = FakeSession()
    monkeypatch.setattr(worker_database, "SessionLocal", lambda: success_session)
    with worker_database.worker_session() as session:
        assert session is success_session
    assert success_session.committed
    assert success_session.closed

    failed_session = FakeSession()
    monkeypatch.setattr(worker_database, "SessionLocal", lambda: failed_session)
    with pytest.raises(RuntimeError, match="task failed"):
        with worker_database.worker_session():
            raise RuntimeError("task failed")
    assert failed_session.rolled_back
    assert failed_session.closed


def test_analysis_task_marks_missing_job_without_side_effects(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    result = worker_tasks.process_analysis_job.apply(
        args=[str(uuid4())],
        throw=True,
    ).get()

    assert result == {
        "status": "missing",
        "message": "Analysis job was not found",
    }


def test_analysis_task_marks_missing_video_failed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, video, job = _queued_video_job(db_session, "missing-video.mp4")
    original_get = db_session.get

    def get_model(model, identity, **kwargs):
        if model is Video:
            return None
        return original_get(model, identity, **kwargs)

    monkeypatch.setattr(db_session, "get", get_model)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    refreshed_job = get_analysis_job_by_id(db_session, job.id)
    assert result["status"] == "failed"
    assert result["message"] == "Video record was not found"
    assert refreshed_job is not None
    assert refreshed_job.status == AnalysisJobStatus.FAILED


def test_analysis_task_marks_missing_media_failed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "missing-media.mp4")
    monkeypatch.setattr(settings, "storage_local_path", tmp_path)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    refreshed_job = get_analysis_job_by_id(db_session, job.id)
    assert result["status"] == "failed"
    assert result["message"] == "Video media file is unavailable"
    assert refreshed_job is not None
    assert refreshed_job.status == AnalysisJobStatus.FAILED


def test_analysis_task_pipeline_boundary_and_duplicate_delivery(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "available.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    (storage_root / "available.mp4").write_bytes(b"media")
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    first = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()
    second = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    refreshed_job = get_analysis_job_by_id(db_session, job.id)
    assert first["status"] == "failed"
    assert first["message"] == "Multimodal video analysis is not implemented yet"
    assert second == {"status": "ignored", "job_status": "failed"}
    assert refreshed_job is not None
    assert refreshed_job.status == AnalysisJobStatus.FAILED
    assert refreshed_job.progress == 0
    assert refreshed_job.started_at is not None
    assert refreshed_job.completed_at is not None


def test_analysis_enqueue_dispatches_only_after_queued_commit(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_user(
        db_session,
        UserCreate(email="queue-commit@example.com", password="worker-test-password"),
        hashed_password="unit-test-hash",
    )
    video = create_video(
        db_session,
        user.id,
        VideoCreate(
            original_filename="queue.mp4",
            storage_key="queue.mp4",
            file_size=100,
            target_platform="test",
            content_category="test",
        ),
    )
    dispatched_ids: list[UUID] = []

    def dispatch(job_id: UUID) -> None:
        persisted_job = db_session.scalar(
            select(AnalysisJob).where(AnalysisJob.id == job_id)
        )
        assert persisted_job is not None
        assert persisted_job.status == AnalysisJobStatus.QUEUED
        dispatched_ids.append(job_id)

    monkeypatch.setattr(analysis_queue, "dispatch_analysis_job", dispatch)
    job = analysis_queue.enqueue_analysis_job(db_session, video.id)

    assert job.status == AnalysisJobStatus.QUEUED
    assert dispatched_ids == [job.id]


def test_analysis_dispatch_failure_is_persisted_and_reported(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_user(
        db_session,
        UserCreate(email="queue-failure@example.com", password="worker-test-password"),
        hashed_password="unit-test-hash",
    )
    video = create_video(
        db_session,
        user.id,
        VideoCreate(
            original_filename="queue-failure.mp4",
            storage_key="queue-failure.mp4",
            file_size=100,
            target_platform="test",
            content_category="test",
        ),
    )

    def fail_dispatch(job_id: UUID) -> None:
        raise KombuOperationalError("broker unavailable")

    monkeypatch.setattr(analysis_queue, "dispatch_analysis_job", fail_dispatch)

    with pytest.raises(analysis_queue.AnalysisDispatchError) as error:
        analysis_queue.enqueue_analysis_job(db_session, video.id)

    job = get_analysis_job_by_id(db_session, error.value.job_id)
    assert job is not None
    assert job.status == AnalysisJobStatus.FAILED
    assert job.error_message == "Analysis task could not be dispatched"
    assert "broker unavailable" not in job.error_message
