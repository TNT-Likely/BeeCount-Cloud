"""Add transfer fee and discount fields to transaction projection.

Revision ID: 0022_transfer_fee_discount
Revises: 0021_mcp_workflows
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_transfer_fee_discount"
down_revision = "0021_mcp_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("read_tx_projection", sa.Column("transfer_fee_amount", sa.Float(), nullable=True))
    op.add_column("read_tx_projection", sa.Column("transfer_discount_amount", sa.Float(), nullable=True))
    op.add_column("read_tx_projection", sa.Column("transfer_fee_account_sync_id", sa.String(255), nullable=True))
    op.add_column("read_tx_projection", sa.Column("transfer_fee_account_name", sa.Text(), nullable=True))
    op.add_column("read_tx_projection", sa.Column("transfer_fee_tx_sync_id", sa.String(255), nullable=True))
    op.add_column("read_tx_projection", sa.Column("transfer_discount_tx_sync_id", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("read_tx_projection", "transfer_discount_tx_sync_id")
    op.drop_column("read_tx_projection", "transfer_fee_tx_sync_id")
    op.drop_column("read_tx_projection", "transfer_fee_account_name")
    op.drop_column("read_tx_projection", "transfer_fee_account_sync_id")
    op.drop_column("read_tx_projection", "transfer_discount_amount")
    op.drop_column("read_tx_projection", "transfer_fee_amount")
