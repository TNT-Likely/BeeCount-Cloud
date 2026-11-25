"""user_account_projection: exclude_from_total — 账户不计入总资产

BeeCount issue #651。开启后该账户余额不计入净资产/总资产/总负债/资产构成/
净值趋势;账户本身照常记账、转账、显示余额。与 hidden 相互独立。

既有行升级后 exclude_from_total=false(server_default),旧 App 不发该字段时保持默认。

Revision ID: 0025_account_exclude_from_total
Revises: 0024_operational_notices
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_account_exclude_from_total"
down_revision = "0024_operational_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column(
            "exclude_from_total", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "exclude_from_total")
