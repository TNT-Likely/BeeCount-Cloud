"""personal_access_tokens — 长期 token 给 MCP / 外部 LLM 客户端用

Revision ID: 0007_personal_access_token
Revises: 0006_attachment_kind
Create Date: 2026-05-13

新建 personal_access_tokens 表:
  - token_hash:sha256 hash,unique index 防碰撞
  - prefix:前 16 字符明文(`bcmcp_a1b2c3d4`),列表展示
  - scopes_json:JSON 数组(`["mcp:read", "mcp:write"]`)
  - expires_at / revoked_at:可空,前者用户自选过期(null = 永不),后者软删
  - last_used_at / last_used_ip:每次成功 auth 异步更新,异常监控用

复合索引 ix_pat_user_active:`(user_id, revoked_at)` 加速"我这个 user 当前
有效 PAT 列表"查询。

详见 .docs/mcp-server-design.md Sprint 1。
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_personal_access_token"
down_revision = "0006_attachment_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("prefix", sa.String(32), nullable=False, index=True),
        sa.Column("scopes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_pat_user_active",
        "personal_access_tokens",
        ["user_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pat_user_active", table_name="personal_access_tokens")
    op.drop_table("personal_access_tokens")
