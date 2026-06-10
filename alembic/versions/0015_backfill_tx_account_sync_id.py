"""read_tx_projection: 存量回填 account_sync_id / from_account_sync_id / to_account_sync_id

修复 #41:老 web 写入或前端映射 miss 时,交易投影只有 account_name 没有 account_sync_id,
导致 sync_id 维度的账户统计/过滤漏算。此迁移对「仅有名且名唯一对应一个账户」的行补全
对应 sync_id;同名多账户不处理(宁缺勿错)。

Revision ID: 0015_backfill_tx_account_sync_id
Revises: 0014_ledger_month_start_day
Create Date: 2026-06-10
"""

import sqlalchemy as sa
from alembic import op


revision = "0015_backfill_tx_account_sync_id"
down_revision = "0014_ledger_month_start_day"
branch_labels = None
depends_on = None


# SQLite / PostgreSQL 双兼容:子查询用 MIN()+COUNT()=1 守卫,避免方言 GROUP BY 问题。
# 三组字段各一条 UPDATE,逻辑相同仅列名不同。供测试直接 import 复用。
_BACKFILL_TEMPLATE = """
UPDATE read_tx_projection
SET {id_col} = (
    SELECT MIN(a.sync_id) FROM user_account_projection a
    WHERE a.user_id = read_tx_projection.user_id
      AND a.name = read_tx_projection.{name_col}
)
WHERE {id_col} IS NULL
  AND {name_col} IS NOT NULL
  AND (SELECT COUNT(*) FROM user_account_projection a2
       WHERE a2.user_id = read_tx_projection.user_id
         AND a2.name = read_tx_projection.{name_col}) = 1
"""

BACKFILL_STATEMENTS = [
    _BACKFILL_TEMPLATE.format(id_col="account_sync_id",      name_col="account_name"),
    _BACKFILL_TEMPLATE.format(id_col="from_account_sync_id", name_col="from_account_name"),
    _BACKFILL_TEMPLATE.format(id_col="to_account_sync_id",   name_col="to_account_name"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for stmt in BACKFILL_STATEMENTS:
        result = bind.execute(sa.text(stmt))
        rowcount = getattr(result, "rowcount", None)
        if rowcount is not None:
            print(f"backfill rowcount: {rowcount}")


def downgrade() -> None:
    # 回填不可逆,但补的是本就该有的引用,留存无害。
    pass
