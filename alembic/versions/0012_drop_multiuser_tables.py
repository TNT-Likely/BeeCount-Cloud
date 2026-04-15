"""drop multi-user share and web projection tables

Revision ID: 0012_drop_multiuser_tables
Revises: 0011_user_profiles
Create Date: 2026-04-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_drop_multiuser_tables"
down_revision = "0011_user_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop ledger_invites (multi-user share feature removed)
    op.drop_table("ledger_invites")

    # Drop all web projection tables (read queries now use SyncChange snapshots)
    op.drop_table("web_tag_projection")
    op.drop_table("web_category_projection")
    op.drop_table("web_account_projection")
    op.drop_table("web_transaction_projection")
    op.drop_table("web_ledger_projection")


def downgrade() -> None:
    # Recreate ledger_invites
    op.create_table(
        "ledger_invites",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ledger_invites_code_hash", "ledger_invites", ["code_hash"], unique=True)
    op.create_index("ix_ledger_invites_ledger_id", "ledger_invites", ["ledger_id"], unique=False)
    op.create_index("ix_ledger_invites_expires_at", "ledger_invites", ["expires_at"], unique=False)

    # Recreate web projection tables (ledger-keyed version from 0003)
    op.create_table(
        "web_ledger_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ledger_name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("income_total", sa.Float(), nullable=False),
        sa.Column("expense_total", sa.Float(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ledger_id", name="uq_web_ledger_projection"),
    )
    op.create_index("ix_web_ledger_projection_ledger_id", "web_ledger_projection", ["ledger_id"])
    op.create_index(
        "ix_web_ledger_projection_source_change_id",
        "web_ledger_projection",
        ["source_change_id"],
    )

    op.create_table(
        "web_transaction_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tx_index", sa.Integer(), nullable=False),
        sa.Column("tx_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("category_name", sa.String(length=255), nullable=True),
        sa.Column("category_kind", sa.String(length=32), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("from_account_name", sa.String(length=255), nullable=True),
        sa.Column("to_account_name", sa.String(length=255), nullable=True),
        sa.Column("tags", sa.String(length=1024), nullable=True),
        sa.Column("attachments_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint("ledger_id", "tx_index", name="uq_web_transaction_projection"),
    )
    op.create_index(
        "ix_web_transaction_projection_ledger_id",
        "web_transaction_projection",
        ["ledger_id"],
    )
    op.create_index(
        "ix_web_transaction_projection_tx_type",
        "web_transaction_projection",
        ["tx_type"],
    )
    op.create_index(
        "ix_web_transaction_projection_happened_at",
        "web_transaction_projection",
        ["happened_at"],
    )
    op.create_index(
        "ix_web_transaction_projection_category_name",
        "web_transaction_projection",
        ["category_name"],
    )
    op.create_index(
        "ix_web_transaction_projection_category_kind",
        "web_transaction_projection",
        ["category_kind"],
    )
    op.create_index(
        "idx_web_tx_projection_ledger_happened",
        "web_transaction_projection",
        ["ledger_id", "happened_at"],
    )

    op.create_table(
        "web_account_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.UniqueConstraint("ledger_id", "name", name="uq_web_account_projection"),
    )
    op.create_index(
        "ix_web_account_projection_ledger_id", "web_account_projection", ["ledger_id"]
    )

    op.create_table(
        "web_category_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("icon_type", sa.String(length=32), nullable=True),
        sa.Column("parent_name", sa.String(length=255), nullable=True),
        sa.UniqueConstraint("ledger_id", "kind", "name", name="uq_web_category_projection"),
    )
    op.create_index(
        "ix_web_category_projection_ledger_id", "web_category_projection", ["ledger_id"]
    )
    op.create_index("ix_web_category_projection_kind", "web_category_projection", ["kind"])

    op.create_table(
        "web_tag_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("ledger_id", "name", name="uq_web_tag_projection"),
    )
    op.create_index("ix_web_tag_projection_ledger_id", "web_tag_projection", ["ledger_id"])
