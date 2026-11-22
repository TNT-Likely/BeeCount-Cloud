"""Add member entity + transaction member association/splits.

Revision ID: 0024_members
Revises: 0023_expense_discount
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_members"
down_revision = "0023_expense_discount"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_member_projection",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column(
            "source_change_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "sync_id"),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("member_sync_ids_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("member_names_csv", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("member_splits_json", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("read_tx_projection", "member_splits_json")
    op.drop_column("read_tx_projection", "member_names_csv")
    op.drop_column("read_tx_projection", "member_sync_ids_json")
    op.drop_table("user_member_projection")
