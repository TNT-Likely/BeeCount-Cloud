"""attachment_files: add attachment_kind + ledger_id nullable

Revision ID: 0006_attachment_kind
Revises: 0005_2fa_totp
Create Date: 2026-05-12

让 attachment_files 支持区分两类附件:
  - transaction (默认) : 交易附件,挂在某个 ledger 下
  - category_icon      : 分类自定义图标,user-global,ledger_id 为 NULL

变更:
  1. ADD COLUMN attachment_kind VARCHAR(32) NOT NULL DEFAULT 'transaction'
  2. ALTER COLUMN ledger_id DROP NOT NULL (SQLite 需要 batch_alter_table)
  3. Backfill: 把 read_category_projection.icon_cloud_file_id 反向引用过的
     attachment_files 行标记为 'category_icon'。这是数据完整性回填,不算
     "清理脏数据" —— 让统计口径(tx 附件 vs 分类图标)从一开始就分得清。

不做:
  - 不迁移已有 category_icon 的物理存储路径(允许新老路径并存)
  - 不动 transaction kind 行的 ledger_id 限制(它们必须有 ledger_id)

详见 .docs 中的"三件事打包"修复方案。
"""

import sqlalchemy as sa
from alembic import op


revision = "0006_attachment_kind"
down_revision = "0005_2fa_totp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite 不支持直接 ALTER COLUMN; 用 batch_alter_table 重建表实现:
    #   1) ledger_id 改 nullable
    #   2) 新增 attachment_kind 列
    with op.batch_alter_table("attachment_files") as batch_op:
        batch_op.alter_column("ledger_id", existing_type=sa.String(36), nullable=True)
        batch_op.add_column(
            sa.Column(
                "attachment_kind",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'transaction'"),
            )
        )

    # Backfill: 已被分类图标引用的 attachment_files 行标记为 category_icon。
    # 不使用 sub-query 的 SQLite 写法,UPDATE FROM 在老版本 SQLite 不支持,
    # 这里用 IN(SELECT ...) 通用语法。
    op.execute(
        """
        UPDATE attachment_files
        SET attachment_kind = 'category_icon'
        WHERE id IN (
            SELECT DISTINCT icon_cloud_file_id
            FROM read_category_projection
            WHERE icon_cloud_file_id IS NOT NULL
              AND icon_cloud_file_id != ''
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("attachment_files") as batch_op:
        batch_op.drop_column("attachment_kind")
        # 注意:downgrade 不会重新填上 ledger_id 为 NULL 的行 —— 如果存在
        # category_icon 数据,downgrade 会破坏完整性。0006 升级后不建议回退。
        batch_op.alter_column("ledger_id", existing_type=sa.String(36), nullable=False)
