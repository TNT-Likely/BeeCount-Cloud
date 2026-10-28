"""add alias + billing_day_in_current to user_account_projection

Revision ID: 0027_account_alias_billing_in_current
Revises: 0026_account_card_icon
Create Date: 2026-10-25

- alias: 账户别名(所有账户类型)。账户名是正式名,别名是列表/卡片上想看的
  短名;为空 = 沿用 name。纯展示字段,不参与统计。
- billing_day_in_current: 信用卡出账日(账单日)当天的消费计入哪一期账单。
  true(默认)= 计入当期账单;false = 计入下一期。NOT NULL + server_default
  true —— 既有行升级后保持主流口径(账单日当天入本期)。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027_account_alias_billing_in_current"
down_revision = "0026_account_card_icon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("alias", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_account_projection",
        sa.Column(
            "billing_day_in_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "billing_day_in_current")
    op.drop_column("user_account_projection", "alias")
