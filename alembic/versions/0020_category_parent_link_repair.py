"""user_category_projection: 父子链接存量修复(#101)

#101 的历史遗留:父分类改名从未级联子分类的 parent_name,而 Web 全链路按
parent_name 分组/守卫。0013 加了 parent_sync_id 稳定 FK 并做过一次按名回填,
但此后发生的改名仍会留下两类坏行:

  ① parent_sync_id 有效、parent_name 悬空(改过名的)—— 按 FK 修名字;
  ② parent_sync_id 为 NULL、parent_name 仍可解析(0013 之后新建又没触发
     upsert 兜底的)—— 按名补 FK。

两类都修不了的名字彻底悬空行(①②条件都不满足)只能用户手动重挂,保持原样。

Revision ID: 0020_category_parent_link_repair
Revises: 0019_account_hidden
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op


revision = "0020_category_parent_link_repair"
down_revision = "0019_account_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ① FK 权威:parent_sync_id 指向的父行存在、名字对不上 → 用父行现名修
    #    parent_name。条件显式 EXISTS,父行已被删的孤儿行不动(交给用户重挂)。
    op.execute(
        """
        UPDATE user_category_projection AS child
        SET parent_name = (
            SELECT parent.name
            FROM user_category_projection AS parent
            WHERE parent.user_id = child.user_id
              AND parent.sync_id = child.parent_sync_id
            LIMIT 1
        )
        WHERE child.parent_sync_id IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM user_category_projection AS parent
            WHERE parent.user_id = child.user_id
              AND parent.sync_id = child.parent_sync_id
          )
          AND (child.parent_name IS NULL
               OR child.parent_name != (
                   SELECT parent.name
                   FROM user_category_projection AS parent
                   WHERE parent.user_id = child.user_id
                     AND parent.sync_id = child.parent_sync_id
                   LIMIT 1
               ))
        """
    )

    # ② 按名补 FK(与 0013 同款反查,只碰 parent_sync_id IS NULL 的行):
    #    同 kind 内同名 level=1 父分类不存在(snapshot mutator 有同名查重),
    #    所以一对一匹配;解析不了的悬空名保持 NULL。
    op.execute(
        """
        UPDATE user_category_projection AS child
        SET parent_sync_id = (
            SELECT parent.sync_id
            FROM user_category_projection AS parent
            WHERE parent.user_id = child.user_id
              AND parent.name = child.parent_name
              AND parent.kind = child.kind
              AND COALESCE(parent.level, 1) = 1
            LIMIT 1
        )
        WHERE COALESCE(child.level, 1) >= 2
          AND child.parent_name IS NOT NULL
          AND child.parent_name != ''
          AND child.parent_sync_id IS NULL
        """
    )


def downgrade() -> None:
    # 数据修复迁移,无 schema 变更,无操作。
    pass
