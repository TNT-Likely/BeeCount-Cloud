"""借款 / 报销 投影表(0023)

对应「借款」「报销」两个新能力:个人记账里的借入/借出跟踪,以及先垫付后
找人/公司报销的跟踪。跟 0021(定期存款/归物)、0022(退款)同一条同步链路
(mobile /sync/push → sync_changes → 本投影表,web 读唯一权威源)。

新表无存量数据,纯 CREATE TABLE。

Revision ID: 0023_loans_reimbursements
Revises: 0022_refunds
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op


revision = "0023_loans_reimbursements"
down_revision = "0022_refunds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_loan_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False, server_default="borrowed"),
        sa.Column("counterparty", sa.Text(), nullable=False, server_default=""),
        sa.Column("principal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(16), nullable=False, server_default="CNY"),
        sa.Column("interest_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repaid_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_loan_projection_user_id", "read_loan_projection", ["user_id"]
    )
    op.create_index(
        "ix_read_loan_user_status", "read_loan_projection", ["user_id", "status"]
    )

    op.create_table(
        "read_reimbursement_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("payer", sa.Text(), nullable=True),
        sa.Column("reimburser", sa.Text(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(16), nullable=False, server_default="CNY"),
        sa.Column("category_sync_id", sa.String(255), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=True),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("related_tx_id", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_reimbursement_projection_user_id",
        "read_reimbursement_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_reimbursement_user_status",
        "read_reimbursement_projection",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_read_reimbursement_user_status",
        table_name="read_reimbursement_projection",
    )
    op.drop_index(
        "ix_read_reimbursement_projection_user_id",
        table_name="read_reimbursement_projection",
    )
    op.drop_table("read_reimbursement_projection")

    op.drop_index("ix_read_loan_user_status", table_name="read_loan_projection")
    op.drop_index(
        "ix_read_loan_projection_user_id", table_name="read_loan_projection"
    )
    op.drop_table("read_loan_projection")
