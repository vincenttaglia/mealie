import os
from functools import lru_cache
from pathlib import Path

import dotenv

from mealie.core.settings import (
    AppDirectories,
    AppLoggingSettings,
    AppSettings,
    app_settings_constructor,
)
from mealie.pkgs.storage import StorageProvider

CWD = Path(__file__).parent
BASE_DIR = CWD.parent.parent
ENV = BASE_DIR.joinpath(".env")

dotenv.load_dotenv(ENV)
PRODUCTION = os.getenv("PRODUCTION", "True").lower() in ["true", "1"]
TESTING = os.getenv("TESTING", "False").lower() in ["true", "1"]
DATA_DIR = os.getenv("DATA_DIR")


def determine_data_dir() -> Path:
    global PRODUCTION, TESTING, BASE_DIR, DATA_DIR

    if TESTING:
        return BASE_DIR.joinpath(DATA_DIR if DATA_DIR else "tests/.temp")

    if PRODUCTION:
        return Path(DATA_DIR if DATA_DIR else "/app/data")

    return BASE_DIR.joinpath("dev", "data")


@lru_cache
def get_app_dirs() -> AppDirectories:
    return AppDirectories(determine_data_dir())


@lru_cache
def get_app_settings() -> AppSettings:
    return app_settings_constructor(env_file=ENV, production=PRODUCTION, data_dir=determine_data_dir())


@lru_cache
def get_logging_settings() -> AppLoggingSettings:
    return AppLoggingSettings(PRODUCTION=PRODUCTION)


@lru_cache
def get_storage() -> StorageProvider:
    settings = get_app_settings()
    if settings.STORAGE_PROVIDER == "s3":
        # imported lazily so local-storage deployments never import boto3
        from mealie.pkgs.storage.s3 import S3StorageProvider

        return S3StorageProvider(
            bucket=settings.STORAGE_S3_BUCKET or "",
            endpoint_url=settings.STORAGE_S3_ENDPOINT_URL,
            region=settings.STORAGE_S3_REGION,
            access_key_id=settings.STORAGE_S3_ACCESS_KEY_ID,
            secret_access_key=settings.STORAGE_S3_SECRET_ACCESS_KEY,
            prefix=settings.STORAGE_S3_PREFIX,
            force_path_style=settings.STORAGE_S3_FORCE_PATH_STYLE,
        )

    from mealie.pkgs.storage.local import LocalStorageProvider

    return LocalStorageProvider(determine_data_dir())
