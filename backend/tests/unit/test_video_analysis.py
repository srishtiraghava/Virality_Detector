import shutil
import subprocess
from pathlib import Path

import cv2
import pytest
from pydantic import ValidationError
from scenedetect.backends.opencv import VideoStreamCv2

from app.schemas.video_analysis import (
    ExtractedFrameMetadata,
    SceneMetadata,
    VideoAnalysisArtifact,
)
from app.services.video import scene_detection
from app.services.video.frame_extraction import extract_video_frames
from app.services.video.processing_errors import FrameExtractionError
from app.services.video.scene_detection import detect_video_scenes


def _extract(
    video_path: Path,
    output_directory: Path,
    *,
    interval: float = 0.25,
    max_count: int = 20,
    max_width: int = 1280,
    max_height: int = 720,
) -> list[ExtractedFrameMetadata]:
    return extract_video_frames(
        video_path,
        output_directory,
        "frames",
        sampling_interval_seconds=interval,
        max_frame_count=max_count,
        max_width=max_width,
        max_height=max_height,
    )


@pytest.fixture
def two_scene_video_file(tmp_path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("FFmpeg is required to generate test media")

    output_path = tmp_path / "two-scenes.mp4"
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
        "color=c=white:s=64x48:r=24:d=1",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-f",
        "mp4",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, check=False, text=True)
    if result.returncode != 0:
        pytest.fail("FFmpeg could not generate a two-scene video")
    return output_path


def test_frame_extraction_records_timestamps_and_valid_image_paths(
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    frames = _extract(sample_video_file, tmp_path / "frames")

    assert len(frames) >= 3
    assert frames[0].timestamp == pytest.approx(0, abs=0.05)
    assert frames[1].timestamp == pytest.approx(0.25, abs=0.06)
    assert all(frame.storage_path.startswith("frames/frame_") for frame in frames)
    for frame in frames:
        stored_path = tmp_path / frame.storage_path
        decoded = cv2.imread(str(stored_path), cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape[1] == frame.width
        assert decoded.shape[0] == frame.height


def test_frame_extraction_enforces_maximum_frame_count(
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    frames = _extract(
        sample_video_file,
        tmp_path / "frames",
        interval=0.01,
        max_count=2,
    )

    assert len(frames) == 2


def test_frame_extraction_resizes_to_configured_resolution(
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    frames = _extract(
        sample_video_file,
        tmp_path / "frames",
        max_width=32,
        max_height=24,
    )

    assert frames[0].width == 32
    assert frames[0].height == 24


def test_frame_extraction_rejects_corrupt_media(
    tmp_path: Path,
) -> None:
    corrupt_media = tmp_path / "corrupt.mp4"
    corrupt_media.write_bytes(b"not a video")

    with pytest.raises(FrameExtractionError, match="codec|decodable"):
        _extract(corrupt_media, tmp_path / "frames")


def test_frame_extraction_reports_missing_media(
    tmp_path: Path,
) -> None:
    with pytest.raises(FrameExtractionError, match="unavailable"):
        _extract(tmp_path / "missing.mp4", tmp_path / "frames")


def test_frame_extraction_releases_capture_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    sample_video_file: Path,
    tmp_path: Path,
) -> None:
    class FailedCapture:
        released = False

        def isOpened(self) -> bool:
            return True

        def get(self, property_id: int) -> float:
            if property_id == cv2.CAP_PROP_FPS:
                return 24.0
            return 0.0

        def read(self) -> tuple[bool, None]:
            raise RuntimeError("decoder failure")

        def release(self) -> None:
            self.released = True

    capture = FailedCapture()
    def create_capture(path: str) -> FailedCapture:
        assert path == str(sample_video_file)
        return capture

    monkeypatch.setattr(
        "app.services.video.frame_extraction.cv2.VideoCapture",
        create_capture,
    )

    with pytest.raises(RuntimeError, match="decoder failure"):
        _extract(sample_video_file, tmp_path / "frames")

    assert capture.released


def test_scene_detection_finds_multiple_scenes(
    two_scene_video_file: Path,
) -> None:
    scenes = detect_video_scenes(
        two_scene_video_file,
        threshold=20,
        min_scene_len_frames=1,
    )

    assert len(scenes) == 2
    assert scenes[0].start_time == pytest.approx(0, abs=0.05)
    assert scenes[0].end_time == pytest.approx(scenes[1].start_time, abs=0.05)
    assert all(
        scene.duration == pytest.approx(scene.end_time - scene.start_time)
        for scene in scenes
    )


def test_scene_detection_returns_single_continuous_scene(
    sample_video_file: Path,
) -> None:
    scenes = detect_video_scenes(
        sample_video_file,
        threshold=20,
        min_scene_len_frames=1,
    )

    assert len(scenes) == 1
    assert scenes[0].start_time == pytest.approx(0, abs=0.05)
    assert scenes[0].end_time > scenes[0].start_time
    assert scenes[0].duration == pytest.approx(
        scenes[0].end_time - scenes[0].start_time
    )


def test_scene_detection_releases_video_capture(
    monkeypatch: pytest.MonkeyPatch,
    sample_video_file: Path,
) -> None:
    original_open_video = scene_detection.open_video
    opened_streams: list[VideoStreamCv2] = []

    def track_open_video(path: str, *, backend: str) -> VideoStreamCv2:
        stream = original_open_video(path, backend=backend)
        assert isinstance(stream, VideoStreamCv2)
        opened_streams.append(stream)
        return stream

    monkeypatch.setattr(scene_detection, "open_video", track_open_video)
    scenes = scene_detection.detect_video_scenes(
        sample_video_file,
        threshold=20,
        min_scene_len_frames=1,
    )

    assert scenes
    assert len(opened_streams) == 1
    assert not opened_streams[0].capture.isOpened()


def test_video_analysis_artifact_schema_is_versioned_and_validated() -> None:
    artifact = VideoAnalysisArtifact.model_validate(
        {
            "schema_version": 1,
            "frames": [
                {
                    "timestamp": 0.0,
                    "storage_path": "analysis/video/v1/frames/frame_000001.jpg",
                    "width": 64,
                    "height": 48,
                }
            ],
            "scenes": [
                {"start_time": 0.0, "end_time": 1.0, "duration": 1.0}
            ],
            "summary": {
                "frame_count": 1,
                "scene_count": 1,
                "processing_duration_seconds": 0.25,
            },
        }
    )

    assert artifact.summary.frame_count == len(artifact.frames)
    assert artifact.summary.scene_count == len(artifact.scenes)


def test_scene_schema_rejects_inconsistent_time_bounds() -> None:
    with pytest.raises(ValidationError):
        SceneMetadata(start_time=2.0, end_time=1.0, duration=1.0)
