"""backup artifacts table

Revision ID: 0005_backup_artifacts
Revises: 0004_web_write_ids_and_idempotency
Create Date: 2026-02-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_backup_artifacts"
down_revision = "0004_web_write_ids_and_idempotency"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


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


def upgrade() -> None:
    if not _has_table("backup_artifacts"):
        op.create_table(
            "backup_artifacts",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "ledger_id",
                sa.String(length=36),
                sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.String(length=1024), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    _create_index_if_missing(
        "ix_backup_artifacts_user_id", "backup_artifacts", ["user_id"], unique=False
    )
    _create_index_if_missing(
        "ix_backup_artifacts_ledger_id", "backup_artifacts", ["ledger_id"], unique=False
    )
    _create_index_if_missing("ix_backup_artifacts_kind", "backup_artifacts", ["kind"], unique=False)
    _create_index_if_missing(
        "ix_backup_artifacts_checksum_sha256",
        "backup_artifacts",
        ["checksum_sha256"],
        unique=False,
    )
    _create_index_if_missing(
        "ix_backup_artifacts_created_at", "backup_artifacts", ["created_at"], unique=False
    )
    _create_index_if_missing(
        "idx_backup_artifacts_ledger_created",
        "backup_artifacts",
        ["ledger_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("backup_artifacts")
