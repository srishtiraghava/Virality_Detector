import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class MediaProbeError(ValueError):
    pass


class MediaToolUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoMetadata:
    duration: float
    width: int
    height: int
    fps: float | None
    aspect_ratio: float
    video_codec: str
    has_audio: bool
    format_names: frozenset[str]


def extract_video_metadata(
    file_path: Path,
    *,
    ffprobe_binary: str = "ffprobe",
    timeout_seconds: float = 30,
) -> VideoMetadata:
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate",
        "-of",
        "json",
        str(file_path),
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
        raise MediaToolUnavailable("Media inspection is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaToolUnavailable("Media inspection timed out") from exc
    except OSError as exc:
        raise MediaToolUnavailable("Media inspection is unavailable") from exc

    if result.returncode != 0:
        raise MediaProbeError("Uploaded file is not valid video media")

    try:
        probe_result = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError("Unable to read media metadata") from exc

    if not isinstance(probe_result, dict):
        raise MediaProbeError("Unable to read media metadata")

    media_format = probe_result.get("format")
    streams = probe_result.get("streams")
    if not isinstance(media_format, dict) or not isinstance(streams, list):
        raise MediaProbeError("Uploaded file has incomplete media metadata")

    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise MediaProbeError("Uploaded file does not contain a video stream")

    try:
        duration = float(media_format["duration"])
        width = int(video_stream["width"])
        height = int(video_stream["height"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MediaProbeError("Uploaded video is missing required metadata") from exc

    codec = video_stream.get("codec_name")
    if (
        not math.isfinite(duration)
        or duration <= 0
        or width <= 0
        or height <= 0
        or not isinstance(codec, str)
        or not codec
    ):
        raise MediaProbeError("Uploaded video has invalid required metadata")

    fps: float | None = None
    frame_rate = video_stream.get("avg_frame_rate")
    if isinstance(frame_rate, str):
        try:
            parsed_frame_rate = float(Fraction(frame_rate))
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
        else:
            if math.isfinite(parsed_frame_rate) and parsed_frame_rate > 0:
                fps = parsed_frame_rate

    format_names_value = media_format.get("format_name")
    if not isinstance(format_names_value, str) or not format_names_value:
        raise MediaProbeError("Uploaded file has no recognized container format")

    return VideoMetadata(
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        aspect_ratio=width / height,
        video_codec=codec,
        has_audio=any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio"
            for stream in streams
        ),
        format_names=frozenset(format_names_value.split(",")),
    )
