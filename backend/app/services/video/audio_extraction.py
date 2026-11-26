import json
import math
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from app.schemas.video_analysis import AudioExtractionMetadata
from app.services.upload.storage import StorageBackend, StorageError
from app.services.video.processing_errors import AudioExtractionError

_AUDIO_SAMPLE_RATE = 16000
_AUDIO_CHANNELS = 1


@dataclass(frozen=True)
class AudioAsset:
    metadata: AudioExtractionMetadata
    local_path: Path | None


def extract_audio(
    media_path: Path,
    storage: StorageBackend,
    storage_key: str,
    *,
    has_audio: bool | None,
    video_duration_seconds: float | None,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    probe_timeout_seconds: float,
    extraction_timeout_seconds: float,
    max_duration_seconds: float,
) -> AudioAsset:
    if not media_path.is_file():
        raise AudioExtractionError("Video file is unavailable")

    probe_duration: float | None = None
    if has_audio is None or video_duration_seconds is None:
        has_audio, probe_duration = _inspect_audio_stream(
            media_path,
            ffprobe_binary=ffprobe_binary,
            timeout_seconds=probe_timeout_seconds,
        )
    if not has_audio:
        return AudioAsset(
            metadata=AudioExtractionMetadata(
                status="skipped",
                reason="no_audio_stream",
            ),
            local_path=None,
        )

    durations = [
        duration
        for duration in (video_duration_seconds, probe_duration)
        if duration is not None and math.isfinite(duration) and duration > 0
    ]
    if not durations:
        raise AudioExtractionError("Video duration could not be determined")
    if max(durations) > max_duration_seconds:
        raise AudioExtractionError(
            "Video exceeds the configured audio processing duration limit"
        )

    try:
        with tempfile.TemporaryDirectory(prefix="viralscope-audio-") as directory:
            temporary_audio_path = Path(directory) / "audio.wav"
            _convert_audio(
                media_path,
                temporary_audio_path,
                ffmpeg_binary=ffmpeg_binary,
                timeout_seconds=extraction_timeout_seconds,
            )
            duration_seconds, sample_rate, channels = _read_audio_metadata(
                temporary_audio_path
            )
            if duration_seconds > max_duration_seconds:
                raise AudioExtractionError(
                    "Extracted audio exceeds the configured duration limit"
                )
            try:
                storage.save(temporary_audio_path, storage_key)
                persistent_audio_path = storage.path_for_key(storage_key)
            except (StorageError, ValueError) as exc:
                raise AudioExtractionError("Extracted audio could not be stored") from exc

        return AudioAsset(
            metadata=AudioExtractionMetadata(
                status="extracted",
                duration_seconds=duration_seconds,
                sample_rate=sample_rate,
                channels=channels,
                storage_path=storage_key,
            ),
            local_path=persistent_audio_path,
        )
    except AudioExtractionError:
        raise
    except OSError as exc:
        raise AudioExtractionError("Audio extraction could not be completed") from exc


def _inspect_audio_stream(
    media_path: Path,
    *,
    ffprobe_binary: str,
    timeout_seconds: float,
) -> tuple[bool, float | None]:
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(media_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise AudioExtractionError("Audio inspection tools are unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioExtractionError("Audio stream inspection timed out") from exc
    except OSError as exc:
        raise AudioExtractionError("Audio stream inspection could not be completed") from exc

    if result.returncode != 0:
        raise AudioExtractionError("Audio stream could not be inspected")
    try:
        result_data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AudioExtractionError("Audio stream metadata could not be read") from exc
    if not isinstance(result_data, dict):
        raise AudioExtractionError("Audio stream metadata could not be read")

    streams = result_data.get("streams")
    if not isinstance(streams, list):
        raise AudioExtractionError("Audio stream metadata could not be read")
    audio_found = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    )
    media_format = result_data.get("format")
    raw_duration = media_format.get("duration") if isinstance(media_format, dict) else None
    duration = _positive_float(raw_duration)
    return audio_found, duration


def _convert_audio(
    media_path: Path,
    output_path: Path,
    *,
    ffmpeg_binary: str,
    timeout_seconds: float,
) -> None:
    command = [
        ffmpeg_binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(media_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        str(_AUDIO_CHANNELS),
        "-ar",
        str(_AUDIO_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "-y",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise AudioExtractionError("Audio extraction tools are unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioExtractionError("Audio extraction timed out") from exc
    except OSError as exc:
        raise AudioExtractionError("Audio extraction could not be completed") from exc

    if result.returncode != 0:
        raise AudioExtractionError("Audio stream could not be decoded")


def _read_audio_metadata(audio_path: Path) -> tuple[float, int, int]:
    try:
        with wave.open(str(audio_path), "rb") as audio_file:
            channels = audio_file.getnchannels()
            sample_rate = audio_file.getframerate()
            frame_count = audio_file.getnframes()
            sample_width = audio_file.getsampwidth()
            compression = audio_file.getcomptype()
    except (OSError, EOFError, wave.Error) as exc:
        raise AudioExtractionError("Extracted audio is invalid") from exc

    if (
        channels != _AUDIO_CHANNELS
        or sample_rate != _AUDIO_SAMPLE_RATE
        or sample_width != 2
        or compression != "NONE"
        or frame_count <= 0
    ):
        raise AudioExtractionError("Extracted audio has unsupported format metadata")
    return frame_count / sample_rate, sample_rate, channels


def _positive_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None
