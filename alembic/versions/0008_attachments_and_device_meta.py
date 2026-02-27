"""attachments storage and device metadata

Revision ID: 0008_attachments_and_device_meta
Revises: 0007_admin_bootstrap
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_attachments_and_device_meta"
down_revision = "0007_admin_bootstrap"
branch_labels = None
depends_on = None


def _column_meta(table_name: str, column_name: str) -> dict | None:
    inspector = sa.inspect(op.get_bind())
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return column
    return None


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    for name, length in (
        ("app_version", 64),
        ("os_version", 64),
        ("device_model", 128),
        ("last_ip", 64),
    ):
        if _column_meta("devices", name) is None:
            op.add_column("devices", sa.Column(name, sa.String(length=length), nullable=True))

    if not _table_exists("attachment_files"):
        op.create_table(
            "attachment_files",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("ledger_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("mime_type", sa.String(length=128), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=True),
            sa.Column("storage_path", sa.String(length=1024), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists("attachment_files", "ix_attachment_files_ledger_id"):
        op.create_index("ix_attachment_files_ledger_id", "attachment_files", ["ledger_id"], unique=False)
    if not _index_exists("attachment_files", "ix_attachment_files_user_id"):
        op.create_index("ix_attachment_files_user_id", "attachment_files", ["user_id"], unique=False)
    if not _index_exists("attachment_files", "ix_attachment_files_sha256"):
        op.create_index("ix_attachment_files_sha256", "attachment_files", ["sha256"], unique=False)
    if not _index_exists("attachment_files", "ix_attachment_files_created_at"):
        op.create_index("ix_attachment_files_created_at", "attachment_files", ["created_at"], unique=False)
    if not _index_exists("attachment_files", "idx_attachment_files_sha256"):
        op.create_index("idx_attachment_files_sha256", "attachment_files", ["sha256"], unique=False)
    if not _index_exists("attachment_files", "idx_attachment_files_ledger_created"):
        op.create_index(
            "idx_attachment_files_ledger_created",
            "attachment_files",
            ["ledger_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    if _table_exists("attachment_files"):
        for index_name in (
            "idx_attachment_files_ledger_created",
            "idx_attachment_files_sha256",
            "ix_attachment_files_created_at",
            "ix_attachment_files_sha256",
            "ix_attachment_files_user_id",
            "ix_attachment_files_ledger_id",
        ):
            if _index_exists("attachment_files", index_name):
                op.drop_index(index_name, table_name="attachment_files")
        op.drop_table("attachment_files")

    for name in ("last_ip", "device_model", "os_version", "app_version"):
        if _column_meta("devices", name) is not None:
            with op.batch_alter_table("devices") as batch_op:
                batch_op.drop_column(name)
