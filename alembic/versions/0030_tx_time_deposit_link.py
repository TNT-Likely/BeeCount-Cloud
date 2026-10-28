"""add time_deposit_sync_id to read_tx_projection

Revision ID: 0030_tx_time_deposit_link
Revises: 0029_items_holding
Create Date: 2026-10-26

- time_deposit_sync_id:交易可关联到一个定期存款(利息收入按键生成),
  该笔收入金额计入该存款「已收利息」。只存 id,弱引用 —— 存款被删不阻塞
  交易,只是从「已收利息」聚合里消失。镜像 items 的 item_sync_id 列。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030_tx_time_deposit_link"
down_revision = "0029_items_holding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("time_deposit_sync_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "time_deposit_sync_id")
