"""user_account_projection: hidden — 账户隐藏

设计见 BeeCount 仓 .docs/account-archive/03-tech-design-cloud.md。
账户是 user-global 实体,hidden 只影响前端「选择器 / 主列表呈现」,不参与任何
服务端统计口径(D1:隐藏账户仍进净资产 / 资产 / 收支聚合,服务端不加过滤)。

既有行升级后 hidden=false(server_default),旧 App 不发该字段时保持默认。

Revision ID: 0019_account_hidden
Revises: 0018_tx_multi_currency
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op


revision = "0019_account_hidden"
down_revision = "0018_tx_multi_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "hidden")
