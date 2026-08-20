import datetime
import operator
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from mealie.core.config import get_storage
from mealie.core.root_logger import get_logger
from mealie.core.security import create_file_token
from mealie.pkgs.stats.fs_stats import pretty_size
from mealie.pkgs.storage import safe_key_component
from mealie.routes._base import BaseAdminController, controller
from mealie.schema.admin.backup import AllBackups, BackupFile
from mealie.schema.response.responses import ErrorResponse, FileTokenResponse, SuccessResponse
from mealie.services.backups_v2.backup_v2 import BackupSchemaMismatch, BackupV2

logger = get_logger()
router = APIRouter(prefix="/backups")


@controller(router)
class AdminBackupController(BaseAdminController):
    def _backup_key(self, file_name: str) -> str:
        try:
            return f"backups/{safe_key_component(file_name)}"
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST) from e

    @router.get("", response_model=AllBackups)
    def get_all(self):
        storage = get_storage()

        imports = []
        for entry in storage.iter_entries("backups/"):
            name = entry.key.removeprefix("backups/")
            if "/" in name or not name.endswith(".zip"):
                continue

            date = entry.modified or datetime.datetime.fromtimestamp(0, datetime.UTC)
            imports.append(BackupFile(name=name, date=date, size=pretty_size(entry.size)))

        templates = []
        for entry in storage.iter_entries("templates/"):
            name = entry.key.removeprefix("templates/")
            if "/" in name or "." not in name:
                continue

            templates.append(name)

        imports.sort(key=operator.attrgetter("date"), reverse=True)

        return AllBackups(imports=imports, templates=templates)

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=SuccessResponse)
    def create_one(self):
        backup = BackupV2()

        try:
            backup.backup()
        except Exception as e:
            logger.exception(e)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR) from e

        return SuccessResponse.respond("Backup created successfully")

    @router.get("/{file_name}", response_model=FileTokenResponse)
    def get_one(self, file_name: str):
        """Returns a token to download a file"""
        key = self._backup_key(file_name)

        if not get_storage().exists(key):
            raise HTTPException(status.HTTP_404_NOT_FOUND)

        return FileTokenResponse.respond(create_file_token(key))

    @router.delete("/{file_name}", status_code=status.HTTP_200_OK, response_model=SuccessResponse)
    def delete_one(self, file_name: str):
        key = self._backup_key(file_name)
        storage = get_storage()

        if not storage.exists(key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST)
        try:
            storage.delete(key)
        except Exception as e:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR) from e

        return SuccessResponse.respond(f"{file_name} has been deleted.")

    @router.post("/upload", response_model=SuccessResponse)
    def upload_one(self, archive: UploadFile = File(...)):
        """Upload a .zip File to later be imported into Mealie"""
        if not archive.filename or "." not in archive.filename:
            raise HTTPException(status.HTTP_400_BAD_REQUEST)

        if archive.filename.split(".")[-1] != "zip":
            raise HTTPException(status.HTTP_400_BAD_REQUEST)

        name = Path(archive.filename).stem
        key = self._backup_key(f"{name}.zip")

        storage = get_storage()
        storage.write_stream(key, archive.file, content_type="application/zip")

        if not storage.exists(key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST)
        return SuccessResponse.respond("Upload successful")

    @router.post("/{file_name}/restore", response_model=SuccessResponse)
    def import_one(self, file_name: str):
        backup = BackupV2()

        try:
            file_name = safe_key_component(file_name)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST) from e

        try:
            backup.restore_from_storage(file_name)
        except BackupSchemaMismatch as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                ErrorResponse.respond("database backup schema version does not match current database"),
            ) from e
        except Exception as e:
            logger.exception(e)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR) from e

        return SuccessResponse.respond("Restore successful")
