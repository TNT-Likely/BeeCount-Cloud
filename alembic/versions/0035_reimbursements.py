"""0035 — 报销(Reimbursement) + 报销项(ReimbursementItem)。

Web-first, ledger-scoped, 不进入移动端同步层。

Revision ID: 0035_reimbursements
Revises: 0034_saving_plans
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0035_reimbursements"
down_revision = "0034_saving_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reimbursement_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tx_sync_id", sa.String(36), nullable=False, index=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("category_name", sa.String(128), nullable=True),
        sa.Column("account_id", sa.String(36), nullable=True, index=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("currency_code", sa.String(16), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("reimbursement_id", sa.String(36), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "reimbursements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("receive_account_id", sa.String(36), nullable=True, index=True),
        sa.Column("receive_account_name", sa.String(128), nullable=True),
        sa.Column("tx_sync_id", sa.String(36), nullable=True, index=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reimbursements")
    op.drop_table("reimbursement_items")
