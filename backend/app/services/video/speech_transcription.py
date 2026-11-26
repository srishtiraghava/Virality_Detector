import math
import threading
import time
from functools import lru_cache
from pathlib import Path

from faster_whisper import WhisperModel

from app.core.config import settings
from app.schemas.video_analysis import (
    TranscriptSegmentMetadata,
    TranscriptionMetadata,
)
from app.services.video.processing_errors import TranscriptionError

_MODEL_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _load_cached_model(
    model_size: str,
    device: str,
    compute_type: str,
    cpu_threads: int,
    download_root: str | None,
) -> WhisperModel:
    return WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        num_workers=1,
        download_root=download_root,
    )


def _get_whisper_model() -> WhisperModel:
    download_root = settings.whisper_download_root
    with _MODEL_LOAD_LOCK:
        return _load_cached_model(
            settings.whisper_model_size,
            settings.whisper_device,
            settings.whisper_compute_type,
            settings.whisper_cpu_threads,
            str(download_root) if download_root is not None else None,
        )


def transcribe_audio(
    audio_path: Path,
    *,
    language_override: str | None = None,
) -> TranscriptionMetadata:
    if not audio_path.is_file():
        raise TranscriptionError("Extracted audio is unavailable for transcription")

    requested_language = language_override or settings.whisper_language
    started_at = time.perf_counter()
    try:
        model = _get_whisper_model()
    except Exception as exc:
        raise TranscriptionError("Speech recognition model could not be loaded") from exc

    try:
        raw_segments, info = model.transcribe(
            str(audio_path),
            language=requested_language,
            beam_size=5,
            vad_filter=False,
        )
        segments = [
            TranscriptSegmentMetadata(
                start_time=float(segment.start),
                end_time=float(segment.end),
                text=segment.text.strip(),
                average_log_probability=_optional_finite_float(
                    getattr(segment, "avg_logprob", None)
                ),
                no_speech_probability=_optional_probability(
                    getattr(segment, "no_speech_prob", None)
                ),
            )
            for segment in raw_segments
            if isinstance(segment.text, str) and segment.text.strip()
        ]
        elapsed = time.perf_counter() - started_at
        detected_language = getattr(info, "language", None)
        if not isinstance(detected_language, str):
            detected_language = None
        language_probability = _optional_probability(
            getattr(info, "language_probability", None)
        )
        transcript_text = " ".join(segment.text for segment in segments)
        return TranscriptionMetadata(
            status="transcribed" if segments else "no_speech",
            reason=None if segments else "no_speech_detected",
            detected_language=detected_language,
            language_probability=language_probability,
            language_override=requested_language,
            text=transcript_text,
            segments=segments,
            processing_duration_seconds=elapsed,
        )
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError("Speech transcription could not be completed") from exc


def _optional_finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_probability(value: object) -> float | None:
    parsed = _optional_finite_float(value)
    if parsed is None or not 0 <= parsed <= 1:
        return None
    return parsed
