import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.models.analysis_job import AnalysisJob
from app.models.video import Video
from app.schemas.video_analysis import ProcessingSummary, VideoAnalysisArtifact
from app.services.upload.storage import StorageBackend, StorageError
from app.services.video.frame_extraction import extract_video_frames
from app.services.video.processing_errors import AnalysisArtifactError
from app.services.video.scene_detection import detect_video_scenes


def run_analysis_pipeline(
    job: AnalysisJob,
    video: Video,
    media_path: Path,
    storage: StorageBackend,
    progress_callback: Callable[[int], None],
) -> VideoAnalysisArtifact:
    started_at = time.perf_counter()
    output_prefix = f"analysis/{video.id}/{job.id}/v1"
    frame_prefix = f"{output_prefix}/frames"
    frame_directory = storage.path_for_key(frame_prefix)

    frames = extract_video_frames(
        media_path,
        frame_directory,
        frame_prefix,
        sampling_interval_seconds=settings.frame_sampling_interval_seconds,
        max_frame_count=settings.frame_max_count,
        max_width=settings.frame_max_width,
        max_height=settings.frame_max_height,
    )
    progress_callback(50)

    scenes = detect_video_scenes(
        media_path,
        threshold=settings.scene_detection_threshold,
        min_scene_len_frames=settings.scene_detection_min_scene_len_frames,
    )
    progress_callback(80)

    artifact = VideoAnalysisArtifact(
        frames=frames,
        scenes=scenes,
        summary=ProcessingSummary(
            frame_count=len(frames),
            scene_count=len(scenes),
            processing_duration_seconds=time.perf_counter() - started_at,
        ),
    )
    artifact_storage_key = f"{output_prefix}/metadata.json"
    temporary_artifact: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as artifact_file:
            artifact_file.write(artifact.model_dump_json(indent=2))
            temporary_artifact = Path(artifact_file.name)
        storage.save(temporary_artifact, artifact_storage_key)
    except (OSError, StorageError) as exc:
        raise AnalysisArtifactError("Analysis metadata could not be stored") from exc
    finally:
        if temporary_artifact is not None:
            temporary_artifact.unlink(missing_ok=True)

    progress_callback(95)
    return artifact
