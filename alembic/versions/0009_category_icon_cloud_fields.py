"""add category icon cloud metadata fields

Revision ID: 0009_category_icon_cloud_fields
Revises: 0008_attachments_and_device_meta
Create Date: 2026-02-26 15:20:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_category_icon_cloud_fields"
down_revision = "0008_attachments_and_device_meta"
branch_labels = None
depends_on = None


def _column_meta(table_name: str, column_name: str):
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()
    for row in rows:
        if row[1] == column_name:
            return row
    return None


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA index_list('{table_name}')").fetchall()
    return any(row[1] == index_name for row in rows)


def upgrade() -> None:
    with op.batch_alter_table("web_category_projection") as batch_op:
        if _column_meta("web_category_projection", "custom_icon_path") is None:
            batch_op.add_column(sa.Column("custom_icon_path", sa.String(length=1024), nullable=True))
        if _column_meta("web_category_projection", "icon_cloud_file_id") is None:
            batch_op.add_column(sa.Column("icon_cloud_file_id", sa.String(length=36), nullable=True))
        if _column_meta("web_category_projection", "icon_cloud_sha256") is None:
            batch_op.add_column(sa.Column("icon_cloud_sha256", sa.String(length=64), nullable=True))

    if not _index_exists("web_category_projection", "ix_web_category_projection_icon_cloud_file_id"):
        op.create_index(
            "ix_web_category_projection_icon_cloud_file_id",
            "web_category_projection",
            ["icon_cloud_file_id"],
            unique=False,
        )


def downgrade() -> None:
    if _index_exists("web_category_projection", "ix_web_category_projection_icon_cloud_file_id"):
        op.drop_index(
            "ix_web_category_projection_icon_cloud_file_id",
            table_name="web_category_projection",
        )
    with op.batch_alter_table("web_category_projection") as batch_op:
        if _column_meta("web_category_projection", "icon_cloud_sha256") is not None:
            batch_op.drop_column("icon_cloud_sha256")
        if _column_meta("web_category_projection", "icon_cloud_file_id") is not None:
            batch_op.drop_column("icon_cloud_file_id")
        if _column_meta("web_category_projection", "custom_icon_path") is not None:
            batch_op.drop_column("custom_icon_path")
