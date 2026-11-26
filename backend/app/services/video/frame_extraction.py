import math
import os
from pathlib import Path

import cv2

from app.schemas.video_analysis import ExtractedFrameMetadata
from app.services.video.processing_errors import FrameExtractionError


def extract_video_frames(
    media_path: Path,
    output_directory: Path,
    storage_key_prefix: str,
    *,
    sampling_interval_seconds: float,
    max_frame_count: int,
    max_width: int,
    max_height: int,
) -> list[ExtractedFrameMetadata]:
    if not media_path.is_file():
        raise FrameExtractionError("Video file is unavailable")

    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FrameExtractionError("Frame output could not be stored") from exc

    capture = cv2.VideoCapture(str(media_path))
    temporary_paths: list[Path] = []
    frames: list[ExtractedFrameMetadata] = []
    try:
        if not capture.isOpened():
            raise FrameExtractionError(
                "Video could not be opened; its codec may be unsupported or the file corrupt"
            )

        fps = float(capture.get(cv2.CAP_PROP_FPS))
        next_sample_time = 0.0
        previous_timestamp = -1.0
        frame_index = 0

        while len(frames) < max_frame_count:
            succeeded, frame = capture.read()
            if not succeeded:
                break
            if (
                frame is None
                or getattr(frame, "ndim", 0) != 3
                or frame.shape[0] <= 0
                or frame.shape[1] <= 0
                or frame.shape[2] < 3
            ):
                frame_index += 1
                continue

            timestamp = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
            if (
                not math.isfinite(timestamp)
                or timestamp < 0
                or timestamp <= previous_timestamp
            ):
                if not math.isfinite(fps) or fps <= 0:
                    raise FrameExtractionError(
                        "Video timing information could not be determined"
                    )
                timestamp = frame_index / fps
            previous_timestamp = timestamp

            if timestamp + 1e-9 >= next_sample_time:
                height, width = frame.shape[:2]
                scale = min(max_width / width, max_height / height, 1.0)
                if scale < 1:
                    frame = cv2.resize(
                        frame,
                        (
                            max(1, round(width * scale)),
                            max(1, round(height * scale)),
                        ),
                        interpolation=cv2.INTER_AREA,
                    )

                frame_number = len(frames) + 1
                filename = f"frame_{frame_number:06d}.jpg"
                output_path = output_directory / filename
                temporary_path = output_directory / f".{filename}.part.jpg"
                temporary_paths.append(temporary_path)
                if not cv2.imwrite(
                    str(temporary_path),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 90],
                ):
                    raise FrameExtractionError("A video frame could not be stored")

                decoded_frame = cv2.imread(str(temporary_path), cv2.IMREAD_COLOR)
                if (
                    decoded_frame is None
                    or decoded_frame.ndim != 3
                    or decoded_frame.shape[0] <= 0
                    or decoded_frame.shape[1] <= 0
                ):
                    raise FrameExtractionError("A stored video frame could not be decoded")
                os.replace(temporary_path, output_path)
                temporary_paths.remove(temporary_path)

                frames.append(
                    ExtractedFrameMetadata(
                        timestamp=timestamp,
                        storage_path=f"{storage_key_prefix}/{filename}",
                        width=int(decoded_frame.shape[1]),
                        height=int(decoded_frame.shape[0]),
                    )
                )
                while next_sample_time <= timestamp + 1e-9:
                    next_sample_time += sampling_interval_seconds

            frame_index += 1

        if not frames:
            raise FrameExtractionError("No decodable video frames were found")
        return frames
    except cv2.error as exc:
        raise FrameExtractionError(
            "Video frames could not be decoded or stored"
        ) from exc
    except OSError as exc:
        raise FrameExtractionError("Frame output could not be stored") from exc
    finally:
        capture.release()
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
