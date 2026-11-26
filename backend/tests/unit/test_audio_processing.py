import shutil
import subprocess
import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.upload.storage import LocalFilesystemStorage
from app.services.video import audio_extraction, speech_transcription
from app.services.video.audio_extraction import AudioAsset, extract_audio
from app.services.video.processing_errors import AudioExtractionError, TranscriptionError
from app.services.video.speech_transcription import transcribe_audio


def _extract_audio(
    media_path: Path,
    storage_root: Path,
    *,
    has_audio: bool | None = None,
    duration: float | None = None,
    max_duration: float = 600,
) -> AudioAsset:
    return extract_audio(
        media_path,
        LocalFilesystemStorage(storage_root),
        "analysis/test/v2/audio/audio.wav",
        has_audio=has_audio,
        video_duration_seconds=duration,
        ffmpeg_binary=settings.ffmpeg_binary,
        ffprobe_binary=settings.ffprobe_binary,
        probe_timeout_seconds=settings.audio_probe_timeout_seconds,
        extraction_timeout_seconds=settings.audio_extraction_timeout_seconds,
        max_duration_seconds=max_duration,
    )


@pytest.fixture
def silent_audio_video_file(tmp_path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("FFmpeg is required to generate test media")

    output_path = tmp_path / "silent-audio.mp4"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x48:r=24:d=1",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=44100",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-f",
        "mp4",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, check=False, text=True)
    if result.returncode != 0:
        pytest.fail("FFmpeg could not generate a silent-audio video")
    return output_path


class FakeModel:
    def __init__(
        self,
        segments: list[object],
        *,
        language: str | None = "en",
        language_probability: float | None = 0.96,
    ) -> None:
        self.segments = segments
        self.language = language
        self.language_probability = language_probability
        self.transcribe_calls: list[tuple[str, dict[str, object]]] = []

    def transcribe(
        self,
        audio_path: str,
        **options: object,
    ) -> tuple[Iterator[object], SimpleNamespace]:
        self.transcribe_calls.append((audio_path, options))
        return iter(self.segments), SimpleNamespace(
            language=self.language,
            language_probability=self.language_probability,
        )


def _fake_model(
    segments: list[object],
    *,
    language: str | None = "en",
    language_probability: float | None = 0.96,
) -> FakeModel:
    return FakeModel(
        segments,
        language=language,
        language_probability=language_probability,
    )


def test_audio_extraction_converts_and_persists_mono_16khz_wav(
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    asset = _extract_audio(sample_video_file, storage_root)

    assert asset.metadata.status == "extracted"
    assert asset.metadata.storage_path == "analysis/test/v2/audio/audio.wav"
    assert asset.local_path == storage_root / asset.metadata.storage_path
    assert asset.local_path.is_file()
    with wave.open(str(asset.local_path), "rb") as audio_file:
        sample_rate = audio_file.getframerate()
        channels = audio_file.getnchannels()
        frame_count = audio_file.getnframes()
    assert sample_rate == 16000
    assert channels == 1
    assert frame_count > 0
    assert asset.metadata.sample_rate == 16000
    assert asset.metadata.channels == 1
    assert asset.metadata.duration_seconds == pytest.approx(frame_count / 16000, abs=0.01)


def test_audio_extraction_skips_video_without_audio(
    silent_video_file: Path,
    tmp_path: Path,
) -> None:
    asset = _extract_audio(silent_video_file, tmp_path / "storage")

    assert asset.metadata.status == "skipped"
    assert asset.metadata.reason == "no_audio_stream"
    assert asset.metadata.storage_path is None
    assert asset.local_path is None


def test_silent_audio_returns_empty_no_speech_transcript(
    silent_audio_video_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = _extract_audio(silent_audio_video_file, tmp_path / "storage")
    assert audio.local_path is not None
    model = _fake_model(
        [],
        language="en",
        language_probability=0.4,
    )
    monkeypatch.setattr(speech_transcription, "_get_whisper_model", lambda: model)

    transcript = transcribe_audio(audio.local_path)

    assert audio.metadata.status == "extracted"
    assert transcript.status == "no_speech"
    assert transcript.reason == "no_speech_detected"
    assert transcript.text == ""
    assert transcript.segments == []


def test_audio_extraction_rejects_corrupt_media(
    tmp_path: Path,
) -> None:
    corrupt_media = tmp_path / "corrupt.mp4"
    corrupt_media.write_bytes(b"not video media")

    with pytest.raises(AudioExtractionError, match="inspected"):
        _extract_audio(corrupt_media, tmp_path / "storage")


def test_audio_extraction_reports_unsupported_audio_codec(
    sample_video_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run = subprocess.run

    def reject_audio(
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        if command[0] == settings.ffmpeg_binary:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="decoder unavailable",
            )
        return original_run(command, **options)

    monkeypatch.setattr(audio_extraction.subprocess, "run", reject_audio)

    with pytest.raises(AudioExtractionError, match="could not be decoded"):
        _extract_audio(sample_video_file, tmp_path / "storage")


def test_audio_extraction_reports_missing_input(
    tmp_path: Path,
) -> None:
    with pytest.raises(AudioExtractionError, match="unavailable"):
        _extract_audio(tmp_path / "missing.mp4", tmp_path / "storage")


def test_audio_extraction_enforces_media_duration_limit(
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(AudioExtractionError, match="duration limit"):
        _extract_audio(
            sample_video_file,
            tmp_path / "storage",
            has_audio=True,
            duration=601,
            max_duration=600,
        )


def test_audio_extraction_cleans_temporary_files_on_success(
    sample_video_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_temporary_directory = tempfile.TemporaryDirectory
    created_directories: list[Path] = []

    def tracked_temporary_directory(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | Path | None = None,
        ignore_cleanup_errors: bool = False,
    ) -> tempfile.TemporaryDirectory[str]:
        directory = original_temporary_directory(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
        )
        created_directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(
        audio_extraction.tempfile,
        "TemporaryDirectory",
        tracked_temporary_directory,
    )

    _extract_audio(sample_video_file, tmp_path / "storage")

    assert len(created_directories) == 1
    assert not created_directories[0].exists()


def test_audio_extraction_cleans_temporary_files_after_timeout(
    sample_video_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run = subprocess.run
    original_temporary_directory = tempfile.TemporaryDirectory
    created_directories: list[Path] = []

    def timeout_ffmpeg(
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        if command[0] == settings.ffmpeg_binary:
            raise subprocess.TimeoutExpired(
                command,
                timeout=options["timeout"],
            )
        return original_run(command, **options)

    def tracked_temporary_directory(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | Path | None = None,
        ignore_cleanup_errors: bool = False,
    ) -> tempfile.TemporaryDirectory[str]:
        directory = original_temporary_directory(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
        )
        created_directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(audio_extraction.subprocess, "run", timeout_ffmpeg)
    monkeypatch.setattr(
        audio_extraction.tempfile,
        "TemporaryDirectory",
        tracked_temporary_directory,
    )

    with pytest.raises(AudioExtractionError, match="timed out"):
        _extract_audio(sample_video_file, tmp_path / "storage")

    assert len(created_directories) == 1
    assert not created_directories[0].exists()


def test_transcription_records_segments_and_detects_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"mock audio")
    model = _fake_model(
        [
            SimpleNamespace(
                start=0.25,
                end=0.9,
                text=" Hello there ",
                avg_logprob=-0.2,
                no_speech_prob=0.03,
            )
        ]
    )
    monkeypatch.setattr(speech_transcription, "_get_whisper_model", lambda: model)

    transcript = transcribe_audio(audio_path)

    assert transcript.status == "transcribed"
    assert transcript.detected_language == "en"
    assert transcript.language_probability == pytest.approx(0.96)
    assert transcript.text == "Hello there"
    assert len(transcript.segments) == 1
    assert transcript.segments[0].start_time == pytest.approx(0.25)
    assert transcript.segments[0].end_time == pytest.approx(0.9)
    assert transcript.segments[0].average_log_probability == pytest.approx(-0.2)
    assert transcript.segments[0].no_speech_probability == pytest.approx(0.03)
    assert transcript.processing_duration_seconds >= 0
    assert model.transcribe_calls[0][1]["language"] is None
    assert model.transcribe_calls[0][1]["vad_filter"] is False


def test_transcription_passes_configured_language_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"mock audio")
    model = _fake_model(
        [
            SimpleNamespace(
                start=0.0,
                end=0.4,
                text=" Bonjour",
                avg_logprob=-0.1,
                no_speech_prob=0.01,
            )
        ],
        language="fr",
    )
    monkeypatch.setattr(speech_transcription, "_get_whisper_model", lambda: model)

    transcript = transcribe_audio(audio_path, language_override="fr")

    assert transcript.detected_language == "fr"
    assert transcript.language_override == "fr"
    assert model.transcribe_calls[0][1]["language"] == "fr"


def test_whisper_model_loads_lazily_and_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    model_instance = object()

    def create_model(*args: object, **options: object) -> object:
        calls.append((args, options))
        return model_instance

    speech_transcription._load_cached_model.cache_clear()
    monkeypatch.setattr(speech_transcription, "WhisperModel", create_model)
    try:
        first_model = speech_transcription._get_whisper_model()
        second_model = speech_transcription._get_whisper_model()
    finally:
        speech_transcription._load_cached_model.cache_clear()

    assert first_model is model_instance
    assert second_model is model_instance
    assert len(calls) == 1
    assert calls[0][0][0] == settings.whisper_model_size
    assert calls[0][1]["compute_type"] == settings.whisper_compute_type


def test_transcription_reports_model_loading_failure_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"mock audio")

    def fail_model_load() -> None:
        raise RuntimeError("model path or internal details")

    monkeypatch.setattr(speech_transcription, "_get_whisper_model", fail_model_load)

    with pytest.raises(TranscriptionError, match="model could not be loaded") as error:
        transcribe_audio(audio_path)
    assert "internal details" not in str(error.value)


def test_transcription_reports_inference_failure_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"mock audio")

    class FailedModel:
        def transcribe(self, audio_path: str, **options: object) -> None:
            assert Path(audio_path).is_file()
            assert options["vad_filter"] is False
            raise RuntimeError("internal inference detail")

    monkeypatch.setattr(
        speech_transcription,
        "_get_whisper_model",
        lambda: FailedModel(),
    )

    with pytest.raises(TranscriptionError, match="could not be completed") as error:
        transcribe_audio(audio_path)
    assert "internal inference detail" not in str(error.value)
