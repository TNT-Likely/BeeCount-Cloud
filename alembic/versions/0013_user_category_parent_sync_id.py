"""user_category_projection: parent_sync_id 列 — 共享账本二级分类父子关系

Revision ID: 0013_user_category_parent_sync_id
Revises: 0012_shared_ledger
Create Date: 2026-05-19

共享账本 Phase 2 bugfix:UserCategoryProjection 只用 parent_name 关联父子,
mobile/web Editor 端无法精确建立 level=2 子分类到 level=1 父分类的稳定
引用(同名 + 重命名都坏)。加 parent_sync_id 列存 owner 的 level=1 行
syncId,迁移现有 level=2 行按 (user_id, name='parent_name', kind, level=1)
反查回填。
"""

import sqlalchemy as sa
from alembic import op


revision = "0013_user_category_parent_sync_id"
down_revision = "0012_shared_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_category_projection",
        sa.Column("parent_sync_id", sa.String(255), nullable=True),
    )

    # 数据回填:对每个 level=2 行,在同 user_id 内按 (parent_name + kind + level=1)
    # 反查 parent 的 sync_id,填进 parent_sync_id 列。同名父分类不会发生(snapshot
    # mutator 同 kind 内有同名查重),所以一对一匹配。
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
          AND child.parent_sync_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("user_category_projection", "parent_sync_id")
