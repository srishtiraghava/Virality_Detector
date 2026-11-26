from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from celery import Celery
from celery.exceptions import Retry
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AnalysisJob, AnalysisJobStatus, User, Video, VideoStatus
from app.repositories.analysis_jobs import create_analysis_job, get_analysis_job_by_id
from app.repositories.users import create_user
from app.repositories.videos import create_video
from app.schemas import AnalysisJobCreate, UserCreate, VideoCreate
from app.schemas.video_analysis import ProcessingSummary, VideoAnalysisArtifact
from app.services import analysis_queue
from app.services.upload.storage import StorageBackend
from app.services.video import (
    analysis_pipeline,
    speech_transcription,
    voice_activity_detection,
)
from app.services.video.processing_errors import (
    TranscriptionError,
    VoiceActivityDetectionError,
)
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


def _mock_no_speech_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWhisperModel:
        def transcribe(
            self,
            audio_path: str,
            **options: object,
        ) -> tuple[Iterator[object], SimpleNamespace]:
            assert Path(audio_path).is_file()
            assert options["vad_filter"] is False
            return iter(()), SimpleNamespace(language="en", language_probability=0.8)

    monkeypatch.setattr(
        speech_transcription,
        "_get_whisper_model",
        lambda: FakeWhisperModel(),
    )


def _mock_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    def infer(
        _waveform: object,
        _model: object,
        _configuration: object,
    ) -> list[dict[str, float]]:
        return [{"start": 0.0, "end": 0.25}]

    monkeypatch.setattr(
        voice_activity_detection,
        "_get_vad_model",
        lambda: object(),
    )
    monkeypatch.setattr(
        voice_activity_detection,
        "_infer_speech_timestamps",
        infer,
    )


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
    _, _, job = _queued_video_job(db_session, "missing-video.mp4")
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


def test_analysis_task_runs_pipeline_and_ignores_duplicate_delivery(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "available.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True)
    (storage_root / "available.mp4").write_bytes(sample_video_file.read_bytes())
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
    _mock_no_speech_whisper(monkeypatch)
    _mock_vad(monkeypatch)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    progress_values: list[int] = []

    def capture_progress(session: Session) -> None:
        persisted_job = session.get(AnalysisJob, job.id)
        assert persisted_job is not None
        progress_values.append(persisted_job.progress)

    event.listen(db_session, "after_commit", capture_progress)
    try:
        first = worker_tasks.process_analysis_job.apply(
            args=[str(job.id)],
            throw=True,
        ).get()
    finally:
        event.remove(
            db_session,
            "after_commit",
            capture_progress,
        )
    second = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    refreshed_job = get_analysis_job_by_id(db_session, job.id)
    assert first["status"] == "completed"
    assert int(first["frame_count"]) > 0
    assert int(first["scene_count"]) > 0
    assert second == {"status": "ignored", "job_status": "completed"}
    assert refreshed_job is not None
    assert refreshed_job.status == AnalysisJobStatus.COMPLETED
    assert refreshed_job.progress == 100
    assert refreshed_job.started_at is not None
    assert refreshed_job.completed_at is not None
    assert progress_values[:8] == [5, 50, 80, 85, 88, 92, 95, 100]
    artifact_path = (
        storage_root
        / "analysis"
        / str(job.video_id)
        / str(job.id)
        / "v2"
        / "metadata.json"
    )
    artifact = VideoAnalysisArtifact.model_validate_json(
        artifact_path.read_text(encoding="utf-8")
    )
    assert artifact.summary.frame_count == len(artifact.frames)
    assert artifact.summary.scene_count == len(artifact.scenes)
    assert artifact.schema_version == 2
    assert artifact.audio is not None
    assert artifact.audio.status == "extracted"
    assert artifact.transcription is not None
    assert artifact.transcription.status == "no_speech"
    assert artifact.vad is not None
    assert artifact.vad.status == "analyzed"
    assert artifact.vad.statistics is not None
    assert artifact.vad.statistics.speech_segment_count == 1
    assert artifact.audio.storage_path is not None
    assert (storage_root / artifact.audio.storage_path).is_file()
    assert all(
        (storage_root / frame.storage_path).is_file()
        for frame in artifact.frames
    )


def test_analysis_task_completes_visual_pipeline_without_audio(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    silent_video_file: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "silent-video.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True)
    (storage_root / "silent-video.mp4").write_bytes(silent_video_file.read_bytes())
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    persisted_job = get_analysis_job_by_id(db_session, job.id)
    artifact_path = (
        storage_root
        / "analysis"
        / str(job.video_id)
        / str(job.id)
        / "v2"
        / "metadata.json"
    )
    artifact = VideoAnalysisArtifact.model_validate_json(
        artifact_path.read_text(encoding="utf-8")
    )
    assert result["status"] == "completed"
    assert persisted_job is not None
    assert persisted_job.status == AnalysisJobStatus.COMPLETED
    assert artifact.audio is not None
    assert artifact.audio.status == "skipped"
    assert artifact.audio.reason == "no_audio_stream"
    assert artifact.transcription is not None
    assert artifact.transcription.status == "skipped"
    assert artifact.transcription.reason == "no_audio_stream"


def test_analysis_task_fails_on_transcription_error_with_checkpointed_progress(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "transcription-failure.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True)
    (storage_root / "transcription-failure.mp4").write_bytes(
        sample_video_file.read_bytes()
    )
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
    _mock_vad(monkeypatch)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )

    def fail_transcription(
        audio_path: Path,
        *,
        language_override: str | None = None,
    ) -> NoReturn:
        assert audio_path.is_file()
        assert language_override is None
        raise TranscriptionError("Speech transcription could not be completed")

    monkeypatch.setattr(analysis_pipeline, "transcribe_audio", fail_transcription)
    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    persisted_job = get_analysis_job_by_id(db_session, job.id)
    assert result == {
        "status": "failed",
        "message": "Speech transcription could not be completed",
    }
    assert persisted_job is not None
    assert persisted_job.status == AnalysisJobStatus.FAILED
    assert persisted_job.progress == 88


def test_vad_failure_is_recorded_but_transcription_completes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "vad-failure.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True)
    (storage_root / "vad-failure.mp4").write_bytes(sample_video_file.read_bytes())
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )
    _mock_no_speech_whisper(monkeypatch)

    def fail_vad(
        audio_path: Path,
        configuration: object,
    ) -> NoReturn:
        assert audio_path.is_file()
        assert configuration is not None
        raise VoiceActivityDetectionError(
            "Silero VAD model could not be loaded",
            reason_code="vad_model_unavailable",
        )

    monkeypatch.setattr(analysis_pipeline, "analyze_voice_activity", fail_vad)

    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        throw=True,
    ).get()

    artifact_path = (
        storage_root
        / "analysis"
        / str(job.video_id)
        / str(job.id)
        / "v2"
        / "metadata.json"
    )
    artifact = VideoAnalysisArtifact.model_validate_json(
        artifact_path.read_text(encoding="utf-8")
    )
    persisted_job = get_analysis_job_by_id(db_session, job.id)
    assert result["status"] == "completed"
    assert persisted_job is not None
    assert persisted_job.status == AnalysisJobStatus.COMPLETED
    assert artifact.vad is not None
    assert artifact.vad.status == "unavailable"
    assert artifact.vad.reason == "vad_model_unavailable"
    assert artifact.transcription is not None
    assert artifact.transcription.status == "no_speech"


def test_analysis_task_records_sanitized_media_failure_and_progress(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "corrupt.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    (storage_root / "corrupt.mp4").write_bytes(b"not video media")
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
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
    assert "codec" in result["message"] or "decodable" in result["message"]
    assert refreshed_job is not None
    assert refreshed_job.status == AnalysisJobStatus.FAILED
    assert 0 < refreshed_job.progress < 100


def test_celery_operational_retry_resumes_processing_job(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    _, _, job = _queued_video_job(db_session, "retry.mp4")
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    (storage_root / "retry.mp4").write_bytes(sample_video_file.read_bytes())
    monkeypatch.setattr(settings, "storage_local_path", storage_root)
    monkeypatch.setattr(
        worker_tasks,
        "worker_session",
        lambda: _use_session(db_session),
    )
    pipeline_attempts = 0

    def fail_once_then_finish(
        analysis_job: AnalysisJob,
        video: Video,
        media_path: Path,
        storage: StorageBackend,
        progress_callback: Callable[[int], None],
    ) -> VideoAnalysisArtifact:
        nonlocal pipeline_attempts
        assert analysis_job.id == job.id
        assert video.id == job.video_id
        assert media_path.is_file()
        assert storage.path_for_key("retry.mp4") == media_path
        pipeline_attempts += 1
        if pipeline_attempts == 1:
            progress_callback(50)
            raise OperationalError("test query", {}, RuntimeError("temporary"))
        return VideoAnalysisArtifact(
            schema_version=1,
            frames=[],
            scenes=[],
            summary=ProcessingSummary(
                frame_count=0,
                scene_count=0,
                processing_duration_seconds=0,
            ),
        )

    monkeypatch.setattr(
        worker_tasks,
        "run_analysis_pipeline",
        fail_once_then_finish,
    )

    with pytest.raises(Retry):
        worker_tasks.process_analysis_job.apply(
            args=[str(job.id)],
            throw=True,
        ).get()
    result = worker_tasks.process_analysis_job.apply(
        args=[str(job.id)],
        retries=1,
        throw=True,
    ).get()

    persisted_job = get_analysis_job_by_id(db_session, job.id)
    assert pipeline_attempts == 2
    assert result["status"] == "completed"
    assert persisted_job is not None
    assert persisted_job.status == AnalysisJobStatus.COMPLETED


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
        assert isinstance(job_id, UUID)
        raise KombuOperationalError("broker unavailable")

    monkeypatch.setattr(analysis_queue, "dispatch_analysis_job", fail_dispatch)

    with pytest.raises(analysis_queue.AnalysisDispatchError) as error:
        analysis_queue.enqueue_analysis_job(db_session, video.id)

    job = get_analysis_job_by_id(db_session, error.value.job_id)
    assert job is not None
    assert job.status == AnalysisJobStatus.FAILED
    assert job.error_message == "Analysis task could not be dispatched"
    assert "broker unavailable" not in job.error_message
