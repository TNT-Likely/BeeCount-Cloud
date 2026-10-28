"""交易扩展字段(优惠/转账手续费/合并付款)+ 定期存款/归物投影表

0021 一次性落地 5 个社区高票功能(对应 BeeCount 仓 issue #465 / Cloud 仓
issue #79 / #80):

- read_tx_projection 增加 discount_json / transfer_fee_json /
  payment_legs_json 三列(JSON 文本,NULL=未使用,旧行不写)。
- 新增 read_time_deposit_projection(定期存款)与
  read_item_return_projection(归物)两张 ledger-scoped 投影表,跟
  budget 同一条同步链路(mobile /sync/push → sync_changes → 投影)。

既有投影行三列留 NULL(旧 App 不发该字段时保持无优惠/无手续费/单账户)。
新表无存量数据,纯 CREATE TABLE。

Revision ID: 0021_tx_discount_fee_legs_deposit_return
Revises: 0020_category_parent_links
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op


revision = "0021_tx_discount_fee_legs_deposit_return"
down_revision = "0020_category_parent_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- read_tx_projection: 3 个 JSON 文本列 -------------------------- #
    op.add_column(
        "read_tx_projection",
        sa.Column("discount_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("transfer_fee_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("payment_legs_json", sa.Text(), nullable=True),
    )

    # --- read_time_deposit_projection ----------------------------------- #
    op.create_table(
        "read_time_deposit_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("principal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(16), nullable=False, server_default="CNY"),
        sa.Column("annual_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("term_months", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("maturity_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="holding"),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("interest_mode", sa.String(16), nullable=False, server_default="simple"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_interest", sa.Float(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_time_deposit_projection_user_id",
        "read_time_deposit_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_time_deposit_user_status",
        "read_time_deposit_projection",
        ["user_id", "status"],
    )

    # --- read_item_return_projection ------------------------------------ #
    op.create_table(
        "read_item_return_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_tx_id", sa.String(255), nullable=True),
        sa.Column("original_amount", sa.Float(), nullable=True),
        sa.Column("refund_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("category_sync_id", sa.String(255), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_item_return_projection_user_id",
        "read_item_return_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_item_return_user_status",
        "read_item_return_projection",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_item_return_user_status", table_name="read_item_return_projection")
    op.drop_index("ix_read_item_return_projection_user_id", table_name="read_item_return_projection")
    op.drop_table("read_item_return_projection")
    op.drop_index("ix_read_time_deposit_user_status", table_name="read_time_deposit_projection")
    op.drop_index("ix_read_time_deposit_projection_user_id", table_name="read_time_deposit_projection")
    op.drop_table("read_time_deposit_projection")
    op.drop_column("read_tx_projection", "payment_legs_json")
    op.drop_column("read_tx_projection", "transfer_fee_json")
    op.drop_column("read_tx_projection", "discount_json")
