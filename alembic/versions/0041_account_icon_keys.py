"""0041 — 账户投影表补齐图标/背景字段。

背景:二次开发给 UserAccountProjection 模型增加了 icon_key / icon_type /
background_key 三个字段(公共图标库键、图标类型、自定义背景键),并在
projection / snapshot_builder / read 链路全面引用。但当初改模型时漏写了
对应的 alembic 迁移,导致"模型有列、实际表无列"—— 查询账户时抛
sqlite3.OperationalError: no such column: user_account_projection.icon_key。

本迁移为 user_account_projection 表补齐这三列(nullable,老数据为 NULL,
前端有兜底逻辑)。用 batch_alter_table 以兼容 SQLite。

Revision ID: 0041_account_icon_keys
Revises: 0040_item_multi_tx_links
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0041_account_icon_keys"
down_revision = "0040_item_multi_tx_links"
branch_labels = None
depends_on = None

TABLE = "user_account_projection"
COLUMNS = (
    ("icon_key", sa.String(64)),
    ("icon_type", sa.String(32)),
    ("background_key", sa.String(64)),
)


def _existing_columns() -> set[str]:
    conn = op.get_bind()
    return {row[1] for row in conn.execute(sa.text(f"PRAGMA table_info({TABLE})"))}


def upgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(TABLE) as batch_op:
        for name, typ in COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(TABLE) as batch_op:
        for name, _ in reversed(COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
