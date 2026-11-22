"""0030 — 联系人 / 消费成员增加关联人资料(头像、多联系方式、多银行卡)。

两个实体共用一份 camelCase JSON 资料，存在 profile_json 列里；旧的
phone/wechat/alipay/avatar_file_id 单值列保留不动，由应用层双向同步。

Revision ID: 0030_party_profiles
Revises: 0029_loan_repayment_attachments
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0030_party_profiles"
down_revision = "0029_loan_repayment_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("profile_json", sa.Text(), nullable=True))
    op.add_column(
        "user_member_projection",
        sa.Column("profile_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_member_projection", "profile_json")
    op.drop_column("contacts", "profile_json")
