"""交易标记:read_tx_projection.exclude_from_stats / exclude_from_budget

Revision ID: 0017_transaction_exclude_flags
Revises: 0016_multi_currency
Create Date: 2026-06-18
"""

import sqlalchemy as sa
from alembic import op


revision = "0017_transaction_exclude_flags"
down_revision = "0016_multi_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("exclude_from_stats", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("exclude_from_budget", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "exclude_from_budget")
    op.drop_column("read_tx_projection", "exclude_from_stats")
