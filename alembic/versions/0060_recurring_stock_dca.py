"""read_recurring_rule_projection: kind / market / symbol / security_name /
stock_fee_rate / stock_fee_min（股票定期定額）

使用者需求(2026-09-28):新增「定期定額投資」,因為定期定額的手續費規則
常常跟單筆買進不一樣,需要能各自設定;同時沿用既有的「週期性收支」管理
頁面,用 `kind` 欄位區分「一般交易」跟「股票定期定額」。

`kind='stock_dca'` 的規則必定 `tx_type='transfer'`(from_account_id=交割
帳戶、to_account_id=投資理財帳戶、amount=每期投入金額,以證券幣別計)。
`market`/`symbol`/`security_name` 同 `read_stock_trade_projection` 對應
欄位,只有 `kind='stock_dca'` 才有值。`stock_fee_rate`/`stock_fee_min` 是
規則層級的手續費覆寫,皆為 NULL 時到期生成沿用投資理財帳戶的預設
`investment_settings_json`。

全部是新欄位,`kind` 給 server_default='general' 讓既有資料自動回填,其它
皆 nullable,不需要額外 backfill。

Revision ID: 0060_recurring_stock_dca
Revises: 0059_stock_dividends
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0060_recurring_stock_dca"
down_revision = "0059_stock_dividends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("kind", sa.String(16), nullable=False, server_default="general"),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("market", sa.String(16), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("symbol", sa.String(32), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("security_name", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("stock_fee_rate", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("stock_fee_min", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_read_recurring_rule_kind", "read_recurring_rule_projection", ["kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_read_recurring_rule_kind", table_name="read_recurring_rule_projection")
    op.drop_column("read_recurring_rule_projection", "stock_fee_min")
    op.drop_column("read_recurring_rule_projection", "stock_fee_rate")
    op.drop_column("read_recurring_rule_projection", "security_name")
    op.drop_column("read_recurring_rule_projection", "symbol")
    op.drop_column("read_recurring_rule_projection", "market")
    op.drop_column("read_recurring_rule_projection", "kind")
