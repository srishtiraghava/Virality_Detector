from __future__ import annotations

import math
import threading
import time
import wave
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings
from app.schemas.video_analysis import (
    SilenceIntervalMetadata,
    SpeechSegmentMetadata,
    VADConfigurationMetadata,
    VADStatisticsMetadata,
    VoiceActivityAnalysisMetadata,
)
from app.services.video.processing_errors import VoiceActivityDetectionError

if TYPE_CHECKING:
    import torch

_SAMPLE_RATE = 16000
_SAMPLE_WIDTH = 2
_MODEL_LOAD_LOCK = threading.Lock()


def build_vad_configuration() -> VADConfigurationMetadata:
    return VADConfigurationMetadata(
        threshold=settings.vad_threshold,
        min_speech_duration_ms=settings.vad_min_speech_duration_ms,
        min_silence_duration_ms=settings.vad_min_silence_duration_ms,
        speech_pad_ms=settings.vad_speech_pad_ms,
    )


@lru_cache(maxsize=1)
def _load_cached_model() -> object:
    return _load_silero_model()


def _load_silero_model() -> object:
    from silero_vad import load_silero_vad

    return load_silero_vad()


def _get_vad_model() -> object:
    with _MODEL_LOAD_LOCK:
        return _load_cached_model()


def analyze_voice_activity(
    audio_path: Path,
    configuration: VADConfigurationMetadata,
) -> VoiceActivityAnalysisMetadata:
    started_at = time.perf_counter()
    if not audio_path.is_file():
        raise VoiceActivityDetectionError(
            "Extracted audio is unavailable for VAD",
            reason_code="audio_unavailable",
        )

    waveform, duration_seconds = _read_waveform(audio_path)
    if waveform is None:
        return VoiceActivityAnalysisMetadata(
            status="skipped",
            reason="empty_audio",
            configuration=configuration,
            statistics=VADStatisticsMetadata(
                total_audio_duration_seconds=0,
                total_speech_duration_seconds=0,
                total_silence_duration_seconds=0,
                speech_to_audio_ratio=None,
                silence_to_audio_ratio=None,
                speech_segment_count=0,
                average_speech_segment_duration_seconds=None,
                longest_silence_interval_seconds=None,
            ),
            processing_duration_seconds=time.perf_counter() - started_at,
        )

    try:
        model = _get_vad_model()
    except Exception as exc:
        raise VoiceActivityDetectionError(
            "Silero VAD model could not be loaded",
            reason_code="vad_model_unavailable",
        ) from exc

    try:
        raw_segments = _infer_speech_timestamps(
            waveform,
            model,
            configuration,
        )
        speech_segments = _normalize_speech_segments(
            raw_segments,
            duration_seconds,
        )
        silence_intervals = _get_silence_intervals(
            speech_segments,
            duration_seconds,
        )
        statistics = _calculate_statistics(
            duration_seconds,
            speech_segments,
            silence_intervals,
        )
        return VoiceActivityAnalysisMetadata(
            status="analyzed",
            configuration=configuration,
            speech_segments=speech_segments,
            silence_intervals=silence_intervals,
            statistics=statistics,
            processing_duration_seconds=time.perf_counter() - started_at,
        )
    except VoiceActivityDetectionError:
        raise
    except Exception as exc:
        raise VoiceActivityDetectionError(
            "Voice activity analysis could not be completed",
            reason_code="vad_processing_failed",
        ) from exc


def _read_waveform(audio_path: Path) -> tuple[torch.Tensor | None, float]:
    try:
        with wave.open(str(audio_path), "rb") as audio_file:
            channels = audio_file.getnchannels()
            sample_rate = audio_file.getframerate()
            sample_width = audio_file.getsampwidth()
            frame_count = audio_file.getnframes()
            compression = audio_file.getcomptype()
            if (
                channels != 1
                or sample_rate != _SAMPLE_RATE
                or sample_width != _SAMPLE_WIDTH
                or compression != "NONE"
                or frame_count < 0
            ):
                raise VoiceActivityDetectionError(
                    "Extracted audio has an unsupported VAD format",
                    reason_code="invalid_audio_format",
                )
            if frame_count == 0:
                return None, 0.0
            raw_frames = bytearray(audio_file.readframes(frame_count))
    except VoiceActivityDetectionError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise VoiceActivityDetectionError(
            "Extracted audio could not be read for VAD",
            reason_code="invalid_audio",
        ) from exc

    expected_byte_count = frame_count * channels * sample_width
    if len(raw_frames) != expected_byte_count:
        raise VoiceActivityDetectionError(
            "Extracted audio is truncated",
            reason_code="invalid_audio",
        )
    try:
        import torch

        samples = torch.frombuffer(raw_frames, dtype=torch.int16)
        waveform = samples.to(dtype=torch.float32)
        del samples
        del raw_frames
        waveform.mul_(1.0 / 32768.0)
    except Exception as exc:
        raise VoiceActivityDetectionError(
            "VAD audio runtime is unavailable",
            reason_code="vad_runtime_unavailable",
        ) from exc
    return waveform, frame_count / sample_rate


def _infer_speech_timestamps(
    waveform: torch.Tensor,
    model: object,
    configuration: VADConfigurationMetadata,
) -> object:
    from silero_vad import get_speech_timestamps

    return get_speech_timestamps(
        waveform,
        model,
        threshold=configuration.threshold,
        sampling_rate=configuration.sample_rate,
        min_speech_duration_ms=configuration.min_speech_duration_ms,
        min_silence_duration_ms=configuration.min_silence_duration_ms,
        speech_pad_ms=configuration.speech_pad_ms,
        return_seconds=True,
    )


def _normalize_speech_segments(
    raw_segments: object,
    audio_duration: float,
) -> list[SpeechSegmentMetadata]:
    if not isinstance(raw_segments, list):
        raise ValueError("Silero VAD returned an invalid segment list")

    bounded_segments: list[tuple[float, float]] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, Mapping):
            raise ValueError("Silero VAD returned an invalid segment")
        start = _finite_seconds(raw_segment.get("start"))
        end = _finite_seconds(raw_segment.get("end"))
        if start is None or end is None:
            raise ValueError("Silero VAD returned invalid timestamps")
        bounded_start = min(max(start, 0.0), audio_duration)
        bounded_end = min(max(end, 0.0), audio_duration)
        if bounded_end > bounded_start:
            bounded_segments.append((bounded_start, bounded_end))

    bounded_segments.sort()
    merged_segments: list[tuple[float, float]] = []
    for start, end in bounded_segments:
        if merged_segments and start <= merged_segments[-1][1]:
            previous_start, previous_end = merged_segments[-1]
            merged_segments[-1] = (previous_start, max(previous_end, end))
        else:
            merged_segments.append((start, end))

    return [
        SpeechSegmentMetadata(
            start_time=start,
            end_time=end,
            duration=end - start,
        )
        for start, end in merged_segments
    ]


def _get_silence_intervals(
    speech_segments: list[SpeechSegmentMetadata],
    audio_duration: float,
) -> list[SilenceIntervalMetadata]:
    intervals: list[SilenceIntervalMetadata] = []
    cursor = 0.0
    for segment in speech_segments:
        if segment.start_time > cursor:
            intervals.append(
                SilenceIntervalMetadata(
                    start_time=cursor,
                    end_time=segment.start_time,
                    duration=segment.start_time - cursor,
                )
            )
        cursor = segment.end_time
    if cursor < audio_duration:
        intervals.append(
            SilenceIntervalMetadata(
                start_time=cursor,
                end_time=audio_duration,
                duration=audio_duration - cursor,
            )
        )
    return intervals


def _calculate_statistics(
    audio_duration: float,
    speech_segments: list[SpeechSegmentMetadata],
    silence_intervals: list[SilenceIntervalMetadata],
) -> VADStatisticsMetadata:
    speech_duration = sum(segment.duration for segment in speech_segments)
    silence_duration = sum(interval.duration for interval in silence_intervals)
    segment_count = len(speech_segments)
    return VADStatisticsMetadata(
        total_audio_duration_seconds=audio_duration,
        total_speech_duration_seconds=speech_duration,
        total_silence_duration_seconds=silence_duration,
        speech_to_audio_ratio=speech_duration / audio_duration,
        silence_to_audio_ratio=silence_duration / audio_duration,
        speech_segment_count=segment_count,
        average_speech_segment_duration_seconds=(
            speech_duration / segment_count if segment_count else None
        ),
        longest_silence_interval_seconds=max(
            (interval.duration for interval in silence_intervals),
            default=None,
        ),
    )


def _finite_seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) else None
