from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.enums import VideoStatus
from app.models.video import Video
from app.repositories.videos import create_video
from app.schemas.video import VideoCreate
from app.services.upload.storage import StorageBackend
from app.services.video.metadata import MediaProbeError, VideoMetadata, extract_video_metadata

_CHUNK_SIZE = 1024 * 1024
_SUPPORTED_MEDIA_TYPES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    ".mp4": (frozenset({"video/mp4", "application/mp4"}), frozenset({"mov", "mp4"})),
    ".mov": (frozenset({"video/quicktime"}), frozenset({"mov", "mp4"})),
    ".m4v": (frozenset({"video/x-m4v", "video/mp4"}), frozenset({"mov", "mp4"})),
    ".webm": (frozenset({"video/webm"}), frozenset({"matroska", "webm"})),
    ".mkv": (frozenset({"video/x-matroska"}), frozenset({"matroska"})),
}


class UnsupportedVideoError(ValueError):
    pass


class EmptyVideoError(ValueError):
    pass


class VideoTooLargeError(ValueError):
    pass


class InvalidVideoError(ValueError):
    pass


def _validate_upload(
    original_filename: str | None,
    content_type: str | None,
) -> tuple[str, str, frozenset[str]]:
    if not original_filename:
        raise UnsupportedVideoError("A supported video filename is required")

    safe_filename = PureWindowsPath(original_filename).name
    safe_filename = "".join(character for character in safe_filename if character.isprintable())
    safe_filename = safe_filename.strip(" .")
    extension = Path(safe_filename).suffix.casefold()
    media_type, container_names = _SUPPORTED_MEDIA_TYPES.get(
        extension,
        (frozenset(), frozenset()),
    )
    normalized_type = (content_type or "").partition(";")[0].strip().casefold()
    if not media_type or normalized_type not in media_type:
        raise UnsupportedVideoError("Unsupported video extension or MIME type")

    if len(safe_filename) > 255:
        stem = safe_filename[: -len(extension)]
        safe_filename = f"{stem[: 255 - len(extension)]}{extension}"
    return safe_filename, extension, container_names


def upload_video(
    session: Session,
    user_id: UUID,
    source: BinaryIO,
    *,
    original_filename: str | None,
    content_type: str | None,
    target_platform: str,
    content_category: str,
    max_size_bytes: int,
    storage: StorageBackend,
) -> Video:
    safe_filename, extension, accepted_containers = _validate_upload(
        original_filename,
        content_type,
    )
    target_platform = target_platform.strip()
    content_category = content_category.strip()
    if not target_platform or not content_category:
        raise ValueError("Target platform and content category are required")

    if max_size_bytes <= 0:
        raise ValueError("Maximum upload size must be positive")

    temp_path: Path | None = None
    storage_key: str | None = None
    persisted = False

    try:
        with NamedTemporaryFile(
            prefix="viralscope-upload-",
            suffix=extension,
            delete=False,
        ) as staged_file:
            temp_path = Path(staged_file.name)
            source.seek(0)
            file_size = 0
            while chunk := source.read(_CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > max_size_bytes:
                    raise VideoTooLargeError("Video exceeds the configured upload limit")
                staged_file.write(chunk)

        if file_size == 0:
            raise EmptyVideoError("Uploaded video is empty")

        try:
            metadata = extract_video_metadata(temp_path)
        except MediaProbeError as exc:
            raise InvalidVideoError(str(exc)) from None
        if not metadata.format_names.intersection(accepted_containers):
            raise InvalidVideoError("Video container does not match its filename")

        storage_key = f"{uuid4().hex}{extension}"
        if temp_path is None:
            raise RuntimeError("Upload staging file was not created")
        video_data = _video_create(
            safe_filename,
            storage_key=storage_key,
            file_size=file_size,
            target_platform=target_platform,
            content_category=content_category,
            metadata=metadata,
        )
        storage.save(temp_path, storage_key)

        try:
            video = create_video(
                session,
                user_id,
                video_data,
                status=VideoStatus.AVAILABLE,
            )
        except SQLAlchemyError:
            session.rollback()
            raise

        persisted = True
        return video
    finally:
        try:
            if not persisted and storage_key is not None:
                staged_deletion = storage.stage_delete(storage_key)
                staged_deletion.commit()
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def _video_create(
    original_filename: str,
    *,
    storage_key: str,
    file_size: int,
    target_platform: str,
    content_category: str,
    metadata: VideoMetadata,
) -> VideoCreate:
    return VideoCreate(
        original_filename=original_filename,
        storage_key=storage_key,
        file_size=file_size,
        duration=metadata.duration,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        aspect_ratio=metadata.aspect_ratio,
        video_codec=metadata.video_codec,
        has_audio=metadata.has_audio,
        target_platform=target_platform,
        content_category=content_category,
    )
