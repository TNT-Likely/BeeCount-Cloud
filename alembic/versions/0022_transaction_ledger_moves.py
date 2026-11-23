"""Persist cross-ledger move receipts and retired source identities."""

import sqlalchemy as sa
from alembic import op

revision = "0022_transaction_ledger_moves"
down_revision = "0021_mcp_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transaction_move_receipts",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "transaction_move_retirements",
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
    )


def downgrade():
    op.drop_table("transaction_move_retirements")
    op.drop_table("transaction_move_receipts")
