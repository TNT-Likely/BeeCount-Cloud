"""背景排程管理後台(§ 排程管理 Phase 5)。

把原本 `main.py` 裡 4 條各自獨立的 asyncio 迴圈(共 7 個邏輯排程動作)收斂
成一張 `ScheduledJobConfig` 設定表 + 這裡的統一輪詢邏輯,讓 admin 可以在
`/admin/scheduled-jobs` 後台調整頻率/停用/立即執行,不需要改代碼重新部署。

`JOB_REGISTRY` 裡每個 job 對應的底層函式都是既有、各自獨立可呼叫的
(`recurring_materializer` / `debt_reminders` / `credit_card_reminders` /
`credit_card_autopay` / `card_reward_payout`),這裡只是包一層統一成
`(Session) -> dict` 的介面 + 正規化摘要文字,不改動函式本身的行為。

`POST /internal/tasks/materialize-recurring`(`routers/internal_tasks.py`)
維持不變、繼續存在——背後呼叫的是同一批底層函式,兩邊互不干擾(各函式本身
有去重/冪等保護)。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MCPCallLog, RefreshToken, ScheduledJobConfig

logger = logging.getLogger(__name__)

_MCP_LOG_RETENTION_DAYS = 30

# refresh_tokens 每次 /auth/refresh 都會 rotate(舊列標 revoked_at、新增一
# 列),舊列本身沒有再被讀取的用途,只是審計用的歷史殘骸。留 2 天緩衝
# (而不是失效當下立刻刪)是為了保留短暫的除錯窗口——例如懷疑某次 refresh
# 失敗是不是誤判撤銷、需要回頭查最近的 token 記錄時還能查得到。
_REFRESH_TOKEN_RETENTION_DAYS = 2

# job_key -> (interval_seconds, cold_start_delay)。跟 seed migration
# (`alembic/versions/0036_scheduled_job_configs.py`)裡的預設值完全對齊——
# `ensure_default_configs` 是給測試 DB(`Base.metadata.create_all` 不會跑
# migration data seed)跟「升級後出現的新 job_key」補預設列用的,兩邊必須
# 保持一致。
_DEFAULT_JOB_CONFIGS: dict[str, tuple[int, bool]] = {
    "mcp_log_retention": (24 * 3600, True),
    "refresh_token_retention": (24 * 3600, False),
    "recurring_materializer": (24 * 3600, True),
    "debt_reminders": (15 * 60, False),
    "debt_unsettled_counterparties": (15 * 60, False),
    "card_due_reminders": (15 * 60, False),
    "transfer_rule_materialization": (15 * 60, False),
    # 股票定期定額(2026-09-28):同 transfer_rule_materialization 掛同一種
    # 頻率(到期查當下報價/餘額,不能等 24 小時)。
    "stock_dca_materialization": (15 * 60, False),
    "card_autopay": (15 * 60, False),
    "card_reward_payout": (5 * 60, False),
    "swipesmart_usage_backfill": (15 * 60, False),
    "check_latest_app_version": (30 * 60, False),
    # 股票持股(2026-09-28):每 5 分鐘檢查一次,各市場收盤後才真的打上游,
    # 見 services/securities/quotes.py::refresh_close_quotes。
    "security_quote_close": (5 * 60, False),
    # 股利(Phase 2):除權息事件每 6 小時同步一次(官方預告表每天更新);
    # 待確認股利每小時偵測一次(除息日當天早上就能收到通知)。
    "security_dividend_sync": (6 * 3600, False),
    "security_dividend_detector": (3600, False),
}


def ensure_default_configs(db: Session) -> None:
    """幂等補齊缺失的 job_key 預設列。生產環境靠 migration seed;這裡是
    第二道保險——測試 DB 用 `Base.metadata.create_all` 建表不會跑 migration
    data seed,以及未來升級後新增 job_key 時,舊部署的 DB 也需要補列。"""
    existing = set(db.scalars(select(ScheduledJobConfig.job_key)).all())
    now = _now()
    added = False
    for job_key, (interval_seconds, cold_start_delay) in _DEFAULT_JOB_CONFIGS.items():
        if job_key in existing:
            continue
        db.add(
            ScheduledJobConfig(
                job_key=job_key,
                interval_seconds=interval_seconds,
                enabled=True,
                next_run_at=(now + timedelta(seconds=interval_seconds)) if cold_start_delay else None,
                created_at=now,
                updated_at=now,
            )
        )
        added = True
    if added:
        db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_mcp_log_retention(db: Session) -> dict:
    from sqlalchemy import delete

    cutoff = _now() - timedelta(days=_MCP_LOG_RETENTION_DAYS)
    result = db.execute(delete(MCPCallLog).where(MCPCallLog.called_at < cutoff))
    deleted = result.rowcount or 0
    return {"deleted": deleted}


def _run_refresh_token_retention(db: Session) -> dict:
    from sqlalchemy import delete, or_

    cutoff = _now() - timedelta(days=_REFRESH_TOKEN_RETENTION_DAYS)
    result = db.execute(
        delete(RefreshToken).where(
            or_(
                RefreshToken.expires_at < cutoff,
                RefreshToken.revoked_at < cutoff,
            )
        )
    )
    deleted = result.rowcount or 0
    return {"deleted": deleted}


def _run_recurring_materializer(db: Session) -> dict:
    from . import recurring_materializer

    result = recurring_materializer.materialize_all_due(db)
    return {"recurring_transactions": result["recurring_transactions"]}


def _run_transfer_rule_materialization(db: Session) -> dict:
    from . import recurring_materializer

    result = recurring_materializer.materialize_due_transfer_rules(db)
    return {
        "materialized": result["materialized"],
        "skipped_insufficient": result["skipped_insufficient"],
    }


def _run_stock_dca_materialization(db: Session) -> dict:
    from . import recurring_materializer

    result = recurring_materializer.materialize_due_stock_rules(db)
    return {
        "materialized": result["materialized"],
        "skipped_insufficient": result["skipped_insufficient"],
        "skipped_no_quote": result["skipped_no_quote"],
    }


def _run_debt_reminders(db: Session) -> dict:
    from . import debt_reminders

    count = debt_reminders.send_due_debt_reminders(db)
    return {"sent": count}


def _run_debt_unsettled_counterparties(db: Session) -> dict:
    from . import debt_unsettled_notifications

    count = debt_unsettled_notifications.sync_unsettled_counterparty_notifications(db)
    return {"touched": count}


def _run_card_due_reminders(db: Session) -> dict:
    from . import credit_card_reminders

    count = credit_card_reminders.send_due_card_reminders(db)
    return {"sent": count}


def _run_card_autopay(db: Session) -> dict:
    from . import credit_card_autopay

    result = credit_card_autopay.materialize_due_card_autopay(db)
    return {
        "executed": result["executed"],
        "skipped_insufficient": result["skipped_insufficient"],
    }


def _run_card_reward_payout(db: Session) -> dict:
    from . import card_reward_payout

    result = card_reward_payout.materialize_due_card_reward_payouts(db)
    return {"tx_payouts": result["tx_payouts"], "period_payouts": result["period_payouts"]}


def _run_swipesmart_usage_backfill(db: Session) -> dict:
    from . import swipesmart_backfill

    return swipesmart_backfill.run_swipesmart_usage_backfill(db)


def _run_check_latest_app_version(db: Session) -> dict:
    """App 端新版本提醒(§3):實際偵測邏輯在 `app_version_check.py`,跟
    `routers/admin_app_version.py` 的「立即偵測」按鈕共用同一個函式 ——
    這裡只是包一層讓它出現在通用的排程管理後台(調頻率/停用/查看
    last_run_at 都走這張表,跟其它 job 一致);偵測結果本身(latest_version /
    last_checked_at / last_check_error)寫在 `AppVersionCheckConfig` 那張獨立
    的單例表上,不是這裡的 `ScheduledJobConfig`。"""
    from . import app_version_check

    return app_version_check.check_latest_app_version(db)


def _run_security_quote_close(db: Session) -> dict:
    """股票持股(docs/STOCK_HOLDINGS_SD.md):各市場收盤後抓一次持有標的的收盤
    價。還沒到收盤門檻/週末/全部已抓過時直接回 {markets: 0}。"""
    from .securities import quotes

    return quotes.refresh_close_quotes(db)


def _run_security_dividend_sync(db: Session) -> dict:
    """股票持股 Phase 2:同步交易過的標的的除權息事件(證交所/櫃買預告表 +
    Yahoo 歷史)。"""
    from .securities import dividends

    return dividends.sync_dividend_events(db)


def _run_security_dividend_detector(db: Session) -> dict:
    """股票持股 Phase 2:除息日後替持有的帳戶建待確認股利 + 發通知。"""
    from .securities import dividends

    return dividends.detect_pending_dividends(db)


# job_key -> (db) -> dict 摘要。`ensure_default_configs` 在每次啟動時自動補齊
# 這裡新登記、但舊部署 DB 裡還沒有的 job_key 列(見該函式 docstring),所以
# 新增 job 不需要另外寫 migration seed。
JOB_REGISTRY: dict[str, Callable[[Session], dict]] = {
    "mcp_log_retention": _run_mcp_log_retention,
    "refresh_token_retention": _run_refresh_token_retention,
    "recurring_materializer": _run_recurring_materializer,
    "debt_reminders": _run_debt_reminders,
    "debt_unsettled_counterparties": _run_debt_unsettled_counterparties,
    "card_due_reminders": _run_card_due_reminders,
    "transfer_rule_materialization": _run_transfer_rule_materialization,
    "stock_dca_materialization": _run_stock_dca_materialization,
    "card_autopay": _run_card_autopay,
    "card_reward_payout": _run_card_reward_payout,
    "swipesmart_usage_backfill": _run_swipesmart_usage_backfill,
    "check_latest_app_version": _run_check_latest_app_version,
    "security_quote_close": _run_security_quote_close,
    "security_dividend_sync": _run_security_dividend_sync,
    "security_dividend_detector": _run_security_dividend_detector,
}


def run_job(db: Session, job_key: str) -> dict:
    """讀設定 → 呼叫對應函式(失敗記 error 但不拋出,沿用改版前每個 tick
    都有的錯誤隔離模式)→ 更新 last_run_at/last_run_status/last_run_message/
    next_run_at → commit → 回傳摘要。job_key 不存在或設定不存在時拋
    ValueError,由 caller(router / run_due_jobs)決定怎麼處理。"""
    config = db.scalar(select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == job_key))
    if config is None:
        raise ValueError(f"unknown scheduled job: {job_key}")
    handler = JOB_REGISTRY.get(job_key)
    if handler is None:
        raise ValueError(f"no handler registered for job: {job_key}")

    now = _now()
    summary: dict
    try:
        summary = handler(db)
        config.last_run_status = "ok"
        config.last_run_message = ", ".join(f"{k}={v}" for k, v in summary.items()) or "ok"
    except Exception as exc:  # noqa: BLE001 — 排程失敗要記錄，不能讓迴圈掛掉
        db.rollback()
        logger.exception("scheduled job failed: job_key=%s", job_key)
        reloaded = db.scalar(select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == job_key))
        if reloaded is None:
            raise ValueError(f"scheduled job disappeared mid-run: {job_key}") from exc
        config = reloaded
        summary = {"error": str(exc)}
        config.last_run_status = "error"
        config.last_run_message = str(exc)[:500]

    config.last_run_at = now
    config.next_run_at = now + timedelta(seconds=config.interval_seconds)
    config.updated_at = now
    db.commit()

    return {
        "job_key": job_key,
        "status": config.last_run_status,
        "message": config.last_run_message,
        "summary": summary,
        "last_run_at": config.last_run_at,
        "next_run_at": config.next_run_at,
    }


def run_due_jobs(db: Session) -> list[dict]:
    """掃描所有 enabled=True 且到期(next_run_at is None or <= now)的設定,
    逐一呼叫 run_job。主迴圈(`main.py::_start_scheduled_jobs_loop`)每 60
    秒呼叫一次。"""
    now = _now()
    due = db.scalars(
        select(ScheduledJobConfig).where(
            ScheduledJobConfig.enabled.is_(True),
            (ScheduledJobConfig.next_run_at.is_(None)) | (ScheduledJobConfig.next_run_at <= now),
        )
    ).all()
    results = []
    for config in due:
        results.append(run_job(db, config.job_key))
    return results
