"""admin flags and projection creator fields

Revision ID: 0006_admin_and_projection_creator
Revises: 0005_backup_artifacts
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_admin_and_projection_creator"
down_revision = "0005_backup_artifacts"
branch_labels = None
depends_on = None


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
    unique: bool = False,
) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _add_user_flag(column_name: str, default_sql: str) -> None:
    if _column_meta("users", column_name) is None:
        op.add_column(
            "users",
            sa.Column(
                column_name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text(default_sql),
            ),
        )
    _create_index_if_missing(f"ix_users_{column_name}", "users", [column_name], unique=False)


def _add_creator_column(table_name: str) -> None:
    if _column_meta(table_name, "created_by_user_id") is None:
        op.add_column(
            table_name,
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        )


def _backfill_creator(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name} AS projection
            SET created_by_user_id = (
                SELECT ledgers.user_id
                FROM ledgers
                WHERE ledgers.id = projection.ledger_id
            )
            WHERE created_by_user_id IS NULL
            """
        )
    )


def upgrade() -> None:
    _add_user_flag("is_admin", "0")
    _add_user_flag("is_enabled", "1")

    for table_name in (
        "web_transaction_projection",
        "web_account_projection",
        "web_category_projection",
        "web_tag_projection",
    ):
        _add_creator_column(table_name)
        _backfill_creator(table_name)

    _create_index_if_missing(
        "ix_web_transaction_projection_created_by_user_id",
        "web_transaction_projection",
        ["created_by_user_id"],
    )
    _create_index_if_missing(
        "idx_web_tx_projection_ledger_creator",
        "web_transaction_projection",
        ["ledger_id", "created_by_user_id"],
    )

    _create_index_if_missing(
        "ix_web_account_projection_created_by_user_id",
        "web_account_projection",
        ["created_by_user_id"],
    )
    _create_index_if_missing(
        "idx_web_account_projection_ledger_creator",
        "web_account_projection",
        ["ledger_id", "created_by_user_id"],
    )

    _create_index_if_missing(
        "ix_web_category_projection_created_by_user_id",
        "web_category_projection",
        ["created_by_user_id"],
    )
    _create_index_if_missing(
        "idx_web_category_projection_ledger_creator",
        "web_category_projection",
        ["ledger_id", "created_by_user_id"],
    )

    _create_index_if_missing(
        "ix_web_tag_projection_created_by_user_id",
        "web_tag_projection",
        ["created_by_user_id"],
    )
    _create_index_if_missing(
        "idx_web_tag_projection_ledger_creator",
        "web_tag_projection",
        ["ledger_id", "created_by_user_id"],
    )


def downgrade() -> None:
    for index_name, table_name in (
        ("idx_web_tag_projection_ledger_creator", "web_tag_projection"),
        ("ix_web_tag_projection_created_by_user_id", "web_tag_projection"),
        ("idx_web_category_projection_ledger_creator", "web_category_projection"),
        ("ix_web_category_projection_created_by_user_id", "web_category_projection"),
        ("idx_web_account_projection_ledger_creator", "web_account_projection"),
        ("ix_web_account_projection_created_by_user_id", "web_account_projection"),
        ("idx_web_tx_projection_ledger_creator", "web_transaction_projection"),
        ("ix_web_transaction_projection_created_by_user_id", "web_transaction_projection"),
        ("ix_users_is_enabled", "users"),
        ("ix_users_is_admin", "users"),
    ):
        if _index_exists(table_name, index_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in (
        "web_tag_projection",
        "web_category_projection",
        "web_account_projection",
        "web_transaction_projection",
    ):
        if _column_meta(table_name, "created_by_user_id") is not None:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.drop_column("created_by_user_id")

    for column_name in ("is_enabled", "is_admin"):
        if _column_meta("users", column_name) is not None:
            with op.batch_alter_table("users") as batch_op:
                batch_op.drop_column(column_name)
