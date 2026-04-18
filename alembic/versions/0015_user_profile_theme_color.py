"""add user_profiles.theme_primary_color column

Revision ID: 0015_user_profile_theme_color
Revises: 0014_user_profile_income_color
Create Date: 2026-04-18 12:00:00.000000

Mobile 主题色推送到 server，web 把它当作"初始偏好"。web 用户本地改色会写
localStorage 优先生效。Nullable 保留给没推过的用户。格式固定 `#RRGGBB`。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_user_profile_theme_color"
down_revision = "0014_user_profile_income_color"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("theme_primary_color", sa.String(length=7), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "theme_primary_color")
