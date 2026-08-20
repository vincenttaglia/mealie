import datetime
import zipfile
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from mealie.core.config import determine_data_dir, get_storage
from mealie.pkgs.stats.fs_stats import pretty_size
from mealie.repos.all_repositories import AllRepositories
from mealie.schema.group.group_exports import GroupDataExport
from mealie.schema.user import GroupInDB

from .._base_service import BaseService
from ._abc_exporter import ABCExporter


def resolve_export_storage_key(path: str) -> str:
    """
    Returns the storage key for a group data export's stored path, relativizing
    legacy rows that hold absolute filesystem paths.
    """
    if not Path(path).is_absolute():
        return path

    try:
        return Path(path).relative_to(determine_data_dir()).as_posix()
    except ValueError:
        posix = PurePosixPath(path).as_posix()
        index = posix.rfind("groups/")
        if index != -1:
            return posix[index:]
        return path


class Exporter(BaseService):
    def __init__(self, group_id: UUID, temp_zip: Path, exporters: list[ABCExporter]) -> None:
        super().__init__()

        self.group_id = group_id
        self.temp_path = temp_zip
        self.exporters = exporters

    def run(self, db: AllRepositories) -> GroupDataExport:
        # Create Zip File
        self.temp_path.touch()

        # Open Zip File
        with zipfile.ZipFile(self.temp_path, "w") as zip:
            for exporter in self.exporters:
                exporter.export(zip)

        export_id = uuid4()

        export_key = f"{GroupInDB.export_prefix(self.group_id)}{export_id}.zip"
        export_size = self.temp_path.stat().st_size

        get_storage().write_file(export_key, self.temp_path, content_type="application/zip")

        group_data_export = GroupDataExport(
            id=export_id,
            group_id=self.group_id,
            path=export_key,
            name="Data Export",
            size=pretty_size(export_size),
            filename=f"{export_id}.zip",
            expires=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1),
        )

        db.group_exports.create(group_data_export)

        return group_data_export
