"""Add refund support: link refunds back to original transactions.

Revision ID: 0025_refunds
Revises: 0024_members
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_refunds"
down_revision = "0024_members"
branch_labels = None
depends_on = None


def upgrade():
    # 退款交易标记 + 关联原单。
    # is_refund: 1 = 该笔交易是一笔退款(必须带 refund_of_sync_id)。
    # refund_of_sync_id: 指向被退款的原始交易 sync_id。
    # 退款冲减原交易的分类支出统计、退回接收账户余额,但不计入收入、不污染月度收支。
    op.add_column(
        "read_tx_projection",
        sa.Column(
            "is_refund",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("refund_of_sync_id", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("read_tx_projection", "refund_of_sync_id")
    op.drop_column("read_tx_projection", "is_refund")
