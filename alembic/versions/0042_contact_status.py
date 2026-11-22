"""0042 — 联系人增加归档 / 停用状态。

给 contacts 表补两列：
- is_archived: 归档。数据保留,但默认列表 / 借款建议 / 排行榜隐藏,可恢复。
- is_active:   停用。列表仍可见(带标记),禁止新增借款与新关联引用,可启用。

两列互相独立,默认 False / True(老数据等价于「活跃未归档」)。
用 batch_alter_table 以兼容 SQLite,并以 PRAGMA 检查保证幂等。

Revision ID: 0042_contact_status
Revises: 0041_account_icon_keys
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0042_contact_status"
down_revision = "0041_account_icon_keys"
branch_labels = None
depends_on = None

TABLE = "contacts"
COLUMNS = (
    ("is_archived", sa.Boolean(), sa.false()),
    ("is_active", sa.Boolean(), sa.true()),
)


def _existing_columns() -> set[str]:
    conn = op.get_bind()
    return {row[1] for row in conn.execute(sa.text(f"PRAGMA table_info({TABLE})"))}


def upgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(TABLE) as batch_op:
        for name, typ, server_default in COLUMNS:
            if name not in existing:
                batch_op.add_column(
                    sa.Column(name, typ, nullable=False, server_default=server_default)
                )


def downgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(TABLE) as batch_op:
        for name, _ in reversed(COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
