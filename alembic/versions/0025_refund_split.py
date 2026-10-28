"""交易退款分摊(0025)

为 read_refund_projection 增加两列:
- refund_mode: 'unified'(默认) | 'split'(分别退款,按原付款 legs 拆分到多张卡)
- refund_legs_json: split 模式下每条腿的退款去向账户 + 金额(JSON),
  sum(legs) == amount(容差 0.011);unified 模式为 NULL。

配合 src/services/linked_transactions.py 的 reconcile_refund_linked_tx:
received 状态的退款会按模式生成 linked income 交易(excludeFromStats),
把退款真实冲回对应卡余额,且出现在账户流水 / 对账单中。

Revision ID: 0025_refund_split
Revises: 0024_members
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_refund_split"
down_revision = "0024_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_refund_projection",
        sa.Column(
            "refund_mode",
            sa.String(16),
            nullable=False,
            server_default="unified",
        ),
    )
    op.add_column(
        "read_refund_projection",
        sa.Column("refund_legs_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_refund_projection", "refund_legs_json")
    op.drop_column("read_refund_projection", "refund_mode")
