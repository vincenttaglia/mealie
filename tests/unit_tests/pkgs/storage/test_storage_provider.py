"""
Contract tests for every StorageProvider implementation.

Each test in the shared section runs against the local filesystem provider and the
S3 provider (via moto), including an S3 provider configured with a key prefix to
prove that prefixing is fully transparent to callers.
"""

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from mealie.pkgs.storage import (
    StorageEntry,
    StorageError,
    StorageFileNotFoundError,
    StorageProvider,
    safe_key_component,
)
from mealie.pkgs.storage.local import LocalStorageProvider
from mealie.pkgs.storage.s3 import S3StorageProvider

BUCKET = "mealie-test-bucket"
REGION = "us-east-1"


def _set_fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


def _build_s3_provider(prefix: str = "") -> S3StorageProvider:
    """Creates the mock bucket and returns a provider pointed at it. Requires an active moto mock."""
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
    return S3StorageProvider(
        BUCKET,
        region=REGION,
        access_key_id="testing",
        secret_access_key="testing",
        prefix=prefix,
    )


@pytest.fixture(params=["local", "s3", "s3-prefixed"])
def provider(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StorageProvider]:
    if request.param == "local":
        data_dir = tmp_path / "storage-data"
        data_dir.mkdir()
        yield LocalStorageProvider(data_dir)
        return

    _set_fake_aws_credentials(monkeypatch)
    with mock_aws():
        yield _build_s3_provider(prefix="mealie-test" if request.param == "s3-prefixed" else "")


# ---------------------------------------------------------------------------
# Shared provider contract
# ---------------------------------------------------------------------------
def test_write_bytes_read_bytes_round_trip(provider: StorageProvider) -> None:
    provider.write_bytes("recipes/abc/original.webp", b"hello world")
    assert provider.read_bytes("recipes/abc/original.webp") == b"hello world"

    # writes are idempotent overwrites
    provider.write_bytes("recipes/abc/original.webp", b"overwritten")
    assert provider.read_bytes("recipes/abc/original.webp") == b"overwritten"


def test_write_stream_and_open_read(provider: StorageProvider) -> None:
    payload = b"\x00\x01\x02" * 100
    provider.write_stream("streams/blob.bin", BytesIO(payload), content_type="application/octet-stream")

    stream = provider.open_read("streams/blob.bin")
    try:
        assert stream.read() == payload
    finally:
        stream.close()


def test_open_read_missing_raises(provider: StorageProvider) -> None:
    with pytest.raises(StorageFileNotFoundError):
        provider.open_read("missing/blob.bin")


def test_read_bytes_missing_raises(provider: StorageProvider) -> None:
    with pytest.raises(StorageFileNotFoundError):
        provider.read_bytes("missing/blob.bin")


def test_write_file_and_download_to_file(provider: StorageProvider, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"file contents")

    provider.write_file("uploads/copy.txt", source, content_type="text/plain")
    assert provider.read_bytes("uploads/copy.txt") == b"file contents"

    dest = tmp_path / "nested" / "dirs" / "download.txt"
    provider.download_to_file("uploads/copy.txt", dest)
    assert dest.read_bytes() == b"file contents"


def test_download_to_file_missing_raises(provider: StorageProvider, tmp_path: Path) -> None:
    with pytest.raises(StorageFileNotFoundError):
        provider.download_to_file("missing.txt", tmp_path / "nested" / "download.txt")


def test_exists(provider: StorageProvider) -> None:
    assert not provider.exists("some/key.txt")
    provider.write_bytes("some/key.txt", b"x")
    assert provider.exists("some/key.txt")


def test_head_returns_entry_fields(provider: StorageProvider) -> None:
    provider.write_bytes("meta/file.txt", b"12345")

    entry = provider.head("meta/file.txt")
    assert isinstance(entry, StorageEntry)
    assert entry.key == "meta/file.txt"
    assert entry.size == 5
    assert entry.modified is not None


def test_head_missing_raises(provider: StorageProvider) -> None:
    with pytest.raises(StorageFileNotFoundError) as exc_info:
        provider.head("missing/file.txt")
    assert exc_info.value.key == "missing/file.txt"


def test_delete_removes_key(provider: StorageProvider) -> None:
    provider.write_bytes("del/file.txt", b"x")
    provider.delete("del/file.txt")
    assert not provider.exists("del/file.txt")


def test_delete_missing_ok_does_not_raise(provider: StorageProvider) -> None:
    provider.delete("missing.txt")
    provider.delete("missing.txt", missing_ok=True)


def test_delete_missing_not_ok_raises(provider: StorageProvider) -> None:
    with pytest.raises(StorageFileNotFoundError):
        provider.delete("missing.txt", missing_ok=False)


def test_delete_prefix(provider: StorageProvider) -> None:
    provider.write_bytes("recipes/one/a.txt", b"a")
    provider.write_bytes("recipes/one/images/b.txt", b"b")
    provider.write_bytes("recipes/two/c.txt", b"c")

    provider.delete_prefix("recipes/one/")

    assert not provider.exists("recipes/one/a.txt")
    assert not provider.exists("recipes/one/images/b.txt")
    assert provider.exists("recipes/two/c.txt")


def test_delete_prefix_empty_is_noop(provider: StorageProvider) -> None:
    provider.delete_prefix("does/not/exist/")


def test_copy(provider: StorageProvider) -> None:
    provider.write_bytes("src.txt", b"copy me")
    provider.copy("src.txt", "dst/nested/dst.txt")

    assert provider.read_bytes("dst/nested/dst.txt") == b"copy me"
    assert provider.exists("src.txt")


def test_copy_missing_source_raises(provider: StorageProvider) -> None:
    with pytest.raises(StorageFileNotFoundError):
        provider.copy("missing.txt", "dst.txt")


def test_copy_prefix(provider: StorageProvider) -> None:
    provider.write_bytes("orig/a.txt", b"a")
    provider.write_bytes("orig/sub/b.txt", b"bb")

    provider.copy_prefix("orig/", "backup/orig/")

    assert provider.read_bytes("backup/orig/a.txt") == b"a"
    assert provider.read_bytes("backup/orig/sub/b.txt") == b"bb"
    assert provider.exists("orig/a.txt")
    assert provider.exists("orig/sub/b.txt")


def test_iter_entries_recursive(provider: StorageProvider) -> None:
    provider.write_bytes("tree/a.txt", b"1")
    provider.write_bytes("tree/sub/b.txt", b"22")
    provider.write_bytes("tree/sub/deep/c.txt", b"333")
    provider.write_bytes("other/d.txt", b"4444")

    # exact dict equality also proves no "directory" entries are ever yielded
    entries = {entry.key: entry.size for entry in provider.iter_entries("tree/")}
    assert entries == {"tree/a.txt": 1, "tree/sub/b.txt": 2, "tree/sub/deep/c.txt": 3}

    all_entries = {entry.key: entry.size for entry in provider.iter_entries("")}
    assert all_entries == {
        "tree/a.txt": 1,
        "tree/sub/b.txt": 2,
        "tree/sub/deep/c.txt": 3,
        "other/d.txt": 4,
    }


def test_iter_entries_empty_prefix_yields_nothing(provider: StorageProvider) -> None:
    assert list(provider.iter_entries("nothing/here/")) == []


def test_size_of_prefix(provider: StorageProvider) -> None:
    provider.write_bytes("sized/a.bin", b"12345")
    provider.write_bytes("sized/sub/b.bin", b"1234567890")
    provider.write_bytes("unsized/c.bin", b"xxx")

    assert provider.size_of_prefix("sized/") == 15
    assert provider.size_of_prefix("nothing/") == 0


def test_get_local_path(provider: StorageProvider) -> None:
    provider.write_bytes("local/path.txt", b"x")
    local_path = provider.get_local_path("local/path.txt")

    if isinstance(provider, LocalStorageProvider):
        assert local_path is not None
        assert local_path == (provider.data_dir / "local/path.txt").resolve()
        assert local_path.read_bytes() == b"x"
    else:
        assert local_path is None


def test_ping(provider: StorageProvider) -> None:
    provider.ping()


# ---------------------------------------------------------------------------
# safe_key_component
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["original.webp", "my recipe.jpg", "a-b_c.1", "..hidden"])
def test_safe_key_component_accepts_normal_names(name: str) -> None:
    assert safe_key_component(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "a\x00b"])
def test_safe_key_component_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError, match="invalid storage key component"):
        safe_key_component(name)


# ---------------------------------------------------------------------------
# Local provider specifics
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["../outside.txt", "a/../../outside.txt"])
def test_local_provider_rejects_traversal_keys(tmp_path: Path, key: str) -> None:
    provider = LocalStorageProvider(tmp_path)

    with pytest.raises(StorageError):
        provider.write_bytes(key, b"x")
    with pytest.raises(StorageError):
        provider.exists(key)


def test_local_iter_entries_skips_empty_directories(tmp_path: Path) -> None:
    provider = LocalStorageProvider(tmp_path)
    provider.write_bytes("dirtest/file.txt", b"x")
    (tmp_path / "dirtest" / "empty").mkdir()

    assert [entry.key for entry in provider.iter_entries("")] == ["dirtest/file.txt"]


# ---------------------------------------------------------------------------
# S3 provider specifics
# ---------------------------------------------------------------------------
def test_s3_prefix_is_transparent_but_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_fake_aws_credentials(monkeypatch)
    with mock_aws():
        provider = _build_s3_provider(prefix="mealie-test")
        provider.write_bytes("recipes/a.txt", b"x")

        raw = boto3.client("s3", region_name=REGION).list_objects_v2(Bucket=BUCKET)
        assert [obj["Key"] for obj in raw["Contents"]] == ["mealie-test/recipes/a.txt"]
        assert [entry.key for entry in provider.iter_entries("")] == ["recipes/a.txt"]


def test_s3_delete_prefix_batches_many_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_fake_aws_credentials(monkeypatch)
    # shrink the batch size so 25 keys exercise the multi-batch code path
    monkeypatch.setattr("mealie.pkgs.storage.s3._DELETE_BATCH_SIZE", 10)

    with mock_aws():
        provider = _build_s3_provider()
        for i in range(25):
            provider.write_bytes(f"bulk/file-{i:03d}.txt", b"x")
        provider.write_bytes("keep/file.txt", b"x")

        provider.delete_prefix("bulk/")

        assert list(provider.iter_entries("bulk/")) == []
        assert provider.exists("keep/file.txt")
