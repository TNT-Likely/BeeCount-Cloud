"""交易退款投影表(0022)

对应 BeeCount 仓 issue #465 的「退款功能」:一笔支出被部分/全额退回时,
挂在这笔原始交易上的退款记录。跟 0021 的定期存款/归物同一条同步链路
(mobile /sync/push → sync_changes → 本投影表,web 读唯一权威源)。

新表无存量数据,纯 CREATE TABLE。

Revision ID: 0022_refunds
Revises: 0021_tx_discount_fee_legs_deposit_return
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op


revision = "0022_refunds"
down_revision = "0021_tx_discount_fee_legs_deposit_return"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_refund_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("original_tx_id", sa.String(255), nullable=False),
        sa.Column("original_amount", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(16), nullable=False, server_default="CNY"),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_refund_projection_user_id",
        "read_refund_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_refund_user_status",
        "read_refund_projection",
        ["user_id", "status"],
    )
    # 累计退款查询(同一原单的非 cancelled 合计)按 (ledger, original_tx_id) 走。
    op.create_index(
        "ix_read_refund_ledger_original_tx",
        "read_refund_projection",
        ["ledger_id", "original_tx_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_read_refund_ledger_original_tx", table_name="read_refund_projection"
    )
    op.drop_index("ix_read_refund_user_status", table_name="read_refund_projection")
    op.drop_index(
        "ix_read_refund_projection_user_id", table_name="read_refund_projection"
    )
    op.drop_table("read_refund_projection")
