"""dashboard_layout: user_profiles.dashboard_layout_json

Web 首頁卡片版面(顯示/隱藏 + 順序),跨裝置同步。新欄位 nullable,不需要 backfill。

Revision ID: 0062_dashboard_layout
Revises: 0061_security_data_source_config
Create Date: 2026-10-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0062_dashboard_layout"
down_revision = "0061_security_data_source_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_profiles") as batch:
        batch.add_column(sa.Column("dashboard_layout_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_profiles") as batch:
        batch.drop_column("dashboard_layout_json")
