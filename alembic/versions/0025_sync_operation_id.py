"""Identify retries independently from a client's wall clock."""

import sqlalchemy as sa
from alembic import op

revision = "0025_sync_operation_id"
down_revision = "0024_operational_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_operation_receipts",
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("operation_id", sa.String(64), primary_key=True),
        sa.Column("change_id", sa.BigInteger(), nullable=False),
        sa.Column("ledger_id", sa.String(36), nullable=True),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_sync_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sync_operation_receipts")
