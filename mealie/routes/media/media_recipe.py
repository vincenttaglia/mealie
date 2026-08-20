from enum import StrEnum

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import UUID4
from starlette.responses import Response

from mealie.core.config import get_storage
from mealie.pkgs.storage import StorageFileNotFoundError, safe_key_component, storage_response
from mealie.schema.recipe import Recipe
from mealie.schema.recipe.recipe_timeline_events import RecipeTimelineEventOut

router = APIRouter(prefix="/recipes")


class ImageType(StrEnum):
    original = "original.webp"
    small = "min-original.webp"
    tiny = "tiny-original.webp"


@router.get("/{recipe_id}/images/{file_name}")
async def get_recipe_img(request: Request, recipe_id: UUID4, file_name: ImageType = ImageType.original) -> Response:
    """Takes in a recipe id, returns the static image"""
    key = Recipe.image_key_from_id(recipe_id, file_name.value)

    try:
        return storage_response(get_storage(), key, media_type="image/webp", request=request)
    except StorageFileNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from e


@router.get("/{recipe_id}/images/timeline/{timeline_event_id}/{file_name}")
async def get_recipe_timeline_event_img(
    request: Request, recipe_id: UUID4, timeline_event_id: UUID4, file_name: ImageType = ImageType.original
) -> Response:
    """Takes in a recipe id and event timeline id, returns the static image"""
    key = RecipeTimelineEventOut.image_key_from_id(recipe_id, timeline_event_id, file_name.value)

    try:
        return storage_response(get_storage(), key, media_type="image/webp", request=request)
    except StorageFileNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from e


@router.get("/{recipe_id}/assets/{file_name}")
async def get_recipe_asset(request: Request, recipe_id: UUID4, file_name: str) -> Response:
    """Returns a recipe asset"""
    try:
        safe_key_component(file_name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST) from e

    key = Recipe.asset_key_from_id(recipe_id, file_name)

    try:
        # Force download and disable MIME sniffing so uploaded assets cannot be
        # served as active content in Mealie's origin.
        return storage_response(
            get_storage(),
            key,
            filename=file_name,
            attachment=True,
            headers={"X-Content-Type-Options": "nosniff"},
            request=request,
        )
    except StorageFileNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from e
