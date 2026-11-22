"""0034 — 储蓄罐(SavingPlan) + 存款记录(SavingContribution)。

Web-first, ledger-scoped, 不进入移动端同步层。

Revision ID: 0034_saving_plans
Revises: 0033_loan_repayment_interest
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0034_saving_plans"
down_revision = "0033_loan_repayment_interest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saving_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("target_amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("currency_code", sa.String(16), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("account_id", sa.String(36), nullable=True, index=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("plan_type", sa.String(16), nullable=False, server_default="free"),
        sa.Column("monthly_amount", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active", index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "saving_contributions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("saving_plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("tx_sync_id", sa.String(36), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("saving_contributions")
    op.drop_table("saving_plans")