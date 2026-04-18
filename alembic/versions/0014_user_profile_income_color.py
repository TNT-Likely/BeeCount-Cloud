"""add user_profiles.income_is_red column

Revision ID: 0014_user_profile_income_color
Revises: 0013_drop_ledger_members
Create Date: 2026-04-18 00:00:00.000000

Mobile 收支颜色方案（`incomeExpenseColorSchemeProvider`）同步到服务端，web 端
只读应用。True = 红色收入 / 绿色支出（mobile 旧默认），False = 红色支出 /
绿色收入。Nullable 兜底已存在的 user_profiles 行，None 视为默认。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_user_profile_income_color"
down_revision = "0013_drop_ledger_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("income_is_red", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "income_is_red")
