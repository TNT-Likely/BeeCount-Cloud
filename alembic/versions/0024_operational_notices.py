"""Track device sync failures and the effective start of backup schedules."""

import sqlalchemy as sa
from alembic import op

revision = "0024_operational_notices"
down_revision = "0023_user_notification_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backup_schedules", sa.Column("cron_effective_at", sa.DateTime(timezone=True)))
    # updated_at also changes when the scheduler starts or a run finishes. Only
    # user edits are a safe historical boundary for old cron expressions.
    schedules = sa.table(
        "backup_schedules",
        sa.column("id"),
        sa.column("user_id"),
        sa.column("created_at"),
        sa.column("cron_effective_at"),
    )
    audit = sa.table(
        "audit_logs",
        sa.column("user_id"),
        sa.column("action"),
        sa.column("metadata_json", sa.JSON()),
        sa.column("created_at"),
    )
    last_edit = (
        sa.select(sa.func.max(audit.c.created_at))
        .where(
            audit.c.user_id == schedules.c.user_id,
            audit.c.action == "backup_schedule_update",
            audit.c.metadata_json["scheduleId"].as_integer() == schedules.c.id,
        )
        .scalar_subquery()
    )
    op.execute(
        schedules.update().values(
            cron_effective_at=sa.func.coalesce(last_edit, schedules.c.created_at)
        )
    )
    op.create_table(
        "device_sync_failures",
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("direction", sa.String(4), primary_key=True),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("device_sync_failures")
    op.drop_column("backup_schedules", "cron_effective_at")
