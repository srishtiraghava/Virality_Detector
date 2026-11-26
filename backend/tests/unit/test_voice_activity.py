import array
import math
import os
import sys
import wave
from pathlib import Path

import pytest

from app.core.config import settings
from app.schemas.video_analysis import (
    AudioExtractionMetadata,
    ProcessingSummary,
    TranscriptionMetadata,
    VADConfigurationMetadata,
    VideoAnalysisArtifact,
)
from app.services.video import voice_activity_detection
from app.services.video.processing_errors import VoiceActivityDetectionError
from app.services.video.voice_activity_detection import analyze_voice_activity


def _write_generated_audio(
    path: Path,
    sections: list[tuple[float, bool]],
) -> Path:
    sample_rate = 16000
    samples = array.array("h")
    sample_index = 0
    for duration, voiced in sections:
        section_samples = int(duration * sample_rate)
        for offset in range(section_samples):
            time_seconds = (sample_index + offset) / sample_rate
            value = 0.0
            if voiced:
                value = (
                    0.20 * math.sin(2 * math.pi * 140 * time_seconds)
                    + 0.12 * math.sin(2 * math.pi * 280 * time_seconds)
                    + 0.06 * math.sin(2 * math.pi * 420 * time_seconds)
                )
            samples.append(int(max(-1.0, min(1.0, value)) * 32767))
        sample_index += section_samples

    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(sample_rate)
        audio_file.writeframes(samples.tobytes())
    return path


def _configuration(
    *,
    min_speech_duration_ms: int = 100,
) -> VADConfigurationMetadata:
    return VADConfigurationMetadata(
        threshold=settings.vad_threshold,
        min_speech_duration_ms=min_speech_duration_ms,
        min_silence_duration_ms=settings.vad_min_silence_duration_ms,
        speech_pad_ms=settings.vad_speech_pad_ms,
    )


def _mock_inference(
    monkeypatch: pytest.MonkeyPatch,
    intervals: list[dict[str, float]],
) -> list[tuple[object, object, VADConfigurationMetadata]]:
    calls: list[tuple[object, object, VADConfigurationMetadata]] = []
    model = object()

    def get_model() -> object:
        return model

    def infer(
        waveform: object,
        loaded_model: object,
        configuration: VADConfigurationMetadata,
    ) -> object:
        calls.append((waveform, loaded_model, configuration))
        return intervals

    monkeypatch.setattr(voice_activity_detection, "_get_vad_model", get_model)
    monkeypatch.setattr(
        voice_activity_detection,
        "_infer_speech_timestamps",
        infer,
    )
    return calls


def test_vad_normalizes_multiple_overlapping_speech_intervals_and_silences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = _write_generated_audio(
        tmp_path / "voiced.wav",
        [(1.0, True), (0.3, False), (0.5, True), (0.7, False)],
    )
    calls = _mock_inference(
        monkeypatch,
        [
            {"start": 0.1, "end": 0.55},
            {"start": 0.5, "end": 0.9},
            {"start": 1.2, "end": 1.6},
            {"start": 2.0, "end": 2.4},
        ],
    )

    result = analyze_voice_activity(audio_path, _configuration())

    assert result.status == "analyzed"
    assert len(calls) == 1
    assert calls[0][2].sample_rate == 16000
    assert calls[0][2].device == "cpu"
    assert [
        (segment.start_time, segment.end_time)
        for segment in result.speech_segments
    ] == [(0.1, 0.9), (1.2, 1.6), (2.0, 2.4)]
    assert [
        (interval.start_time, interval.end_time)
        for interval in result.silence_intervals
    ] == [(0.0, 0.1), (0.9, 1.2), (1.6, 2.0), (2.4, 2.5)]
    assert result.statistics is not None
    assert result.statistics.total_audio_duration_seconds == pytest.approx(2.5)
    assert result.statistics.total_speech_duration_seconds == pytest.approx(1.6)
    assert result.statistics.total_silence_duration_seconds == pytest.approx(0.9)
    assert result.statistics.speech_to_audio_ratio == pytest.approx(0.64)
    assert result.statistics.silence_to_audio_ratio == pytest.approx(0.36)
    assert result.statistics.speech_segment_count == 3
    assert result.statistics.average_speech_segment_duration_seconds == pytest.approx(
        1.6 / 3
    )
    assert result.statistics.longest_silence_interval_seconds == pytest.approx(0.4)
    assert result.processing_duration_seconds >= 0


def test_vad_preserves_configured_short_utterances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = _write_generated_audio(tmp_path / "short.wav", [(0.5, True)])
    calls = _mock_inference(monkeypatch, [{"start": 0.2, "end": 0.27}])

    result = analyze_voice_activity(
        audio_path,
        _configuration(min_speech_duration_ms=50),
    )

    assert calls[0][2].min_speech_duration_ms == 50
    assert len(result.speech_segments) == 1
    assert result.speech_segments[0].duration == pytest.approx(0.07)


def test_vad_marks_silent_audio_as_one_silence_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = _write_generated_audio(tmp_path / "silent.wav", [(1.25, False)])
    _mock_inference(monkeypatch, [])

    result = analyze_voice_activity(audio_path, _configuration())

    assert result.status == "analyzed"
    assert result.speech_segments == []
    assert [(item.start_time, item.end_time) for item in result.silence_intervals] == [
        (0.0, 1.25)
    ]
    assert result.statistics is not None
    assert result.statistics.total_speech_duration_seconds == 0
    assert result.statistics.total_silence_duration_seconds == pytest.approx(1.25)
    assert result.statistics.speech_to_audio_ratio == 0
    assert result.statistics.silence_to_audio_ratio == 1


def test_empty_audio_is_skipped_without_loading_the_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = _write_generated_audio(tmp_path / "empty.wav", [])
    monkeypatch.setattr(
        voice_activity_detection,
        "_get_vad_model",
        lambda: pytest.fail("Empty audio must not load the VAD model"),
    )

    result = analyze_voice_activity(audio_path, _configuration())

    assert result.status == "skipped"
    assert result.reason == "empty_audio"
    assert result.statistics is not None
    assert result.statistics.total_audio_duration_seconds == 0
    assert result.statistics.speech_to_audio_ratio is None
    assert result.statistics.silence_to_audio_ratio is None
    assert result.silence_intervals == []


def test_vad_reports_missing_audio(
    tmp_path: Path,
) -> None:
    with pytest.raises(VoiceActivityDetectionError) as error:
        analyze_voice_activity(tmp_path / "missing.wav", _configuration())

    assert error.value.reason_code == "audio_unavailable"


def test_vad_model_loading_failure_is_explicit_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = _write_generated_audio(tmp_path / "voice.wav", [(0.5, True)])

    def fail_model_load() -> object:
        raise RuntimeError("internal model cache path")

    monkeypatch.setattr(voice_activity_detection, "_get_vad_model", fail_model_load)

    with pytest.raises(VoiceActivityDetectionError) as error:
        analyze_voice_activity(audio_path, _configuration())

    assert error.value.reason_code == "vad_model_unavailable"
    assert "internal model cache path" not in str(error.value)


def test_vad_model_is_loaded_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[object] = []
    model = object()

    def load_model() -> object:
        loads.append(model)
        return model

    voice_activity_detection._load_cached_model.cache_clear()
    monkeypatch.setattr(voice_activity_detection, "_load_silero_model", load_model)
    try:
        first = voice_activity_detection._get_vad_model()
        second = voice_activity_detection._get_vad_model()
    finally:
        voice_activity_detection._load_cached_model.cache_clear()

    assert first is model
    assert second is model
    assert len(loads) == 1


def test_vad_clamps_predictions_to_audio_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = _write_generated_audio(tmp_path / "bounded.wav", [(1.0, True)])
    _mock_inference(
        monkeypatch,
        [
            {"start": -0.5, "end": 0.5},
            {"start": 0.8, "end": 1.5},
            {"start": 2.0, "end": 3.0},
        ],
    )

    result = analyze_voice_activity(audio_path, _configuration())

    assert [(item.start_time, item.end_time) for item in result.speech_segments] == [
        (0.0, 0.5),
        (0.8, 1.0),
    ]
    assert all(item.end_time <= 1.0 for item in result.speech_segments)


def test_v2_artifact_accepts_vad_metadata_and_legacy_v2_without_it() -> None:
    legacy = VideoAnalysisArtifact.model_validate(
        {
            "schema_version": 2,
            "frames": [],
            "scenes": [],
            "summary": {
                "frame_count": 0,
                "scene_count": 0,
                "processing_duration_seconds": 0,
            },
            "audio": {
                "status": "skipped",
                "reason": "no_audio_stream",
            },
            "transcription": {
                "status": "skipped",
                "reason": "no_audio_stream",
                "text": "",
                "segments": [],
                "processing_duration_seconds": 0,
            },
        }
    )
    assert legacy.vad is None

    current = legacy.model_dump()
    current["vad"] = {
        "status": "skipped",
        "reason": "no_audio_stream",
        "configuration": _configuration(),
        "processing_duration_seconds": 0,
    }
    validated = VideoAnalysisArtifact.model_validate(current)

    assert validated.vad is not None
    assert validated.vad.status == "skipped"
    assert validated.audio is not None
    assert validated.transcription is not None
    assert isinstance(validated.audio, AudioExtractionMetadata)
    assert isinstance(validated.transcription, TranscriptionMetadata)
    assert validated.summary == ProcessingSummary(
        frame_count=0,
        scene_count=0,
        processing_duration_seconds=0,
    )


@pytest.mark.skipif(
    os.environ.get("VIRALSCOPE_REAL_VAD_SMOKE") != "1",
    reason="Set VIRALSCOPE_REAL_VAD_SMOKE=1 to run the real Silero model smoke test",
)
def test_real_silero_model_smoke_on_generated_audio(tmp_path: Path) -> None:
    audio_path = _write_generated_audio(
        tmp_path / "real-vad-smoke.wav",
        [(0.5, False), (0.5, True), (0.5, False)],
    )

    result = analyze_voice_activity(audio_path, _configuration())

    assert result.status == "analyzed"
    assert result.configuration.model_name == "silero-vad"
    assert result.configuration.model_version == "6.2.0"
    assert result.statistics is not None
    assert result.statistics.total_audio_duration_seconds == pytest.approx(1.5)
