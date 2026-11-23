"""Persist personal Cloud notification read receipts.

Revision ID: 0022_user_notification_reads
Revises: 0021_mcp_workflows
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_user_notification_reads"
down_revision = "0021_mcp_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notification_reads",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("notification_id", sa.String(128), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("user_notification_reads")
