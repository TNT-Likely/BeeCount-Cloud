"""add read_item_projection(归物 = 物品持有管理) + tx.item_sync_id

Revision ID: 0029_items_holding
Revises: 0028_time_deposit_payout_plans
Create Date: 2026-10-26

「归物」重新定义为**物品持有管理**:记录一件物品的购买日期与金额,按存活
时间算日均成本,跟踪保修,直到二手卖出或报废才停止记录。

read_item_projection
--------------------
一件物品一行。核心是「成本随时间摊薄」:

- purchase_date / purchase_amount:购买日期与金额(账本本位币)。
- warranty_until:保修到期日。到期只是**过保**,物品仍然存活 —— 过保不改
  status,由读端点按今天派生 warranty_status('active' / 'expired' / 'none')。
- status 'in_use'(在用) | 'disposed'(已处置)。只有二手卖出(sold)或
  报废(scrapped)才转 disposed —— 语义是「不再持有」,不是「不再保修」。
- disposal_date / disposal_amount:处置日期与二手卖出收入(scrapped 时无收入)。

派生态(持有天数 / 累计成本 / 日均成本 / 保修状态)**不落库**:跟定期存款的
expected_interest 同款口径,读端点按今天现算,避免后台任务刷状态,也避免
「今天」被写死成历史值。处置后天数是**定格**在 disposal_date 的(见
entity_extensions.item_derived)。

read_tx_projection.item_sync_id
-------------------------------
交易挂物品的弱引用(存 sync_id 字符串,不设 FK):记一笔支出时可选关联到
某件物品,该笔金额计入物品累计成本(维护、配件、延保……)。物品被删不阻塞
交易;交易被删只是从物品成本里消失,不级联。

跟 category_sync_id 同款:改名只刷 *_name 冗余列,这里物品名不冗余到交易
(列表展示时按 sync_id 现查),避免物品改名要回头刷全部交易。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_items_holding"
down_revision = "0028_time_deposit_payout_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_item_projection",
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purchase_amount", sa.Float(), nullable=True),
        sa.Column("warranty_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("disposal_type", sa.String(length=16), nullable=True),
        sa.Column("disposal_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposal_amount", sa.Float(), nullable=True),
        sa.Column("category_sync_id", sa.String(length=255), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=True),
        sa.Column("account_sync_id", sa.String(length=255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_item_user_status",
        "read_item_projection",
        ["user_id", "status"],
    )

    op.add_column(
        "read_tx_projection",
        sa.Column("item_sync_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_read_tx_ledger_item",
        "read_tx_projection",
        ["ledger_id", "item_sync_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_tx_ledger_item", table_name="read_tx_projection")
    op.drop_column("read_tx_projection", "item_sync_id")
    op.drop_index("ix_read_item_user_status", table_name="read_item_projection")
    op.drop_table("read_item_projection")
