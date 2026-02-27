"""bootstrap one admin user when none exists

Revision ID: 0007_admin_bootstrap
Revises: 0006_admin_and_projection_creator
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_admin_bootstrap"
down_revision = "0006_admin_and_projection_creator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.String(length=36)),
        sa.column("is_admin", sa.Boolean()),
        sa.column("is_enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    admin_count = int(
        bind.scalar(
            sa.select(sa.func.count())
            .select_from(users)
            .where(users.c.is_admin.is_(True))
        )
        or 0
    )
    if admin_count > 0:
        return

    fallback_user_id = bind.scalar(
        sa.select(users.c.id)
        .where(users.c.is_enabled.is_(True))
        .order_by(users.c.created_at.asc(), users.c.id.asc())
        .limit(1)
    )
    if fallback_user_id is None:
        return

    bind.execute(
        sa.update(users)
        .where(users.c.id == fallback_user_id)
        .values(is_admin=True)
    )


def downgrade() -> None:
    # Data migration only; keep assigned admin flags on downgrade.
    return
