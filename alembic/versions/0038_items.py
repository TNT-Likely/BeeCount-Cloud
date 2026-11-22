"""0038 — 物品管理：物品档案 + 物品收益/支出流水。

新增 items（物品）与 item_flows（物品收益/支出流水）两张表。
物品可关联账单（new 新建交易 / existing 已有交易 / none 不关联），
并配置均值计算与目标成本（none/daily_avg/per_day）。

Revision ID: 0038_items
Revises: 0037_reimbursement_companies
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0038_items"
down_revision = "0037_reimbursement_companies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("price", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purchase_channel", sa.String(128), nullable=True),
        sa.Column("category_name", sa.String(128), nullable=True),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("link_mode", sa.String(16), nullable=False, server_default="none"),
        sa.Column("tx_sync_id", sa.String(36), nullable=True),
        sa.Column("account_id", sa.String(36), nullable=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("average_mode", sa.String(16), nullable=False, server_default="none"),
        sa.Column("target_cost_mode", sa.String(16), nullable=False, server_default="none"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("user_id", "ledger_id", "purchased_at", "category_name", "link_mode", "tx_sync_id", "account_id"):
        op.create_index(f"ix_items_{col}", "items", [col])

    op.create_table(
        "item_flows",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("flow_type", sa.String(16), nullable=False, server_default="expense"),
        sa.Column("amount", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category_name", sa.String(128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("link_mode", sa.String(16), nullable=False, server_default="none"),
        sa.Column("tx_sync_id", sa.String(36), nullable=True),
        sa.Column("account_id", sa.String(36), nullable=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("user_id", "ledger_id", "item_id", "flow_type", "happened_at", "category_name", "link_mode", "tx_sync_id", "account_id"):
        op.create_index(f"ix_item_flows_{col}", "item_flows", [col])


def downgrade() -> None:
    op.drop_table("item_flows")
    op.drop_table("items")
