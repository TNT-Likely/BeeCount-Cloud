"""stock holdings phase 1: securities + security_quotes + read_stock_trade_projection
+ user_account_projection.investment_settings_json

股票持股(docs/STOCK_HOLDINGS_SD.md)Phase 1:
- `securities` / `security_quotes`:全域市場資料(不分 user、不進 sync),
  比照 `exchange_rate_cache`。
- `read_stock_trade_projection`:新的 ledger-scoped sync entity `stock_trade`。
- `user_account_projection.investment_settings_json`:投資理財帳戶的自訂
  費用設定(wire `investmentSettings`)。

全部是新表/nullable 新欄位,不需要 backfill。

Revision ID: 0058_stock_holdings
Revises: 0057_system_broadcasts
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_stock_holdings"
down_revision = "0057_system_broadcasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "securities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("currency", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="stock"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ux_securities_market_symbol", "securities", ["market", "symbol"], unique=True
    )

    op.create_table(
        "security_quotes",
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("prev_close", sa.Float(), nullable=True),
        sa.Column("quote_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session", sa.String(16), nullable=False, server_default="close"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "read_stock_trade_projection",
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("market", sa.String(16), nullable=False, server_default=""),
        sa.Column("symbol", sa.String(32), nullable=False, server_default=""),
        sa.Column("security_name", sa.Text(), nullable=True),
        sa.Column("trade_type", sa.String(32), nullable=False, server_default="buy"),
        sa.Column("shares", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tax", sa.Float(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("trade_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tx_sync_id", sa.String(255), nullable=True),
        sa.Column("dividend_event_ref", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_stock_trade_projection_user_id", "read_stock_trade_projection", ["user_id"]
    )
    op.create_index(
        "ix_read_stock_trade_account",
        "read_stock_trade_projection",
        ["user_id", "account_sync_id"],
    )
    op.create_index(
        "ix_read_stock_trade_symbol", "read_stock_trade_projection", ["market", "symbol"]
    )

    op.add_column(
        "user_account_projection",
        sa.Column("investment_settings_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "investment_settings_json")
    op.drop_index("ix_read_stock_trade_symbol", table_name="read_stock_trade_projection")
    op.drop_index("ix_read_stock_trade_account", table_name="read_stock_trade_projection")
    op.drop_index(
        "ix_read_stock_trade_projection_user_id", table_name="read_stock_trade_projection"
    )
    op.drop_table("read_stock_trade_projection")
    op.drop_table("security_quotes")
    op.drop_index("ux_securities_market_symbol", table_name="securities")
    op.drop_table("securities")
