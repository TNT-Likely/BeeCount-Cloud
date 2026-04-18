"""add user_profiles.ai_config_json column

Revision ID: 0017_user_profile_ai_config
Revises: 0016_user_profile_appearance
Create Date: 2026-04-18 18:30:00.000000

AI 配置(服务商数组 + 能力绑定 + 自定义提示词 + 策略等)打包成 JSON 存到
user_profiles,方便跨设备同步。跟 appearance_json 同样的套路,API key 敏感,
只在用户自己的 session 内流转。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_user_profile_ai_config"
down_revision = "0016_user_profile_appearance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("ai_config_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "ai_config_json")
