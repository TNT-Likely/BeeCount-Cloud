"""Add account + flow linkage columns to loans / loan_repayments (0027).

借款关联真实账户(扣款/到款)并生成对应流水:loan 与 loan_repayment 各增加
account_id / account_name(选中的真实账户)与 tx_sync_id(自动生成的交易流水 syncId)。
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_loan_account_flow"
down_revision = "0026_contacts_loans"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loans",
        sa.Column("account_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "loans",
        sa.Column("account_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "loans",
        sa.Column("tx_sync_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_loans_account_id", "loans", ["account_id"])
    op.create_index("ix_loans_tx_sync_id", "loans", ["tx_sync_id"])

    op.add_column(
        "loan_repayments",
        sa.Column("account_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "loan_repayments",
        sa.Column("account_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "loan_repayments",
        sa.Column("tx_sync_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_loan_repayments_account_id", "loan_repayments", ["account_id"])
    op.create_index("ix_loan_repayments_tx_sync_id", "loan_repayments", ["tx_sync_id"])


def downgrade():
    op.drop_index("ix_loan_repayments_tx_sync_id", table_name="loan_repayments")
    op.drop_index("ix_loan_repayments_account_id", table_name="loan_repayments")
    op.drop_column("loan_repayments", "tx_sync_id")
    op.drop_column("loan_repayments", "account_name")
    op.drop_column("loan_repayments", "account_id")

    op.drop_index("ix_loans_tx_sync_id", table_name="loans")
    op.drop_index("ix_loans_account_id", table_name="loans")
    op.drop_column("loans", "tx_sync_id")
    op.drop_column("loans", "account_name")
    op.drop_column("loans", "account_id")
