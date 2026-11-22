"""0043 — 用户级锁账配置。

给 users 表增加 is_locked / lock_date 两列：
- is_locked: 锁账总开关(默认关闭)。
- lock_date: 销账/关账截止日期(当天及之前的交易冻结)。

所有写交易与改账户初始金额的路径在 projection 层统一校验这两个字段,
因此历史数据与未来业务(借款/储蓄罐/报销/物品/定期/import/sync)一并覆盖。

Revision ID: 0043_user_lock_settings
Revises: 0042_contact_status
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0043_user_lock_settings"
down_revision = "0042_contact_status"
branch_labels = None
depends_on = None

TABLE = "users"


def _existing_columns() -> set[str]:
    conn = op.get_bind()
    return {row[1] for row in conn.execute(sa.text(f"PRAGMA table_info({TABLE})"))}


def upgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(TABLE) as batch_op:
        if "is_locked" not in existing:
            batch_op.add_column(
                sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false())
            )
        if "lock_date" not in existing:
            batch_op.add_column(sa.Column("lock_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(TABLE) as batch_op:
        if "lock_date" in existing:
            batch_op.drop_column("lock_date")
        if "is_locked" in existing:
            batch_op.drop_column("is_locked")
