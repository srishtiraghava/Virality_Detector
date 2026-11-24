from pathlib import Path

import pytest

from app.services.upload.storage import LocalFilesystemStorage
from app.services.video.metadata import MediaProbeError, extract_video_metadata


def test_ffprobe_extracts_video_and_audio_metadata(sample_video_file: Path) -> None:
    metadata = extract_video_metadata(sample_video_file)

    assert metadata.duration > 0
    assert metadata.width == 64
    assert metadata.height == 48
    assert metadata.fps == pytest.approx(24)
    assert metadata.aspect_ratio == pytest.approx(4 / 3)
    assert metadata.video_codec == "mpeg4"
    assert metadata.has_audio
    assert {"mov", "mp4"}.intersection(metadata.format_names)


def test_ffprobe_reports_absent_audio_without_inventing_streams(
    silent_video_file: Path,
) -> None:
    metadata = extract_video_metadata(silent_video_file)

    assert not metadata.has_audio


def test_ffprobe_rejects_non_media_content(tmp_path: Path) -> None:
    invalid_media = tmp_path / "invalid.mp4"
    invalid_media.write_bytes(b"not video media")

    with pytest.raises(MediaProbeError):
        extract_video_metadata(invalid_media)


def test_local_storage_stages_deletion_and_can_restore(
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    storage = LocalFilesystemStorage(tmp_path / "storage")
    storage.save(sample_video_file, "owned-video.mp4")
    stored_path = tmp_path / "storage" / "owned-video.mp4"

    staged = storage.stage_delete("owned-video.mp4")
    assert not stored_path.exists()
    staged.restore()
    assert stored_path.read_bytes() == sample_video_file.read_bytes()

    staged = storage.stage_delete("owned-video.mp4")
    staged.commit()
    assert not stored_path.exists()


def test_local_storage_rejects_traversal_keys(
    tmp_path: Path,
    sample_video_file: Path,
) -> None:
    storage = LocalFilesystemStorage(tmp_path / "storage")

    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.save(sample_video_file, "..\\outside.mp4")
