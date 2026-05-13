"""mcp_call_logs — MCP tool 调用审计表

Revision ID: 0008_mcp_call_log
Revises: 0007_personal_access_token
Create Date: 2026-05-13

每次 MCP tool 调用都登记一行,Web 设置页"调用历史"读这张表。隐私设计:
  - 只存 tool_name / status / duration / client_ip 等元数据
  - args_summary 是结构化字段的脱敏摘要(`tx_type=expense, amount=38`),
    note 之类的自由文本不进表
  - 30 天保留,过期由 APScheduler 清

pat_id FK 设 SET NULL —— 用户删 PAT 时历史不被级联删,只是失去到具体 token
的关联(prefix 保留可识别)。
"""

import sqlalchemy as sa
from alembic import op


revision = "0008_mcp_call_log"
down_revision = "0007_personal_access_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_call_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pat_id",
            sa.String(36),
            sa.ForeignKey("personal_access_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pat_prefix", sa.String(32), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("args_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column(
            "called_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_mcp_call_logs_user_id", "mcp_call_logs", ["user_id"])
    op.create_index("ix_mcp_call_logs_pat_id", "mcp_call_logs", ["pat_id"])
    op.create_index("ix_mcp_call_logs_tool_name", "mcp_call_logs", ["tool_name"])
    op.create_index("ix_mcp_call_logs_status", "mcp_call_logs", ["status"])
    op.create_index("ix_mcp_call_logs_called_at", "mcp_call_logs", ["called_at"])
    # 复合:WHERE user_id=? ORDER BY called_at DESC LIMIT N — 列表页主查询
    op.create_index(
        "ix_mcp_call_user_time",
        "mcp_call_logs",
        ["user_id", sa.text("called_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_call_user_time", table_name="mcp_call_logs")
    op.drop_index("ix_mcp_call_logs_called_at", table_name="mcp_call_logs")
    op.drop_index("ix_mcp_call_logs_status", table_name="mcp_call_logs")
    op.drop_index("ix_mcp_call_logs_tool_name", table_name="mcp_call_logs")
    op.drop_index("ix_mcp_call_logs_pat_id", table_name="mcp_call_logs")
    op.drop_index("ix_mcp_call_logs_user_id", table_name="mcp_call_logs")
    op.drop_table("mcp_call_logs")
