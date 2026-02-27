"""web write ids and idempotency

Revision ID: 0004_web_write_ids_and_idempotency
Revises: 0003_share_and_projection_ledger_dim
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_web_write_ids_and_idempotency"
down_revision = "0003_share_and_projection_ledger_dim"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _column_meta(table_name: str, column_name: str) -> dict | None:
    inspector = sa.inspect(op.get_bind())
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return column
    return None


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool,
) -> None:
    if _index_exists(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def _add_sync_id(table_name: str, prefix: str) -> None:
    if _column_meta(table_name, "sync_id") is None:
        op.add_column(table_name, sa.Column("sync_id", sa.String(length=64), nullable=True))

    op.execute(f"UPDATE {table_name} SET sync_id = '{prefix}-' || id WHERE sync_id IS NULL")
    column = _column_meta(table_name, "sync_id")
    if column is not None and column.get("nullable", True):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "sync_id",
                existing_type=sa.String(length=64),
                nullable=False,
            )


def upgrade() -> None:
    _add_sync_id("web_transaction_projection", "tx")
    _add_sync_id("web_account_projection", "acc")
    _add_sync_id("web_category_projection", "cat")
    _add_sync_id("web_tag_projection", "tag")

    _create_index_if_missing(
        "uq_web_transaction_projection_sync",
        "web_transaction_projection",
        ["ledger_id", "sync_id"],
        unique=True,
    )
    _create_index_if_missing(
        "uq_web_account_projection_sync",
        "web_account_projection",
        ["ledger_id", "sync_id"],
        unique=True,
    )
    _create_index_if_missing(
        "uq_web_category_projection_sync",
        "web_category_projection",
        ["ledger_id", "sync_id"],
        unique=True,
    )
    _create_index_if_missing(
        "uq_web_tag_projection_sync",
        "web_tag_projection",
        ["ledger_id", "sync_id"],
        unique=True,
    )

    _create_index_if_missing(
        "ix_web_transaction_projection_sync_id",
        "web_transaction_projection",
        ["sync_id"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_web_account_projection_sync_id",
        "web_account_projection",
        ["sync_id"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_web_category_projection_sync_id",
        "web_category_projection",
        ["sync_id"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_web_tag_projection_sync_id",
        "web_tag_projection",
        ["sync_id"],
        unique=False,
    )

    if not _has_table("sync_push_idempotency"):
        op.create_table(
            "sync_push_idempotency",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("device_id", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("request_hash", sa.String(length=128), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "device_id",
                "idempotency_key",
                name="uq_sync_push_idempotency",
            ),
        )

    _create_index_if_missing(
        "ix_sync_push_idempotency_user_id",
        "sync_push_idempotency",
        ["user_id"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_sync_push_idempotency_device_id",
        "sync_push_idempotency",
        ["device_id"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_sync_push_idempotency_idempotency_key",
        "sync_push_idempotency",
        ["idempotency_key"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_sync_push_idempotency_expires_at",
        "sync_push_idempotency",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("sync_push_idempotency")

    op.drop_index("ix_web_tag_projection_sync_id", table_name="web_tag_projection")
    op.drop_index("ix_web_category_projection_sync_id", table_name="web_category_projection")
    op.drop_index("ix_web_account_projection_sync_id", table_name="web_account_projection")
    op.drop_index("ix_web_transaction_projection_sync_id", table_name="web_transaction_projection")

    op.drop_index("uq_web_tag_projection_sync", table_name="web_tag_projection")
    op.drop_index("uq_web_category_projection_sync", table_name="web_category_projection")
    op.drop_index("uq_web_account_projection_sync", table_name="web_account_projection")
    op.drop_index("uq_web_transaction_projection_sync", table_name="web_transaction_projection")

    with op.batch_alter_table("web_tag_projection") as batch_op:
        batch_op.drop_column("sync_id")
    with op.batch_alter_table("web_category_projection") as batch_op:
        batch_op.drop_column("sync_id")
    with op.batch_alter_table("web_account_projection") as batch_op:
        batch_op.drop_column("sync_id")
    with op.batch_alter_table("web_transaction_projection") as batch_op:
        batch_op.drop_column("sync_id")
