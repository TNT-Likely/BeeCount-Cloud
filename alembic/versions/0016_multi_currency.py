"""多币种 MVP:user_profiles.primary_currency + 手动汇率 projection + 汇率代理缓存

Revision ID: 0016_multi_currency
Revises: 0015_backfill_tx_account_sync_id
Create Date: 2026-06-10

设计:BeeCount 仓 .docs/multi-currency/03-tech-design-cloud.md。
rate 列存 decimal 字符串(方向 1 quote = rate base);
exchange_rate_cache.payload_json 方向 1 base = x quote(与上游一致)。
"""

import sqlalchemy as sa
from alembic import op


revision = "0016_multi_currency"
down_revision = "0015_backfill_tx_account_sync_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("primary_currency", sa.String(16), nullable=True),  # 预留 16 位对齐既有币种列宽
    )
    op.create_table(
        "user_exchange_rate_projection",
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column("base_currency", sa.String(16), nullable=False),  # 预留 16 位对齐既有币种列宽
        sa.Column("quote_currency", sa.String(16), nullable=False),  # 预留 16 位对齐既有币种列宽
        sa.Column("rate", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_user_rate_pair",
        "user_exchange_rate_projection",
        ["user_id", "base_currency", "quote_currency"],
    )
    op.create_table(
        "exchange_rate_cache",
        sa.Column("base_currency", sa.String(16), primary_key=True),  # 预留 16 位对齐既有币种列宽
        sa.Column("rate_date", sa.String(10), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("exchange_rate_cache")
    op.drop_index("ix_user_rate_pair", table_name="user_exchange_rate_projection")
    op.drop_table("user_exchange_rate_projection")
    op.drop_column("user_profiles", "primary_currency")
