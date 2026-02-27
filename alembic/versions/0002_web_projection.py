"""web projection tables

Revision ID: 0002_web_projection
Revises: 0001_init
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_web_projection"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_ledger_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ledger_external_id", sa.String(length=128), nullable=False),
        sa.Column("ledger_name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("income_total", sa.Float(), nullable=False),
        sa.Column("expense_total", sa.Float(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "ledger_external_id", name="uq_web_ledger_projection"),
    )
    op.create_index(
        "ix_web_ledger_projection_user_id",
        "web_ledger_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_ledger_projection_ledger_external_id",
        "web_ledger_projection",
        ["ledger_external_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_ledger_projection_source_change_id",
        "web_ledger_projection",
        ["source_change_id"],
        unique=False,
    )

    op.create_table(
        "web_transaction_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ledger_external_id", sa.String(length=128), nullable=False),
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
        sa.UniqueConstraint(
            "user_id",
            "ledger_external_id",
            "tx_index",
            name="uq_web_transaction_projection",
        ),
    )
    op.create_index(
        "ix_web_transaction_projection_user_id",
        "web_transaction_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_transaction_projection_ledger_external_id",
        "web_transaction_projection",
        ["ledger_external_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_transaction_projection_tx_type",
        "web_transaction_projection",
        ["tx_type"],
        unique=False,
    )
    op.create_index(
        "ix_web_transaction_projection_happened_at",
        "web_transaction_projection",
        ["happened_at"],
        unique=False,
    )
    op.create_index(
        "ix_web_transaction_projection_category_name",
        "web_transaction_projection",
        ["category_name"],
        unique=False,
    )
    op.create_index(
        "ix_web_transaction_projection_category_kind",
        "web_transaction_projection",
        ["category_kind"],
        unique=False,
    )
    op.create_index(
        "idx_web_tx_projection_ledger_happened",
        "web_transaction_projection",
        ["user_id", "ledger_external_id", "happened_at"],
        unique=False,
    )

    op.create_table(
        "web_account_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ledger_external_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "ledger_external_id",
            "name",
            name="uq_web_account_projection",
        ),
    )
    op.create_index(
        "ix_web_account_projection_user_id",
        "web_account_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_account_projection_ledger_external_id",
        "web_account_projection",
        ["ledger_external_id"],
        unique=False,
    )

    op.create_table(
        "web_category_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ledger_external_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("icon_type", sa.String(length=32), nullable=True),
        sa.Column("parent_name", sa.String(length=255), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "ledger_external_id",
            "kind",
            "name",
            name="uq_web_category_projection",
        ),
    )
    op.create_index(
        "ix_web_category_projection_user_id",
        "web_category_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_category_projection_ledger_external_id",
        "web_category_projection",
        ["ledger_external_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_category_projection_kind",
        "web_category_projection",
        ["kind"],
        unique=False,
    )

    op.create_table(
        "web_tag_projection",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ledger_external_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "ledger_external_id",
            "name",
            name="uq_web_tag_projection",
        ),
    )
    op.create_index(
        "ix_web_tag_projection_user_id",
        "web_tag_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_web_tag_projection_ledger_external_id",
        "web_tag_projection",
        ["ledger_external_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("web_tag_projection")
    op.drop_table("web_category_projection")
    op.drop_table("web_account_projection")
    op.drop_table("web_transaction_projection")
    op.drop_table("web_ledger_projection")
