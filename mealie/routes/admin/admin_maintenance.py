import shutil
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException

from mealie.core.config import get_storage
from mealie.pkgs.stats import fs_stats
from mealie.pkgs.storage import StorageProvider
from mealie.routes._base import BaseAdminController, controller
from mealie.schema.admin import MaintenanceSummary
from mealie.schema.admin.maintenance import MaintenanceStorageDetails
from mealie.schema.response import ErrorResponse, SuccessResponse

router = APIRouter(prefix="/maintenance")


def clean_images(storage: StorageProvider, dry_run: bool) -> int:
    cleaned_images = 0

    for entry in storage.iter_entries("recipes/"):
        parts = entry.key.split("/")
        if len(parts) != 4 or parts[2] != "images":
            continue

        if PurePosixPath(entry.key).suffix != ".webp":
            if not dry_run:
                storage.delete(entry.key)

            cleaned_images += 1

    return cleaned_images


def clean_recipe_folders(storage: StorageProvider, dry_run: bool) -> int:
    folders: set[str] = set()

    for entry in storage.iter_entries("recipes/"):
        relative_key = entry.key.removeprefix("recipes/")
        if "/" in relative_key:
            folders.add(relative_key.split("/", 1)[0])

    cleaned_dirs = 0

    for folder in folders:
        # Attempt to convert the folder name to a UUID
        try:
            uuid.UUID(folder)
            continue
        except ValueError:
            if not dry_run:
                storage.delete_prefix(f"recipes/{folder}/")
            cleaned_dirs += 1

    return cleaned_dirs


def tail_log(log_file: Path, n: int) -> list[str]:
    try:
        with open(log_file) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return ["no log file found"]

    return lines[-n:]


@controller(router)
class AdminMaintenanceController(BaseAdminController):
    @router.get("", response_model=MaintenanceSummary)
    def get_maintenance_summary(self):
        """
        Get the maintenance summary
        """
        storage = get_storage()

        data_dir_size = fs_stats.get_dir_size(self.folders.DATA_DIR)
        if storage.get_local_path("") is None:
            data_dir_size += storage.size_of_prefix("")

        return MaintenanceSummary(
            data_dir_size=fs_stats.pretty_size(data_dir_size),
            cleanable_images=clean_images(storage, dry_run=True),
            cleanable_dirs=clean_recipe_folders(storage, dry_run=True),
        )

    @router.get("/storage", response_model=MaintenanceStorageDetails)
    def get_storage_details(self):
        storage = get_storage()

        return MaintenanceStorageDetails(
            temp_dir_size=fs_stats.pretty_size(fs_stats.get_dir_size(self.folders.TEMP_DIR)),
            backups_dir_size=fs_stats.pretty_size(storage.size_of_prefix("backups/")),
            groups_dir_size=fs_stats.pretty_size(storage.size_of_prefix("groups/")),
            recipes_dir_size=fs_stats.pretty_size(storage.size_of_prefix("recipes/")),
            user_dir_size=fs_stats.pretty_size(storage.size_of_prefix("users/")),
        )

    @router.post("/clean/images", response_model=SuccessResponse)
    def clean_images(self):
        """
        Purges all the images from the filesystem that aren't .webp
        """
        try:
            cleaned_images = clean_images(get_storage(), dry_run=False)
            return SuccessResponse.respond(f"{cleaned_images} Images cleaned")
        except Exception as e:
            raise HTTPException(status_code=500, detail=ErrorResponse.respond("Failed to clean images")) from e

    @router.post("/clean/temp", response_model=SuccessResponse)
    def clean_temp(self):
        try:
            if self.folders.TEMP_DIR.exists():
                shutil.rmtree(self.folders.TEMP_DIR)

            self.folders.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=ErrorResponse.respond("Failed to clean temp")) from e

        return SuccessResponse.respond("'.temp' directory cleaned")

    @router.post("/clean/recipe-folders", response_model=SuccessResponse)
    def clean_recipe_folders(self):
        """
        Deletes all the recipe folders that don't have names that are valid UUIDs
        """
        try:
            cleaned_dirs = clean_recipe_folders(get_storage(), dry_run=False)
            return SuccessResponse.respond(f"{cleaned_dirs} Recipe folders removed")
        except Exception as e:
            raise HTTPException(status_code=500, detail=ErrorResponse.respond("Failed to clean directories")) from e
