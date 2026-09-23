"""Allow the dedicated balance adjustment transaction type."""

from alembic import op
import sqlalchemy as sa


revision = "0020_balance_adjustment_type"
down_revision = "0019_account_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not support ALTER COLUMN directly; batch mode keeps the
    # migration usable by the local/test database as well as PostgreSQL.
    with op.batch_alter_table("read_tx_projection") as batch_op:
        batch_op.alter_column(
            "tx_type",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("read_tx_projection") as batch_op:
        batch_op.alter_column(
            "tx_type",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
