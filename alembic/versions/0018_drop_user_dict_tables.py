"""drop user_accounts / user_categories / user_tags projection tables

Revision ID: 0018_drop_user_dict_tables
Revises: 0017_user_profile_ai_config
Create Date: 2026-04-18

新架构下,所有实体(交易/账户/分类/标签/预算)都以 sync_changes 事件流 +
ledger_snapshot JSON 聚合为权威来源。Web 读写都从 snapshot 走。UserAccount /
UserCategory / UserTag 三张老投影表唯一的写入方(/write/workspace/* 和 tx 创建
时的 _resolve_tx_dictionary_payload)已经全部删掉,这里把表也 DROP 掉。
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_drop_user_dict_tables"
down_revision = "0017_user_profile_ai_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 顺序:先删依赖外键的索引,SQLite/PG 都会在 drop_table 时级联处理,
    # 所以直接 drop_table 即可。user_categories 有自引用 parent_id,Alembic
    # 的 drop_table 会自己先删 FK。
    op.drop_table("user_tags")
    op.drop_table("user_categories")
    op.drop_table("user_accounts")


def downgrade() -> None:
    # 回滚用:重建 3 张空表。不 backfill 数据(原本就被视为废弃)。

    op.create_table(
        "user_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_accounts_user_id", "user_accounts", ["user_id"])
    op.create_index("ix_user_accounts_name", "user_accounts", ["name"])
    op.create_index(
        "idx_user_accounts_user_name", "user_accounts", ["user_id", "name"]
    )

    op.create_table(
        "user_categories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("icon_type", sa.String(length=32), nullable=True),
        sa.Column("custom_icon_path", sa.String(length=1024), nullable=True),
        sa.Column("icon_cloud_file_id", sa.String(length=36), nullable=True),
        sa.Column("icon_cloud_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "parent_id",
            sa.String(length=36),
            sa.ForeignKey("user_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_categories_user_id", "user_categories", ["user_id"])
    op.create_index("ix_user_categories_name", "user_categories", ["name"])
    op.create_index("ix_user_categories_kind", "user_categories", ["kind"])
    op.create_index(
        "idx_user_categories_user_kind_name",
        "user_categories",
        ["user_id", "kind", "name"],
    )

    op.create_table(
        "user_tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_tags_user_id", "user_tags", ["user_id"])
    op.create_index("ix_user_tags_name", "user_tags", ["name"])
    op.create_index("idx_user_tags_user_name", "user_tags", ["user_id", "name"])
