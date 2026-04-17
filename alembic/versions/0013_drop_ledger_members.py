"""drop ledger_members table (single-user-per-ledger model)

Revision ID: 0013_drop_ledger_members
Revises: 0012_drop_multiuser_tables
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_drop_ledger_members"
down_revision = "0012_drop_multiuser_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop ledger_members entirely: single-user-per-ledger means ownership is
    # already captured by ledgers.user_id. LedgerMember was a multi-user relic.
    op.drop_table("ledger_members")


def downgrade() -> None:
    # Recreate ledger_members; backfill handled by caller if needed.
    op.create_table(
        "ledger_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("ledger_id", "user_id", name="uq_ledger_members"),
    )
    op.create_index("ix_ledger_members_ledger_id", "ledger_members", ["ledger_id"])
    op.create_index("ix_ledger_members_user_id", "ledger_members", ["user_id"])
    op.create_index("ix_ledger_members_role", "ledger_members", ["role"])
    op.create_index("ix_ledger_members_status", "ledger_members", ["status"])
