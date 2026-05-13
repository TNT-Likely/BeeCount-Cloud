"""mcp_call_logs.pat_name — 调用时缓存 PAT 名字

Revision ID: 0009_mcp_call_log_pat_name
Revises: 0008_mcp_call_log
Create Date: 2026-05-13

历史行用 pat_prefix(`bcmcp_xxx…`)对用户太抽象,加一列 pat_name 缓存当时
PAT 的 user-given 名字(如 "Claude Desktop")。denormalize:即便日后 PAT
改名或被删,历史里仍能看到"这次是 Claude Desktop 调的"。
"""

import sqlalchemy as sa
from alembic import op


revision = "0009_mcp_call_log_pat_name"
down_revision = "0008_mcp_call_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_call_logs") as batch:
        batch.add_column(sa.Column("pat_name", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mcp_call_logs") as batch:
        batch.drop_column("pat_name")
