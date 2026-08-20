"""relativize group data export paths

Revision ID: ace46d42ed7b
Revises: 2187537c52b8
Create Date: 2026-08-19 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "ace46d42ed7b"
down_revision: str | None = "2187537c52b8"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

group_data_exports = sa.table("group_data_exports", sa.column("id"), sa.column("path"))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(group_data_exports.c.id, group_data_exports.c.path)).fetchall()

    for row_id, path in rows:
        if not path:
            continue

        index = path.rfind("groups/")
        if index <= 0:
            continue

        bind.execute(group_data_exports.update().where(group_data_exports.c.id == row_id).values(path=path[index:]))


def downgrade() -> None:
    pass
