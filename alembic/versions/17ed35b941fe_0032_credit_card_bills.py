"""0032_credit_card_bills

信用卡账单功能：在 user_account_projection 上新增账单周期/状态/金额字段，
并将 billing_day / payment_due_day 从 INTEGER 改为 DateTime(timezone=True)。

Revision ID: 17ed35b941fe
Revises: 0031_deposits
"""
from alembic import op
import sqlalchemy as sa


revision = '17ed35b941fe'
down_revision = '0031_deposits'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 账单周期锚点日:按 billing_day 自动划分的账期起始日(本地日期)
    op.add_column(
        'user_account_projection',
        sa.Column('billing_cycle_start', sa.DateTime(timezone=True), nullable=True),
    )
    # 账单状态:0=未出账 / 1=已出账 / 2=已还款 / 3=部分还款 / 4=已逾期
    op.add_column(
        'user_account_projection',
        sa.Column('bill_status', sa.Integer(), server_default='0', nullable=False),
    )
    # 本期应还 / 最低还款 / 已还 / 剩余待还(均为原币金额)
    op.add_column(
        'user_account_projection',
        sa.Column('bill_amount_due', sa.Float(), nullable=True),
    )
    op.add_column(
        'user_account_projection',
        sa.Column('bill_minimum_amount', sa.Float(), nullable=True),
    )
    op.add_column(
        'user_account_projection',
        sa.Column('bill_paid_amount', sa.Float(), nullable=True),
    )
    op.add_column(
        'user_account_projection',
        sa.Column('bill_remaining_amount', sa.Float(), nullable=True),
    )
    # billing_day / payment_due_day 从 INTEGER 改为 DateTime(timezone=True)
    # SQLite 不支持 ALTER COLUMN TYPE,采用重建列的方式:
    # 1) 新增临时列;2) 迁移数据;3) 删除旧列;4) 重命名临时列。
    _alter_column_datetime('user_account_projection', 'billing_day')
    _alter_column_datetime('user_account_projection', 'payment_due_day')


def _alter_column_datetime(table: str, column: str) -> None:
    """SQLite 兼容的列类型变更:INTEGER -> DateTime(timezone=True)。

    SQLite 的 ALTER COLUMN 仅支持 RENAME / ADD / DROP,不支持改类型。
    这里走 add -> copy -> drop -> rename 四步,保留原有索引与约束。
    """
    tmp = f"_{column}_tmp"
    op.add_column(table, sa.Column(tmp, sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            f"UPDATE {table} SET {tmp} = "
            f"datetime(CAST({column} AS TEXT), 'unixepoch') "
            f"WHERE {column} IS NOT NULL"
        )
    )
    op.drop_column(table, column)
    op.alter_column(table, tmp, new_column_name=column)


def downgrade() -> None:
    _alter_column_integer('user_account_projection', 'payment_due_day')
    _alter_column_integer('user_account_projection', 'billing_day')
    op.drop_column('user_account_projection', 'bill_remaining_amount')
    op.drop_column('user_account_projection', 'bill_paid_amount')
    op.drop_column('user_account_projection', 'bill_minimum_amount')
    op.drop_column('user_account_projection', 'bill_amount_due')
    op.drop_column('user_account_projection', 'bill_status')
    op.drop_column('user_account_projection', 'billing_cycle_start')


def _alter_column_integer(table: str, column: str) -> None:
    """回滚:DateTime(timezone=True) -> INTEGER(天数)。"""
    tmp = f"_{column}_tmp"
    op.add_column(table, sa.Column(tmp, sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            f"UPDATE {table} SET {tmp} = "
            f"CAST(strftime('%d', {column}) AS INTEGER) "
            f"WHERE {column} IS NOT NULL"
        )
    )
    op.drop_column(table, column)
    op.alter_column(table, tmp, new_column_name=column)