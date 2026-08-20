import datetime
import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from mealie.core.config import get_app_settings, get_storage
from mealie.core.settings.static import APP_VERSION
from mealie.services._base_service import BaseService
from mealie.services.backups_v2.alchemy_exporter import AlchemyExporter
from mealie.services.backups_v2.backup_file import BackupFile


class BackupSchemaMismatch(Exception): ...


class BackupV2(BaseService):
    EXCLUDE_DIRS = {"backups", ".temp"}
    EXCLUDE_FILES = {"mealie.db"}
    EXCLUDE_FILES_REGEX = {re.compile(r"^mealie\.log(?:\.\d+)?$")}
    EXCLUDE_EXTENTIONS = {".zip"}

    RESTORE_FILES = {".secret"}
    RESTORE_PREFIXES = ("recipes", "users", "groups", "templates")

    def __init__(self, db_url: str | None = None) -> None:
        super().__init__()

        # type - one of these has to be a string
        self.db_url: str = db_url or self.settings.DB_URL  # type: ignore

        self.db_exporter = AlchemyExporter(self.db_url)

    def _sqlite(self) -> None:
        db_file = self.settings.DB_URL.removeprefix("sqlite:///")  # type: ignore

        # Create a backup of the SQLite database
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y.%m.%d")
        shutil.copy(db_file, self.directories.DATA_DIR.joinpath(f"mealie_{timestamp}.bak.db"))

    def _postgres(self) -> None:
        pass

    def _should_exclude(self, key: str) -> bool:
        path = PurePosixPath(key)

        if path.name in self.EXCLUDE_FILES:
            return True

        if any(pattern.search(path.name) for pattern in self.EXCLUDE_FILES_REGEX):
            return True

        if path.suffix in self.EXCLUDE_EXTENTIONS:
            return True

        return path.parent.name in self.EXCLUDE_DIRS

    def backup(self) -> Path:
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y.%m.%d.%H.%M.%S")
        short_hash = self.settings.GIT_COMMIT_HASH[:7]

        if APP_VERSION == "develop":
            backup_name = f"mealie_dev-{short_hash}_{timestamp}.zip"
        elif APP_VERSION == "nightly":
            backup_name = f"mealie_nightly-{short_hash}_{timestamp}.zip"
        else:
            backup_name = f"mealie_{APP_VERSION}_{timestamp}.zip"

        storage = get_storage()

        self.directories.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(dir=self.directories.TEMP_DIR))
        backup_file = temp_dir / backup_name

        database_json = self.db_exporter.dump()

        with ZipFile(backup_file, "w") as zip_file:
            zip_file.writestr("database.json", json.dumps(database_json))

            for entry in storage.iter_entries():
                if self._should_exclude(entry.key):
                    continue

                arcname = f"data/{entry.key}"
                local_path = storage.get_local_path(entry.key)
                if local_path is not None and local_path.is_file():
                    zip_file.write(local_path, arcname)
                else:
                    with storage.open_read(entry.key) as source, zip_file.open(arcname, "w") as dest:
                        shutil.copyfileobj(source, dest)

        storage.write_file(f"backups/{backup_name}", backup_file, content_type="application/zip")

        stored_path = storage.get_local_path(f"backups/{backup_name}")
        if stored_path is None:
            return backup_file

        shutil.rmtree(temp_dir, ignore_errors=True)
        return stored_path

    def _copy_data(self, data_path: Path) -> None:
        storage = get_storage()

        for f in data_path.iterdir():
            if f.is_file():
                if f.name not in self.RESTORE_FILES:
                    continue

                shutil.copyfile(f, self.directories.DATA_DIR / f.name)
                continue

            if f.name not in self.RESTORE_PREFIXES:
                continue

            prefix = f"{f.name}/"
            storage.delete_prefix(prefix)

            for source in sorted(f.glob("**/*")):
                if source.is_file():
                    storage.write_file(f"{prefix}{source.relative_to(f).as_posix()}", source)

        # since we copied a new .secret, AppSettings has the wrong secret info
        self.logger.info("invalidating appsettings cache")
        get_app_settings.cache_clear()
        self.settings = get_app_settings()

    def restore_from_storage(self, file_name: str) -> None:
        key = f"backups/{file_name}"
        storage = get_storage()

        local_path = storage.get_local_path(key)
        if local_path is not None and local_path.is_file():
            self.restore(local_path)
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_zip = Path(temp_dir) / file_name
            storage.download_to_file(key, temp_zip)
            self.restore(temp_zip)

    def restore(self, backup_path: Path) -> None:
        self.logger.info("initializing backup restore")

        backup = BackupFile(backup_path)

        if self.settings.DB_ENGINE == "sqlite":
            self._sqlite()
        elif self.settings.DB_ENGINE == "postgres":
            self._postgres()

        with backup as contents:
            # ================================
            # Validation
            if not contents.validate():
                self.logger.error(
                    "Invalid backup file. file does not contain required elements (data directory and database.json)"
                )
                raise ValueError("Invalid backup file")

            database_json = contents.read_tables()

            # ================================
            # Purge Database

            self.logger.info("dropping all database tables")
            self.db_exporter.drop_all()

            # ================================
            # Restore Database

            self.logger.info("importing database tables")
            self.db_exporter.restore(database_json)

            self.logger.info("database tables imported successfully")

            self.logger.info("restoring data directory")
            self._copy_data(contents.data_directory)
            self.logger.info("data directory restored successfully")
        self.logger.info("backup restore complete")
