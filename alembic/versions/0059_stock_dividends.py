"""stock holdings phase 2: security_dividend_events + pending_dividends

股票持股(docs/STOCK_HOLDINGS_SD.md §7)Phase 2 股利:
- `security_dividend_events`:除權息事件,全域市場資料(同 `securities`)。
- `pending_dividends`:待確認股利,server 專屬狀態(不進 sync)。

全部是新表,不需要 backfill。

Revision ID: 0059_stock_dividends
Revises: 0058_stock_holdings
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0059_stock_dividends"
down_revision = "0058_stock_holdings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_dividend_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=True),
        sa.Column("cash_per_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stock_per_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ux_security_dividend_events_sec_date",
        "security_dividend_events",
        ["security_id", "ex_date"],
        unique=True,
    )

    op.create_table(
        "pending_dividends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_sync_id", sa.String(255), nullable=False),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("security_dividend_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("security_name", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("shares", sa.Float(), nullable=False, server_default="0"),
        sa.Column("est_gross", sa.Float(), nullable=False, server_default="0"),
        sa.Column("est_fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("est_tax", sa.Float(), nullable=False, server_default="0"),
        sa.Column("est_net", sa.Float(), nullable=False, server_default="0"),
        sa.Column("est_stock_shares", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_trade_ids", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ux_pending_dividends_user_account_event",
        "pending_dividends",
        ["user_id", "account_sync_id", "event_id"],
        unique=True,
    )
    op.create_index(
        "ix_pending_dividends_user_status",
        "pending_dividends",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_dividends_user_status", table_name="pending_dividends")
    op.drop_index("ux_pending_dividends_user_account_event", table_name="pending_dividends")
    op.drop_table("pending_dividends")
    op.drop_index("ux_security_dividend_events_sec_date", table_name="security_dividend_events")
    op.drop_table("security_dividend_events")
