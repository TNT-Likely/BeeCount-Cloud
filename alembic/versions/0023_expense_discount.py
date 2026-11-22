"""Add expense discount amount to transaction projection.

Revision ID: 0023_expense_discount
Revises: 0022_transfer_fee_discount
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_expense_discount"
down_revision = "0022_transfer_fee_discount"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("read_tx_projection", sa.Column("discount_amount", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("read_tx_projection", "discount_amount")
