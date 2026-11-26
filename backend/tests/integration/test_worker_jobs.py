from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
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
from app.services.video import speech_transcription, voice_activity_detection
from app.schemas import (
    AnalysisJobCreate,
    AnalysisJobStatusUpdate,
    UserCreate,
    VideoCreate,
)
from app.schemas.video_analysis import VADConfigurationMetadata
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
    monkeypatch.setattr(
        voice_activity_detection,
        "_get_vad_model",
        lambda: object(),
    )

    def infer_speech(
        _waveform: object,
        _model: object,
        _configuration: VADConfigurationMetadata,
    ) -> list[dict[str, float]]:
        return [{"start": 0.0, "end": 0.25}]

    monkeypatch.setattr(
        voice_activity_detection,
        "_infer_speech_timestamps",
        infer_speech,
    )

    def infer_speech(
        _waveform: object,
        _model: object,
        _configuration: VADConfigurationMetadata,
    ) -> list[dict[str, float]]:
        return [{"start": 0.0, "end": 0.25}]

    monkeypatch.setattr(
        voice_activity_detection,
        "_infer_speech_timestamps",
        infer_speech,
    )

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
        / "v2"
        / "metadata.json"
    ).is_file()
