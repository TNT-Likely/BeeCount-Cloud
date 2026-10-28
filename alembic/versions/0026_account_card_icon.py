"""add card_number + icon to user_account_projection

Revision ID: 0026_account_card_icon
Revises: 0025_refund_split
Create Date: 2026-10-25

- card_number: 完整卡号(用户手动录入;card_last_four 可由后四位派生)
- icon: 账户图标 key(内置银行/支付图标注册表主键,如 'icbc');空 = 按类型回退
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026_account_card_icon"
down_revision = "0025_refund_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("card_number", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "user_account_projection",
        sa.Column("icon", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "icon")
    op.drop_column("user_account_projection", "card_number")
