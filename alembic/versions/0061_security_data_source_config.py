"""security_data_source_config: 股票報價/除息資料來源設定(Phase 3)

單例表(id=1),管理者在後台選 free / twelvedata 並填 API key(加密存放)。

Revision ID: 0061_security_data_source_config
Revises: 0060_recurring_stock_dca
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0061_security_data_source_config"
down_revision = "0060_recurring_stock_dca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_data_source_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="free"),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_error", sa.String(1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("security_data_source_config")
