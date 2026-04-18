"""add user_profiles.appearance_json column

Revision ID: 0016_user_profile_appearance
Revises: 0015_user_profile_theme_color
Create Date: 2026-04-18 17:30:00.000000

外观类设置打包成 JSON blob 存到 user_profiles,当前包括
header_decoration_style / compact_amount / show_transaction_time。
字体缩放 font_scale 故意不进来 —— 不同设备屏幕尺寸不同,强行拉齐反而不好。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_user_profile_appearance"
down_revision = "0015_user_profile_theme_color"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("appearance_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "appearance_json")
