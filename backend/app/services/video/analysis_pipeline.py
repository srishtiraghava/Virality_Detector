import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.models.analysis_job import AnalysisJob
from app.models.video import Video
from app.schemas.video_analysis import (
    ProcessingSummary,
    TranscriptionMetadata,
    VideoAnalysisArtifact,
    VoiceActivityAnalysisMetadata,
)
from app.services.upload.storage import StorageBackend, StorageError
from app.services.video.audio_extraction import extract_audio
from app.services.video.frame_extraction import extract_video_frames
from app.services.video.processing_errors import (
    AnalysisArtifactError,
    AudioExtractionError,
    VoiceActivityDetectionError,
)
from app.services.video.scene_detection import detect_video_scenes
from app.services.video.speech_transcription import transcribe_audio
from app.services.video.voice_activity_detection import (
    analyze_voice_activity,
    build_vad_configuration,
)

logger = logging.getLogger(__name__)


def run_analysis_pipeline(
    job: AnalysisJob,
    video: Video,
    media_path: Path,
    storage: StorageBackend,
    progress_callback: Callable[[int], None],
) -> VideoAnalysisArtifact:
    started_at = time.perf_counter()
    output_prefix = f"analysis/{video.id}/{job.id}/v2"
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

    audio_asset = extract_audio(
        media_path,
        storage,
        f"{output_prefix}/audio/audio.wav",
        has_audio=video.has_audio,
        video_duration_seconds=video.duration,
        ffmpeg_binary=settings.ffmpeg_binary,
        ffprobe_binary=settings.ffprobe_binary,
        probe_timeout_seconds=settings.audio_probe_timeout_seconds,
        extraction_timeout_seconds=settings.audio_extraction_timeout_seconds,
        max_duration_seconds=settings.audio_max_duration_seconds,
    )
    progress_callback(85)

    vad_configuration = build_vad_configuration()
    if audio_asset.metadata.status == "extracted":
        if audio_asset.local_path is None:
            raise AudioExtractionError("Extracted audio is unavailable for transcription")
        vad_started_at = time.perf_counter()
        try:
            vad = analyze_voice_activity(
                audio_asset.local_path,
                vad_configuration,
            )
        except VoiceActivityDetectionError as exc:
            logger.warning(
                "Voice activity analysis is unavailable",
                extra={"reason_code": exc.reason_code},
            )
            vad = VoiceActivityAnalysisMetadata(
                status="unavailable",
                reason=exc.reason_code,
                configuration=vad_configuration,
                processing_duration_seconds=time.perf_counter() - vad_started_at,
            )
        progress_callback(88)
        transcription = transcribe_audio(
            audio_asset.local_path,
            language_override=settings.whisper_language,
        )
    else:
        vad = VoiceActivityAnalysisMetadata(
            status="skipped",
            reason="no_audio_stream",
            configuration=vad_configuration,
            processing_duration_seconds=0,
        )
        progress_callback(88)
        transcription = TranscriptionMetadata(
            status="skipped",
            reason="no_audio_stream",
            text="",
            segments=[],
            processing_duration_seconds=0,
        )
    progress_callback(92)

    artifact = VideoAnalysisArtifact(
        schema_version=2,
        frames=frames,
        scenes=scenes,
        audio=audio_asset.metadata,
        transcription=transcription,
        vad=vad,
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
