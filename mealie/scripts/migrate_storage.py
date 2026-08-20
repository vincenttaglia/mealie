import argparse
import fnmatch
import mimetypes
import sys

from mealie.core import root_logger
from mealie.core.config import determine_data_dir, get_app_settings, get_storage
from mealie.pkgs.storage import StorageFileNotFoundError
from mealie.pkgs.storage.local import LocalStorageProvider

logger = root_logger.get_logger()

PROGRESS_INTERVAL = 50

# content that always stays on local disk and must never be migrated
SKIP_DIR_PREFIXES = (".temp/", "docker-validation/")
SKIP_ROOT_FILE_PATTERNS = ("mealie.db*", "*.log*", ".secret*", ".session_secret*", "*.bak.db")


def should_skip(key: str) -> bool:
    """Whether a key is local-only content (database, logs, secrets, temp files)"""
    if any(key.startswith(prefix) for prefix in SKIP_DIR_PREFIXES):
        return True
    if "/" not in key:  # root-level files
        return any(fnmatch.fnmatch(key, pattern) for pattern in SKIP_ROOT_FILE_PATTERNS)
    return False


def migrate(dry_run: bool) -> None:
    settings = get_app_settings()
    if settings.STORAGE_PROVIDER != "s3":
        logger.error(
            "STORAGE_PROVIDER must be set to 's3' (with the STORAGE_S3_* variables configured) "
            "before running this migration; see the backend configuration documentation"
        )
        sys.exit(1)

    data_dir = determine_data_dir()
    source = LocalStorageProvider(data_dir)
    destination = get_storage()
    destination.ping()

    logger.info("migrating local files from %s to s3 bucket '%s'", data_dir, settings.STORAGE_S3_BUCKET)
    if dry_run:
        logger.info("dry run: no files will be uploaded")

    uploaded = skipped = excluded = 0
    for entry in source.iter_entries(""):
        if should_skip(entry.key):
            excluded += 1
            continue

        try:
            # already migrated files are skipped, so the script is safe to re-run after a failure
            if destination.head(entry.key).size == entry.size:
                skipped += 1
                continue
        except StorageFileNotFoundError:
            pass

        if dry_run:
            logger.info("would upload %s (%d bytes)", entry.key, entry.size)
        else:
            local_path = source.get_local_path(entry.key)
            if local_path is None:
                raise RuntimeError(f"missing local path for key: {entry.key}")

            content_type, _ = mimetypes.guess_type(entry.key)
            destination.write_file(entry.key, local_path, content_type=content_type)

        uploaded += 1
        if (uploaded + skipped) % PROGRESS_INTERVAL == 0:
            logger.info("processed %d files (%d uploaded, %d skipped)", uploaded + skipped, uploaded, skipped)

    logger.info(
        "migration complete: %d files %s, %d skipped (already present), %d excluded (local-only)",
        uploaded,
        "would be uploaded" if dry_run else "uploaded",
        skipped,
        excluded,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy local Mealie data files (recipe images, assets, avatars, backups, ...) "
        "into the configured S3 object storage"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List the files that would be uploaded without uploading them"
    )
    args = parser.parse_args()

    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
