from collections.abc import Iterator
from email.utils import format_datetime
from typing import BinaryIO

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse

from .provider import StorageFileNotFoundError, StorageProvider

_CHUNK_SIZE = 64 * 1024


def _iter_stream(stream: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := stream.read(_CHUNK_SIZE):
            yield chunk
    finally:
        stream.close()


def storage_response(
    storage: StorageProvider,
    key: str,
    *,
    media_type: str | None = None,
    filename: str | None = None,
    attachment: bool = False,
    headers: dict[str, str] | None = None,
    request: Request | None = None,
    background: BackgroundTask | None = None,
) -> Response:
    """
    Serves a storage key over HTTP: a FileResponse when the provider is backed by
    local disk, otherwise a streamed response with ETag/Last-Modified headers
    (returning 304 when the request's If-None-Match matches).

    Raises StorageFileNotFoundError when the key does not exist.
    """
    if attachment and not filename:
        filename = key.rsplit("/", 1)[-1]

    local_path = storage.get_local_path(key)
    if local_path is not None:
        if not local_path.is_file():
            raise StorageFileNotFoundError(key)
        return FileResponse(
            local_path,
            media_type=media_type,
            filename=filename,
            content_disposition_type="attachment" if attachment else "inline",
            headers=headers,
            background=background,
        )

    entry = storage.head(key)

    response_headers = dict(headers) if headers else {}
    if entry.etag:
        response_headers["etag"] = entry.etag
    if entry.modified:
        response_headers["last-modified"] = format_datetime(entry.modified, usegmt=True)

    if request is not None and entry.etag and request.headers.get("if-none-match") == entry.etag:
        return Response(status_code=304, headers=response_headers, background=background)

    if filename:
        disposition = "attachment" if attachment else "inline"
        response_headers["content-disposition"] = f'{disposition}; filename="{filename}"'
    response_headers["content-length"] = str(entry.size)

    return StreamingResponse(
        _iter_stream(storage.open_read(key)),
        media_type=media_type or "application/octet-stream",
        headers=response_headers,
        background=background,
    )
