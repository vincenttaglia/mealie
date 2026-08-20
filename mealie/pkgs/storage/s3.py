from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from .provider import StorageEntry, StorageError, StorageFileNotFoundError, StorageProvider

_MISSING_ERROR_CODES = {"404", "NoSuchKey", "NotFound"}
_DELETE_BATCH_SIZE = 1000


class S3StorageProvider(StorageProvider):
    """
    Stores files in an S3-compatible object store (AWS S3, MinIO, Cloudflare R2, ...).

    When no explicit credentials are configured, boto3's default credential chain
    is used (environment, shared config, IAM instance/task roles).
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        prefix: str = "",
        force_path_style: bool = False,
    ) -> None:
        # boto3 is imported lazily so local-storage deployments never pay the import cost
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError

        if not bucket:
            raise StorageError("S3 storage requires a bucket name")

        self.bucket = bucket
        self.prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self._client_error = ClientError
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(s3={"addressing_style": "path" if force_path_style else "auto"}),
        )

    def _key(self, key: str) -> str:
        if key.startswith("/") or ".." in key.split("/"):
            raise StorageError(f"invalid storage key: {key!r}")
        return self.prefix + key

    def _is_missing(self, error: Any) -> bool:
        return str(error.response.get("Error", {}).get("Code")) in _MISSING_ERROR_CODES

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except self._client_error as e:
            if self._is_missing(e):
                return False
            raise

    def head(self, key: str) -> StorageEntry:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=self._key(key))
        except self._client_error as e:
            if self._is_missing(e):
                raise StorageFileNotFoundError(key) from e
            raise
        return StorageEntry(
            key=key,
            size=response["ContentLength"],
            modified=response.get("LastModified"),
            etag=response.get("ETag"),
        )

    def open_read(self, key: str) -> BinaryIO:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._key(key))
        except self._client_error as e:
            if self._is_missing(e):
                raise StorageFileNotFoundError(key) from e
            raise
        return response["Body"]

    def write_stream(self, key: str, stream: BinaryIO, *, content_type: str | None = None) -> None:
        extra_args = {"ContentType": content_type} if content_type else {}
        self._client.upload_fileobj(stream, self.bucket, self._key(key), ExtraArgs=extra_args)

    def write_file(self, key: str, source: Path, *, content_type: str | None = None) -> None:
        extra_args = {"ContentType": content_type} if content_type else {}
        self._client.upload_file(str(source), self.bucket, self._key(key), ExtraArgs=extra_args)

    def download_to_file(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.bucket, self._key(key), str(dest))
        except self._client_error as e:
            if self._is_missing(e):
                raise StorageFileNotFoundError(key) from e
            raise

    def delete(self, key: str, *, missing_ok: bool = True) -> None:
        if not missing_ok and not self.exists(key):
            raise StorageFileNotFoundError(key)
        self._client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def delete_prefix(self, prefix: str) -> None:
        batch: list[dict[str, str]] = []
        for page in self._paginate(prefix):
            for obj in page.get("Contents", []):
                batch.append({"Key": obj["Key"]})
                if len(batch) == _DELETE_BATCH_SIZE:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
                    batch = []
        if batch:
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})

    def copy(self, src_key: str, dst_key: str) -> None:
        try:
            # client.copy handles multipart copies for objects over 5GB
            self._client.copy(
                {"Bucket": self.bucket, "Key": self._key(src_key)},
                self.bucket,
                self._key(dst_key),
            )
        except self._client_error as e:
            if self._is_missing(e):
                raise StorageFileNotFoundError(src_key) from e
            raise

    def _paginate(self, prefix: str) -> Iterator[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        yield from paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix))

    def iter_entries(self, prefix: str = "") -> Iterator[StorageEntry]:
        for page in self._paginate(prefix):
            for obj in page.get("Contents", []):
                yield StorageEntry(
                    key=obj["Key"].removeprefix(self.prefix),
                    size=obj["Size"],
                    modified=obj.get("LastModified"),
                    etag=obj.get("ETag"),
                )

    def ping(self) -> None:
        self._client.head_bucket(Bucket=self.bucket)
