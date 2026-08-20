from fastapi import APIRouter, Depends, HTTPException, status
from starlette.responses import Response

from mealie.core.config import get_storage
from mealie.core.dependencies import validate_file_token
from mealie.pkgs.storage import StorageFileNotFoundError, storage_response

router = APIRouter(prefix="/api/utils", tags=["Utils"], include_in_schema=True)


@router.get("/download")
async def download_file(file_key: str = Depends(validate_file_token)) -> Response:
    """Uses a file token obtained by an active user to retrieve a file from storage."""

    try:
        return storage_response(get_storage(), file_key, media_type="application/octet-stream", attachment=True)
    except StorageFileNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from e
