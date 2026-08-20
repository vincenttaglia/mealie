from fastapi import APIRouter, HTTPException, Request, status
from pydantic import UUID4
from starlette.responses import Response

from mealie.core.config import get_storage
from mealie.pkgs.storage import StorageFileNotFoundError, safe_key_component, storage_response
from mealie.schema.user import PrivateUser

router = APIRouter(prefix="/users")


@router.get("/{user_id}/{file_name}")
async def get_user_image(request: Request, user_id: UUID4, file_name: str) -> Response:
    """Takes in a user id, returns the static image"""
    try:
        safe_key_component(file_name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST) from e

    key = f"{PrivateUser.storage_prefix(user_id)}{file_name}"

    try:
        return storage_response(get_storage(), key, media_type="image/webp", request=request)
    except StorageFileNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from e
