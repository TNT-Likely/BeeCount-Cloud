"""Keep detached attachment candidates until an upload manifest is complete."""
from alembic import op
import sqlalchemy as sa


revision = "0027_tx_attachment_gc"
down_revision = "0026_ws_relay"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("read_tx_projection", sa.Column("pending_attachment_gc_json", sa.Text(), nullable=True))
    op.add_column("read_tx_projection", sa.Column("attachment_gc_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_read_tx_projection_attachment_gc_after", "read_tx_projection", ["attachment_gc_after"])


def downgrade():
    op.drop_index("ix_read_tx_projection_attachment_gc_after", table_name="read_tx_projection")
    op.drop_column("read_tx_projection", "attachment_gc_after")
    op.drop_column("read_tx_projection", "pending_attachment_gc_json")
