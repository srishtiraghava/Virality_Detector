from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video
from scenedetect.backends.opencv import VideoStreamCv2
from scenedetect.video_stream import VideoOpenFailure

from app.schemas.video_analysis import SceneMetadata
from app.services.video.processing_errors import SceneDetectionError


def detect_video_scenes(
    media_path: Path,
    *,
    threshold: float,
    min_scene_len_frames: int,
) -> list[SceneMetadata]:
    video: VideoStreamCv2 | None = None
    try:
        stream = open_video(str(media_path), backend="opencv")
        if not isinstance(stream, VideoStreamCv2):
            raise SceneDetectionError("OpenCV video capture is unavailable")
        video = stream
        scene_manager = SceneManager()
        scene_manager.add_detector(
            ContentDetector(
                threshold=threshold,
                min_scene_len=min_scene_len_frames,
            )
        )
        scene_manager.detect_scenes(video, show_progress=False)
        detected_scenes = scene_manager.get_scene_list(start_in_scene=True)
        return [
            SceneMetadata(
                start_time=start.get_seconds(),
                end_time=end.get_seconds(),
                duration=end.get_seconds() - start.get_seconds(),
            )
            for start, end in detected_scenes
            if end.get_seconds() > start.get_seconds()
        ]
    except (OSError, ValueError, RuntimeError, VideoOpenFailure) as exc:
        raise SceneDetectionError("Video scenes could not be detected") from exc
    finally:
        if video is not None:
            video.capture.release()
