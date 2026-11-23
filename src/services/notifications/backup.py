from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import BackupRun, BackupSchedule, User
from ...time_utils import as_utc
from ..backup.scheduler import get_scheduler
from .types import BackupNotice, notice_id

OVERDUE_GRACE = timedelta(hours=1)
FULL_FAILURE_THRESHOLD = 2


def backup_notices(db: Session, user: User) -> list[BackupNotice]:
    if not user.is_admin:
        return []

    # Each plan has one incident between two fully successful runs. Compute the
    # boundary in SQL so reading the inbox never loads historical run logs.
    plan, run = BackupSchedule, BackupRun
    effective = func.coalesce(plan.cron_effective_at, plan.created_at)
    plans = (
        select(
            plan.id,
            plan.name,
            plan.cron_expr,
            effective.label("effective_at"),
            func.coalesce(func.max(run.finished_at), effective).label("since"),
        )
        .outerjoin(
            run,
            and_(
                run.schedule_id == plan.id,
                run.user_id == user.id,
                run.status == "succeeded",
                run.started_at >= effective,
            ),
        )
        .where(plan.user_id == user.id, plan.enabled.is_(True))
        .group_by(plan.id, plan.name, plan.cron_expr, plan.cron_effective_at, plan.created_at)
        .cte("monitored_plans")
    )
    failures = (
        select(
            run.schedule_id,
            run.id,
            run.status,
            run.finished_at,
            func.count().over(partition_by=run.schedule_id).label("failure_count"),
            func.row_number()
            .over(partition_by=run.schedule_id, order_by=(run.finished_at.desc(), run.id.desc()))
            .label("position"),
        )
        .join(plans, plans.c.id == run.schedule_id)
        .where(
            run.user_id == user.id,
            run.started_at >= plans.c.effective_at,
            run.status.in_(("failed", "partial")),
            run.finished_at > plans.c.since,
        )
        .subquery()
    )
    rows = db.execute(
        select(
            plans,
            failures.c.id.label("run_id"),
            failures.c.status,
            failures.c.finished_at,
            failures.c.failure_count,
        )
        .outerjoin(failures, and_(failures.c.schedule_id == plans.c.id, failures.c.position == 1))
        .order_by(plans.c.id)
    )
    now = datetime.now(timezone.utc)
    notices = []
    for row in rows:
        since = as_utc(row.since)
        count = row.failure_count or 0
        if row.status == "partial" or count >= FULL_FAILURE_THRESHOLD:
            reason = row.status
            at = as_utc(row.finished_at)
        else:
            if not get_settings().backup_scheduler_enabled:
                continue
            try:
                trigger = CronTrigger.from_crontab(row.cron_expr, timezone=get_scheduler().timezone)
            except ValueError:
                continue
            due = trigger.get_next_fire_time(None, since + timedelta(microseconds=1))
            if due is None or now < as_utc(due) + OVERDUE_GRACE:
                continue
            reason = "overdue"
            at = as_utc(due)
        notices.append(
            BackupNotice(
                id=notice_id("backup_schedule_issue", user.id, row.id, since.isoformat()),
                schedule_id=row.id,
                schedule_name=row.name,
                reason=reason,
                at=at,
                count=count,
                run_id=row.run_id,
            )
        )
    return notices
