"""shared ledger — ledger_members + ledger_invites

Revision ID: 0010_shared_ledger
Revises: 0009_mcp_call_log_pat_name
Create Date: 2026-05-14

Phase 1 共享账本基础设施:

- 新增 ``ledger_members`` 表:谁能访问哪个账本(role: owner / editor;
  viewer 远期再开)。PK = (ledger_id, user_id),一个用户对一个账本只有一行。
- 新增 ``ledger_invites`` 表:6 位邀请码 + 一次性 + 24h 默认失效。
- 数据迁移:把现有 ``ledgers.user_id`` 写入 ``ledger_members``(role='owner'),
  保证升级后所有现有账本零感知继续工作。

注意:
- ``Ledger.user_id`` 列**保留**作为"原 owner 冗余字段",降低首版风险;
  Phase 2 稳定后再 deprecate(详见 02-data-model.md §1.5)。
- 没动 ``sync_changes`` — 该表已有 ``updated_by_user_id`` 可作创建者/编辑者来源。
"""

import sqlalchemy as sa
from alembic import op


revision = "0010_shared_ledger"
down_revision = "0009_mcp_call_log_pat_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. ledger_members ────────────────────────────────────────────
    op.create_table(
        "ledger_members",
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Phase 1: 'owner' / 'editor';application 层 validate,DB 不加 enum
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "invited_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ledger_members_user_id",
        "ledger_members",
        ["user_id"],
    )
    op.create_index(
        "ix_ledger_members_ledger_id",
        "ledger_members",
        ["ledger_id"],
    )

    # 2. ledger_invites ────────────────────────────────────────────
    op.create_table(
        "ledger_invites",
        # 6 位明文码,字符集 A-Z2-9(排除 O/0/I/1)。熵 ≈ 30^6 ≈ 7 亿。
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "invited_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_role", sa.String(16), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 3. 数据迁移:把现有 ledger.user_id 写入 ledger_members(role='owner')
    #
    # 对每个 ledgers 行生成一行 ledger_members 行。joined_at 用 ledger.created_at
    # 做兜底,如果为空再用 CURRENT_TIMESTAMP。
    op.execute(
        """
        INSERT INTO ledger_members (ledger_id, user_id, role, invited_by, joined_at)
        SELECT id, user_id, 'owner', NULL, COALESCE(created_at, CURRENT_TIMESTAMP)
        FROM ledgers
        """
    )


def downgrade() -> None:
    op.drop_table("ledger_invites")
    op.drop_index("ix_ledger_members_ledger_id", table_name="ledger_members")
    op.drop_index("ix_ledger_members_user_id", table_name="ledger_members")
    op.drop_table("ledger_members")
