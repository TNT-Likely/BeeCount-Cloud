"""APScheduler 启动 + 从 DB 装载所有启用的 schedule。

走 BackgroundScheduler(线程池),避免 rclone subprocess 长任务阻塞 FastAPI
事件循环。

job_id 格式:`backup-schedule-{schedule_id}`,1 对 1 映射 DB 行。

调用约定:
  - app startup:`get_scheduler().start_from_db()` 装载并启动
  - DB 增删改 schedule 后:`get_scheduler().reload()` 重新装载
  - app shutdown:`get_scheduler().shutdown()`

进度回调通过 `get_scheduler().on_progress(fn)` 注册;run 时 broadcast 到 WS。

时区:cron 表达式用什么时区解释非常关键 ——「0 4 * * *」在 UTC 跟在
Asia/Shanghai 差 8 小时。我们显式传 timezone 给 BackgroundScheduler,
读取顺序:settings.scheduler_timezone (env SCHEDULER_TIMEZONE) → tzlocal
(TZ env / /etc/localtime) → UTC fallback。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ...config import get_settings
from ...database import SessionLocal
from ...models import BackupSchedule
from .runner import run_backup


logger = logging.getLogger(__name__)


_singleton: "BackupScheduler | None" = None
_lock = threading.Lock()


def get_scheduler() -> "BackupScheduler":
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = BackupScheduler()
        return _singleton


def _resolve_scheduler_tz() -> Any:
    """决定 BackgroundScheduler 用哪个 timezone。

    1) settings.scheduler_timezone 显式配置 → 用之
    2) tzlocal 自动检测(TZ env / /etc/localtime) → 用之
    3) UTC fallback(打 warning,提示用户配 TZ env)
    """
    settings = get_settings()
    explicit = (settings.scheduler_timezone or "").strip()
    if explicit:
        try:
            return ZoneInfo(explicit)
        except ZoneInfoNotFoundError:
            logger.warning(
                "SCHEDULER_TIMEZONE=%r is not a valid IANA TZ; falling back to local",
                explicit,
            )
    try:
        import tzlocal  # type: ignore[import-not-found]

        local = tzlocal.get_localzone()
        # tzlocal v5 returns a zoneinfo.ZoneInfo; v4 may return pytz.tzfile.
        # Both work with APScheduler.
        logger.info("scheduler timezone resolved via tzlocal: %s", local)
        return local
    except Exception as exc:
        logger.warning(
            "tzlocal failed (%s) — using UTC. cron 'H' fields will be in UTC. "
            "Set SCHEDULER_TIMEZONE=Asia/Shanghai (or your TZ) in env to fix.",
            exc,
        )
        return ZoneInfo("UTC")


class BackupScheduler:
    """APScheduler wrapper。"""

    def __init__(self) -> None:
        self._timezone = _resolve_scheduler_tz()
        self._scheduler = BackgroundScheduler(
            timezone=self._timezone,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 3600,
            },
        )
        self._started = False
        self._on_progress: Callable[[str, dict[str, Any]], None] | None = None
        # on_progress 回调签名: (user_id, event_dict)。run_backup 内部把
        # event 推给我们,我们 multiplex 给注册的 listener(WS)。

    def on_progress(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        self._on_progress = fn

    @property
    def timezone(self) -> Any:
        return self._timezone

    # ----------------------------------------------------------------- start

    def start_from_db(self) -> None:
        if self._started:
            return
        settings = get_settings()
        if not settings.backup_scheduler_enabled:
            logger.info("backup scheduler disabled by config")
            return
        self._scheduler.start()
        self._started = True
        self.reload()

    def shutdown(self) -> None:
        if not self._started:
            return
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("scheduler shutdown failed")
        self._started = False

    # ------------------------------------------------------------- reload

    def reload(self) -> None:
        """读 DB 里所有 enabled schedule,重新 add_job。已有 jobs 全清掉。"""
        if not self._started:
            return
        # 清掉旧 jobs
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith("backup-schedule-"):
                self._scheduler.remove_job(job.id)

        db = SessionLocal()
        try:
            rows = db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).all()
            for s in rows:
                self._add_job(s)
            logger.info("backup scheduler reloaded: %d jobs", len(rows))
        finally:
            db.close()

    def _add_job(self, schedule: BackupSchedule) -> None:
        try:
            # 显式传 timezone 给 CronTrigger,避免 APScheduler 走默认 → 跟
            # scheduler 的 timezone 不一致导致 next_run 时间错位。
            trigger = CronTrigger.from_crontab(schedule.cron_expr, timezone=self._timezone)
        except ValueError:
            logger.error("invalid cron_expr for schedule %s: %s", schedule.id, schedule.cron_expr)
            return
        sid = schedule.id
        uid = schedule.user_id
        job_id = f"backup-schedule-{sid}"

        def _job_fn(schedule_id: int = sid, user_id: str = uid) -> None:
            def _push(event: dict[str, Any]) -> None:
                if self._on_progress is not None:
                    try:
                        self._on_progress(user_id, event)
                    except Exception:
                        logger.exception("scheduler on_progress raised")

            try:
                run_backup(user_id=user_id, schedule_id=schedule_id, on_progress=_push)
            except Exception:
                logger.exception("scheduled backup failed user=%s schedule=%s", user_id, schedule_id)

        self._scheduler.add_job(
            _job_fn,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
        )
        # 把 next_run 写回 DB(给 UI 展示)。APScheduler 返回的
        # next_run_time 是 scheduler timezone 的 tz-aware datetime
        # (例如 Asia/Shanghai)。SQLite 不保留 tz,统一转成 UTC tz-aware
        # 后存,_build_schedule_out 里 _utc() 拿出来标 UTC,前端
        # toLocaleString 自动按用户时区展示。
        from datetime import timezone

        db = SessionLocal()
        try:
            row = db.get(BackupSchedule, sid)
            if row is not None:
                next_run = self._scheduler.get_job(job_id).next_run_time
                if next_run is not None and next_run.tzinfo is not None:
                    next_run = next_run.astimezone(timezone.utc)
                row.next_run_at = next_run
                db.commit()
        finally:
            db.close()
