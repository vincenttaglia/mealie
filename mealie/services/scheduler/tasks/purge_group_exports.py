import datetime

from sqlalchemy import cast, select

from mealie.core import root_logger
from mealie.core.config import get_storage
from mealie.db.db_setup import session_context
from mealie.db.models._model_utils.datetime import NaiveDateTime
from mealie.db.models.group.exports import GroupDataExportsModel
from mealie.services.exporter import resolve_export_storage_key

ONE_DAY_AS_MINUTES = 1440


def purge_group_data_exports(max_minutes_old=ONE_DAY_AS_MINUTES):
    """Purges all group exports after x days"""
    logger = root_logger.get_logger()

    logger.debug("purging group data exports")
    limit = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=max_minutes_old)

    storage = get_storage()

    with session_context() as session:
        stmt = select(GroupDataExportsModel).filter(cast(GroupDataExportsModel.expires, NaiveDateTime) <= limit)
        results = session.execute(stmt).scalars().all()

        total_removed = 0
        for result in results:
            session.delete(result)
            storage.delete(resolve_export_storage_key(result.path), missing_ok=True)
            total_removed += 1

        session.commit()

        logger.info(f"finished purging group data exports. {total_removed} exports removed from group data")


def purge_excess_files() -> None:
    """Purges all group export files that are older than 2 days"""
    logger = root_logger.get_logger()
    storage = get_storage()

    limit = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=ONE_DAY_AS_MINUTES * 2)

    for entry in storage.iter_entries("groups/"):
        if "/export/" not in entry.key or not entry.key.endswith(".zip"):
            continue

        if entry.modified is not None and entry.modified < limit:
            storage.delete(entry.key, missing_ok=True)
            logger.debug(f"excess group file removed '{entry.key}'")

    logger.info("finished purging excess files")
