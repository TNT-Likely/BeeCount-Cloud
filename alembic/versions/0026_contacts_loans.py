"""Add contacts, loans and loan_repayments tables (web-first feature).

Revision ID: 0026_contacts_loans
Revises: 0025_refunds
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_contacts_loans"
down_revision = "0025_refunds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("avatar_file_id", sa.String(length=128), nullable=True),
        sa.Column("contact_tag", sa.String(length=32), nullable=True, index=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("wechat", sa.String(length=128), nullable=True),
        sa.Column("alipay", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "loans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "contact_id",
            sa.String(length=36),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("direction", sa.String(length=16), nullable=False, index=True),
        sa.Column("amount", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("currency_code", sa.String(length=16), nullable=True),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interest_type", sa.String(length=16), server_default="none", nullable=False),
        sa.Column("interest_rate", sa.Float(), nullable=True),
        sa.Column("interest_amount", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("voucher_file_ids_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="unpaid", nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "loan_repayments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "ledger_id",
            sa.String(length=36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "loan_id",
            sa.String(length=36),
            sa.ForeignKey("loans.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("repaid_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_table("loan_repayments")
    op.drop_table("loans")
    op.drop_table("contacts")
