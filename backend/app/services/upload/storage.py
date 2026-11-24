import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

from app.core.config import PROJECT_ROOT, settings


class StorageError(RuntimeError):
    pass


class StorageConfigurationError(RuntimeError):
    pass


class StagedDeletion(Protocol):
    def restore(self) -> None: ...

    def commit(self) -> None: ...


class StorageBackend(Protocol):
    def save(self, source_path: Path, storage_key: str) -> None: ...

    def stage_delete(self, storage_key: str) -> StagedDeletion: ...


class _LocalStagedDeletion:
    def __init__(self, original_path: Path, staged_path: Path | None) -> None:
        self._original_path = original_path
        self._staged_path = staged_path

    def restore(self) -> None:
        if self._staged_path is None or not self._staged_path.exists():
            return
        try:
            self._original_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self._staged_path, self._original_path)
        except OSError as exc:
            raise StorageError("Unable to restore deleted media") from exc
        self._staged_path = None

    def commit(self) -> None:
        if self._staged_path is None:
            return
        try:
            self._staged_path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Unable to finalize media deletion") from exc
        self._staged_path = None


class LocalFilesystemStorage:
    def __init__(self, root_path: Path) -> None:
        self._root_path = root_path.resolve()

    def save(self, source_path: Path, storage_key: str) -> None:
        target_path = self._resolve_key(storage_key)
        partial_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.part")
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, partial_path)
            os.replace(partial_path, target_path)
        except OSError as exc:
            partial_path.unlink(missing_ok=True)
            raise StorageError("Unable to store uploaded media") from exc

    def stage_delete(self, storage_key: str) -> StagedDeletion:
        original_path = self._resolve_key(storage_key)
        if not original_path.exists():
            return _LocalStagedDeletion(original_path, None)

        trash_path = self._root_path / ".trash" / f"{uuid4().hex}.media"
        try:
            trash_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original_path, trash_path)
        except OSError as exc:
            raise StorageError("Unable to stage media deletion") from exc
        return _LocalStagedDeletion(original_path, trash_path)

    def _resolve_key(self, storage_key: str) -> Path:
        key_path = PurePosixPath(storage_key)
        if (
            not storage_key
            or "\\" in storage_key
            or ":" in storage_key
            or key_path.is_absolute()
            or not key_path.parts
            or any(part in {"", ".", ".."} for part in key_path.parts)
            or key_path.parts[0] == ".trash"
        ):
            raise ValueError("Invalid storage key")

        resolved_path = self._root_path.joinpath(*key_path.parts).resolve()
        if self._root_path not in resolved_path.parents:
            raise ValueError("Invalid storage key")
        return resolved_path


def get_storage_backend() -> StorageBackend:
    if settings.storage_backend.casefold() != "local":
        raise StorageConfigurationError("Configured storage backend is unavailable")

    root_path = settings.storage_local_path
    if not root_path.is_absolute():
        root_path = PROJECT_ROOT / root_path
    return LocalFilesystemStorage(root_path)
