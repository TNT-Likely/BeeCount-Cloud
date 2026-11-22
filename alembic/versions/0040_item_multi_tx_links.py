"""0040 — 物品可关联多个交易流水。

背景:此前物品(以及物品的收益/支出流水)关联已有账单时只能选**一笔**交易
(items.tx_sync_id / item_flows.tx_sync_id 单值)。现实中一件物品的购入往往
由多笔账单组成(如在 A 平台付定金、在 B 平台付尾款),需要能一次关联多笔。

本迁移:
- items / item_flows 新增 tx_sync_ids_json —— 关联的**全部**交易流水 syncId
  (JSON 数组)。tx_sync_id 单值列保留:存第一个关联(向后兼容旧客户端/旧数据)。
- 老数据迁移:tx_sync_ids_json = [tx_sync_id](非空时),保证读写一致。

Revision ID: 0040_item_multi_tx_links
Revises: 0039_loan_merged_repayment
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0040_item_multi_tx_links"
down_revision = "0039_loan_merged_repayment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("items", "item_flows"):
        op.add_column(table, sa.Column("tx_sync_ids_json", sa.Text(), nullable=True))
        # 老数据回填:单值 tx_sync_id → 数组
        op.execute(
            f"update {table} set tx_sync_ids_json = json_array(tx_sync_id) "
            "where tx_sync_id is not null and tx_sync_id <> ''"
        )


def downgrade() -> None:
    for table in ("item_flows", "items"):
        op.drop_column(table, "tx_sync_ids_json")
