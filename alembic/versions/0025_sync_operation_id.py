"""Identify retries independently from a client's wall clock."""

import sqlalchemy as sa
from alembic import op

revision = "0025_sync_operation_id"
down_revision = "0024_operational_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_changes", sa.Column("operation_id", sa.String(64)))
    present = sa.text("operation_id IS NOT NULL")
    op.create_index("uq_sync_change_device_operation", "sync_changes",
                    ["updated_by_device_id", "operation_id"], unique=True,
                    postgresql_where=present, sqlite_where=present)


def downgrade() -> None:
    op.drop_index("uq_sync_change_device_operation", table_name="sync_changes")
    op.drop_column("sync_changes", "operation_id")
