"""Add payment_splits_json to read_tx_projection (0028).

支出组合付款:一笔支出可由多个账户分摊支付,拆分明细存 JSON,账户余额/统计按拆分扣减。
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_payment_splits"
down_revision = "0027_loan_account_flow"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "read_tx_projection",
        sa.Column("payment_splits_json", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("read_tx_projection", "payment_splits_json")
