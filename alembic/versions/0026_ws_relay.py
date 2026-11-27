"""Short-lived user-addressed WebSocket frames for multiple Cloud processes."""

from alembic import op
import sqlalchemy as sa

revision = "0026_ws_relay"
down_revision = "0025_sync_operation_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ws_relay_frames",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_ws_relay_frames_created_at", "ws_relay_frames", ["created_at"])


def downgrade() -> None:
    op.drop_table("ws_relay_frames")
