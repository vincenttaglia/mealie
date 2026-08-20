import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .provider import StorageEntry, StorageError, StorageFileNotFoundError, StorageProvider


class LocalStorageProvider(StorageProvider):
    """
    Stores files on the local filesystem under the data directory, preserving
    Mealie's historical on-disk layout (a key maps directly to DATA_DIR/<key>).
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._root = data_dir.resolve()

    def _path(self, key: str) -> Path:
        path = (self.data_dir / key).resolve()
        if path != self._root and not path.is_relative_to(self._root):
            raise StorageError(f"storage key escapes the data directory: {key!r}")
        return path

    def _entry(self, key: str, path: Path) -> StorageEntry:
        stat = path.stat()
        return StorageEntry(key=key, size=stat.st_size, modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def head(self, key: str) -> StorageEntry:
        path = self._path(key)
        if not path.is_file():
            raise StorageFileNotFoundError(key)
        return self._entry(key, path)

    def open_read(self, key: str) -> BinaryIO:
        try:
            return self._path(key).open("rb")
        except (FileNotFoundError, IsADirectoryError) as e:
            raise StorageFileNotFoundError(key) from e

    def write_stream(self, key: str, stream: BinaryIO, *, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            shutil.copyfileobj(stream, f)

    def write_file(self, key: str, source: Path, *, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)

    def download_to_file(self, key: str, dest: Path) -> None:
        path = self._path(key)
        if not path.is_file():
            raise StorageFileNotFoundError(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    def delete(self, key: str, *, missing_ok: bool = True) -> None:
        path = self._path(key)
        if not missing_ok and not path.is_file():
            raise StorageFileNotFoundError(key)
        path.unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        shutil.rmtree(self._path(prefix), ignore_errors=True)

    def copy(self, src_key: str, dst_key: str) -> None:
        src = self._path(src_key)
        if not src.is_file():
            raise StorageFileNotFoundError(src_key)
        dst = self._path(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def copy_prefix(self, src_prefix: str, dst_prefix: str) -> None:
        src = self._path(src_prefix)
        if not src.is_dir():
            return
        shutil.copytree(src, self._path(dst_prefix), dirs_exist_ok=True)

    def iter_entries(self, prefix: str = "") -> Iterator[StorageEntry]:
        root = self._path(prefix)
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if path.is_file():
                yield self._entry(path.relative_to(self._root).as_posix(), path)

    def get_local_path(self, key: str) -> Path | None:
        return self._path(key)
