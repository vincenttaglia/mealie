import datetime
from abc import ABC, abstractmethod
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, NamedTuple


class StorageError(Exception):
    """Base error for storage provider operations"""


class StorageFileNotFoundError(StorageError):
    """Raised when a storage key does not exist"""

    def __init__(self, key: str) -> None:
        super().__init__(f"storage key not found: {key}")
        self.key = key


class StorageEntry(NamedTuple):
    key: str
    size: int
    modified: datetime.datetime | None
    etag: str | None = None


def safe_key_component(name: str) -> str:
    """
    Validates a single path component destined for a storage key. Guards against
    key/path traversal when composing keys from user-supplied file names.
    """
    if name in {"", ".", ".."} or any(char in name for char in ("/", "\\", "\x00")):
        raise ValueError(f"invalid storage key component: {name!r}")
    return name


class StorageProvider(ABC):
    """
    Abstract interface over persistent file storage.

    Keys are POSIX-style paths relative to the storage root with no leading slash
    (e.g. "recipes/<id>/images/original.webp"); prefixes must end with "/".
    Writes are idempotent overwrites. Providers create any missing parents on write.
    """

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def head(self, key: str) -> StorageEntry:
        """Raises StorageFileNotFoundError if the key does not exist"""

    @abstractmethod
    def open_read(self, key: str) -> BinaryIO:
        """
        Returns a readable binary stream for the key; the caller must close it.
        Raises StorageFileNotFoundError if the key does not exist.
        """

    def read_bytes(self, key: str) -> bytes:
        stream = self.open_read(key)
        try:
            return stream.read()
        finally:
            stream.close()

    @abstractmethod
    def write_stream(self, key: str, stream: BinaryIO, *, content_type: str | None = None) -> None: ...

    def write_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        self.write_stream(key, BytesIO(data), content_type=content_type)

    def write_file(self, key: str, source: Path, *, content_type: str | None = None) -> None:
        with source.open("rb") as stream:
            self.write_stream(key, stream, content_type=content_type)

    @abstractmethod
    def download_to_file(self, key: str, dest: Path) -> None:
        """Downloads the key to a local file, creating parent directories as needed"""

    @abstractmethod
    def delete(self, key: str, *, missing_ok: bool = True) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Deletes every file under the prefix; a no-op when nothing matches"""

    @abstractmethod
    def copy(self, src_key: str, dst_key: str) -> None: ...

    def copy_prefix(self, src_prefix: str, dst_prefix: str) -> None:
        for entry in self.iter_entries(src_prefix):
            self.copy(entry.key, dst_prefix + entry.key.removeprefix(src_prefix))

    @abstractmethod
    def iter_entries(self, prefix: str = "") -> Iterator[StorageEntry]:
        """Recursively yields every file under the prefix; never yields directories"""

    def size_of_prefix(self, prefix: str) -> int:
        return sum(entry.size for entry in self.iter_entries(prefix))

    def get_local_path(self, key: str) -> Path | None:
        """
        The local filesystem path for a key when the provider is backed by local
        disk, else None. Fast path for FileResponse and image tooling; the path is
        not guaranteed to exist.
        """
        return None

    def ping(self) -> None:
        """Verifies the storage backend is reachable; raises on failure"""
        return None
