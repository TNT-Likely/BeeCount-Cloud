"""add icon + linked_user_id to read_member_projection

Revision ID: 0031_member_icon_link
Revises: 0030_tx_time_deposit_link
Create Date: 2026-10-27

- icon:成员图标 key(人物语义 Material Symbols 名),只影响展示。
- linked_user_id:成员关联的记账人(登录用户)。设了之后,该用户记的、没有
  分摊的交易在 member-share-stats 里并入该成员(kind='member'),不再单独
  出一条 kind='user' —— 避免同一个人被算两遍。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_member_icon_link"
down_revision = "0030_tx_time_deposit_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_member_projection",
        sa.Column("icon", sa.String(64), nullable=True),
    )
    op.add_column(
        "read_member_projection",
        sa.Column("linked_user_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_read_member_projection_linked_user_id",
        "read_member_projection",
        ["linked_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_read_member_projection_linked_user_id",
        table_name="read_member_projection",
    )
    op.drop_column("read_member_projection", "linked_user_id")
    op.drop_column("read_member_projection", "icon")
