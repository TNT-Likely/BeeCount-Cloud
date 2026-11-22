"""0033 — 借款还款增加利息字段与利息流水关联。

- loan_repayments 新增 interest_amount / interest_account_id / interest_account_name / interest_tx_sync_id
  用于记录还款时收/付的利息及其对应的收入/支出流水(分类「借款利息」，不计入收支统计)。

Revision ID: 0033_loan_repayment_interest
Revises: 17ed35b941fe
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0033_loan_repayment_interest"
down_revision = "17ed35b941fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loan_repayments", sa.Column("interest_amount", sa.Float(), nullable=True))
    op.add_column("loan_repayments", sa.Column("interest_account_id", sa.String(36), nullable=True))
    op.add_column("loan_repayments", sa.Column("interest_account_name", sa.String(128), nullable=True))
    op.add_column("loan_repayments", sa.Column("interest_tx_sync_id", sa.String(36), nullable=True))
    op.create_index("ix_loan_repayments_interest_account_id", "loan_repayments", ["interest_account_id"])
    op.create_index("ix_loan_repayments_interest_tx_sync_id", "loan_repayments", ["interest_tx_sync_id"])


def downgrade() -> None:
    op.drop_index("ix_loan_repayments_interest_tx_sync_id", table_name="loan_repayments")
    op.drop_index("ix_loan_repayments_interest_account_id", table_name="loan_repayments")
    op.drop_column("loan_repayments", "interest_tx_sync_id")
    op.drop_column("loan_repayments", "interest_account_name")
    op.drop_column("loan_repayments", "interest_account_id")
    op.drop_column("loan_repayments", "interest_amount")
