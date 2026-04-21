"""read.py 的共享层。

原 src/routers/read.py 1705 行,按路由组拆成 3 个子模块后,各 endpoint 都
依赖的 imports + helper(_resolve_ledger_name / _owner_map_for_ledgers /
_get_latest_change_id / snapshot_cache 相关 / Flutter ↔ server 字段转换 等)
集中在这里。

子模块各自关注一类资源:
  - ledgers.py    /ledgers / /ledgers/{id}/*      账本维度读
  - workspace.py  /workspace/*                    跨账本聚合读
  - summary.py    /summary                        单独小端点

修改某条 read 端点的具体查询,进对应子模块;修改共享字段映射 / 账本可见性
/ projection 读辅助,改 _shared.py 一处。
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user, require_any_scopes, require_scopes
from ...ledger_access import (
    get_accessible_ledger_by_external_id,
)
from ...models import (
    AttachmentFile,
    Ledger,
    ReadAccountProjection,
    ReadBudgetProjection,
    ReadCategoryProjection,
    ReadTagProjection,
    ReadTxProjection,
    SyncChange,
    User,
    UserProfile,
)
from ...schemas import (
    AnalyticsMetric,
    AnalyticsScope,
    ReadAccountOut,
    ReadBudgetOut,
    ReadCategoryOut,
    ReadLedgerDetailOut,
    ReadLedgerOut,
    ReadSummaryOut,
    ReadTagOut,
    ReadTransactionOut,
    WorkspaceAccountOut,
    WorkspaceAnalyticsCategoryRankOut,
    WorkspaceAnalyticsOut,
    WorkspaceAnalyticsRangeOut,
    WorkspaceAnalyticsSeriesItemOut,
    WorkspaceAnalyticsSummaryOut,
    WorkspaceCategoryOut,
    WorkspaceLedgerCountsOut,
    WorkspaceTagOut,
    WorkspaceTransactionOut,
    WorkspaceTransactionPageOut,
)
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from ... import snapshot_cache

router = APIRouter()
settings = get_settings()
_READ_SCOPE_DEP = (
    require_any_scopes(SCOPE_WEB_READ, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_WEB_READ)
)


def _is_admin(current_user: User) -> bool:
    """单用户隔离模型下,read 路由永远按 current_user 过滤 —— admin 角色只
    作用于 /admin/* 管理面板(用户列表、备份、日志等),不给读账本/交易/分类/
    标签/账户开"看所有用户数据"的后门。之前 admin 用户注册成第一个账号会
    自动被提升为 admin(见 alembic 0007_admin_bootstrap),结果 User B 登录
    就看到 User A 所有账本 —— 单用户自部署场景下这是 bug,不是 feature。
    """
    _ = current_user
    return False


def _require_ledger(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    is_admin: bool,
) -> tuple[Ledger, None]:
    """Resolve a ledger for the caller. Admin bypasses ownership check.

    Returns ``(ledger, None)`` — the second slot used to hold a LedgerMember
    row and is retained for back-compat with callers that destructure.
    """
    if is_admin:
        ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
        if ledger is None:
            raise HTTPException(status_code=404, detail="Ledger not found")
        if _is_ledger_deleted(db, ledger_id=ledger.id):
            raise HTTPException(status_code=404, detail="Ledger not found")
        return ledger, None

    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger_row, _ = row
    if _is_ledger_deleted(db, ledger_id=ledger_row.id):
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row


# ---------------------------------------------------------------------------
# Snapshot helpers — replaces all Web*Projection queries
# ---------------------------------------------------------------------------

def _get_latest_snapshot(db: Session, *, ledger_id: str) -> dict[str, Any] | None:
    """Return the parsed snapshot from the most recent ledger_snapshot SyncChange.

    payload_json 形状是 `{"content": "<json-string>", "metadata": {...}}`,
    content 是真正的 snapshot(ledgerName / items / accounts / categories / tags /
    budgets)。

    热路径:
    - 先只查该 ledger 的 `ledger_snapshot` 最大 change_id(很轻,命中索引,不读 blob)
    - 拿进程内 `snapshot_cache` 按 (ledger_id, change_id) 对账,命中直接返回 —
      跳过 3MB 行读 + 几十毫秒的 json.loads
    - 未命中才读 payload + parse + 回灌缓存

    命中率:单用户日常 dashboard 连环打 5-6 次 `/read/*`,首次 miss、其余全 hit,
    累计耗时从 ~250ms 降到 ~50ms(一次 parse 摊薄)。
    """
    latest_change_id_for_snapshot = db.scalar(
        select(func.max(SyncChange.change_id)).where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
        )
    )
    if latest_change_id_for_snapshot is None:
        return None

    cached = snapshot_cache.get(ledger_id, int(latest_change_id_for_snapshot))
    if cached is not None:
        return cached

    row = db.scalar(
        select(SyncChange.payload_json)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.change_id == latest_change_id_for_snapshot,
        )
        .limit(1)
    )
    if row is None:
        return None
    if isinstance(row, str):
        row = json.loads(row)
    parsed: dict[str, Any] | None = None
    if isinstance(row, dict):
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = row  # fallback:把原 payload_json 返回,维持老行为
        else:
            parsed = row
    if parsed is not None:
        snapshot_cache.put(ledger_id, int(latest_change_id_for_snapshot), parsed)
    return parsed


def _get_latest_change_id(db: Session, *, ledger_id: str) -> int:
    val = db.scalar(
        select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
    )
    return int(val or 0)


def _owner_map_for_ledgers(
    db: Session, ledgers: list[Ledger]
) -> dict[str, tuple[str, str | None]]:
    """Return {ledger_external_id: (user_id, email)} for the given ledgers.
    Single-user-per-ledger: every entity in a ledger was created by its owner,
    so this is the right attribution for the web tables.
    """
    user_ids = {lg.user_id for lg in ledgers}
    if not user_ids:
        return {}
    rows = db.execute(
        select(User.id, User.email).where(User.id.in_(user_ids))
    ).all()
    email_by_uid = {row[0]: row[1] for row in rows}
    return {
        lg.external_id: (lg.user_id, email_by_uid.get(lg.user_id))
        for lg in ledgers
    }


def _is_ledger_deleted(db: Session, *, ledger_id: str) -> bool:
    """True iff the latest ledger_snapshot sync change for this ledger is a
    tombstone (``action='delete'``). Used to filter / 404 deleted ledgers in
    read endpoints without dropping the underlying rows (we keep the audit
    trail under the soft-delete model)."""
    latest_action = db.scalar(
        select(SyncChange.action)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
        )
        .order_by(SyncChange.change_id.desc())
        .limit(1)
    )
    return latest_action == "delete"


def _snapshot_ledger_info(
    snapshot: dict[str, Any] | None,
    *,
    ledger: Ledger,
) -> tuple[str, str]:
    """Return (ledger_name, currency) from a snapshot, with fallbacks."""
    if snapshot:
        name = (snapshot.get("ledgerName") or "").strip()
        currency = (snapshot.get("currency") or "").strip()
    else:
        name = ""
        currency = ""
    if not name:
        name = (ledger.name or ledger.external_id).strip() or ledger.external_id
    if not currency:
        currency = "CNY"
    return name, currency


def _resolve_ledger_name(db: Session, *, ledger: Ledger) -> str:
    """Ledger.name 是权威源。边缘 case(老数据 name 为 NULL,0020 迁移错过,
    或 mobile 没 push ledger entity 过):回退到 sync_changes,并自愈写回 Ledger.name。
    自愈写入用独立 commit —— 读 endpoint 默认不提交,我们显式提交以持久化。
    """
    if ledger.name and ledger.name.strip():
        return ledger.name.strip()

    resolved: str | None = None

    # Fallback 1:最近一条 ledger entity SyncChange 的 ledgerName
    recent_ledger = db.scalar(
        select(SyncChange.payload_json).where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "ledger",
            SyncChange.action == "upsert",
        ).order_by(SyncChange.change_id.desc()).limit(1)
    )
    if recent_ledger is not None:
        payload = recent_ledger
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if isinstance(payload, dict):
            name = (payload.get("ledgerName") or "").strip()
            if name:
                resolved = name

    # Fallback 2:历史 ledger_snapshot 的 content.ledgerName(Plan B 前的数据)
    if resolved is None:
        recent_snap = db.scalar(
            select(SyncChange.payload_json).where(
                SyncChange.ledger_id == ledger.id,
                SyncChange.entity_type == "ledger_snapshot",
                SyncChange.action == "upsert",
            ).order_by(SyncChange.change_id.desc()).limit(1)
        )
        if recent_snap is not None:
            payload = recent_snap
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if isinstance(payload, dict):
                content = payload.get("content")
                if isinstance(content, str) and content.strip():
                    try:
                        snap = json.loads(content)
                        if isinstance(snap, dict):
                            name = (snap.get("ledgerName") or "").strip()
                            if name:
                                resolved = name
                    except json.JSONDecodeError:
                        pass

    if resolved is None:
        return ledger.external_id

    # 自愈持久化:显式 commit 让下次 read 直接命中 Ledger.name 快路径
    try:
        ledger.name = resolved[:255]
        db.commit()
    except Exception:  # noqa: BLE001 — 自愈失败不影响读本次返回
        db.rollback()
    return resolved


def _load_owner_identity(db: Session, *, ledger: Ledger) -> tuple[str, str | None, str | None, str | None, int]:
    """Return (user_id, email, display_name, avatar_url, avatar_version) for
    the ledger owner. Single-user-per-ledger model: every row in the ledger
    was created by the owner."""
    row = db.execute(
        select(
            User.id,
            User.email,
            UserProfile.display_name,
            UserProfile.avatar_file_id,
            UserProfile.avatar_version,
        )
        .join(UserProfile, UserProfile.user_id == User.id, isouter=True)
        .where(User.id == ledger.user_id)
    ).first()
    if row is None:
        return ledger.user_id, None, None, None, 0
    return row[0], row[1], row[2], (
        f"/api/v1/attachments/{row[3]}" if row[3] else None
    ), int(row[4] or 0)


def _tags_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _projection_totals(
    db: Session, ledger_internal_id: str
) -> tuple[int, float, float, datetime | None]:
    """从 read_tx_projection 聚合出 (count, income_total, expense_total, latest)。
    SQLite / PostgreSQL 通用:用 SQLAlchemy 的 `case` 做条件 sum。"""
    from sqlalchemy import case as sa_case

    row = db.execute(
        select(
            func.count(ReadTxProjection.sync_id),
            func.coalesce(func.sum(
                sa_case((ReadTxProjection.tx_type == "income", ReadTxProjection.amount),
                        else_=0.0)
            ), 0.0),
            func.coalesce(func.sum(
                sa_case((ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                        else_=0.0)
            ), 0.0),
            func.max(ReadTxProjection.happened_at),
        ).where(ReadTxProjection.ledger_id == ledger_internal_id)
    ).one()
    tx_count, income_total, expense_total, latest_raw = row
    return (
        int(tx_count or 0),
        float(income_total or 0),
        float(expense_total or 0),
        _to_utc(latest_raw) if latest_raw else None,
    )


def _bucket_key(scope: AnalyticsScope, happened_at: datetime) -> str:
    normalized = _to_utc(happened_at)
    if scope == "month":
        return normalized.strftime("%Y-%m-%d")
    return normalized.strftime("%Y-%m")


def _analytics_range(
    *,
    scope: AnalyticsScope,
    period: str | None,
    tz_offset_minutes: int = 0,
) -> tuple[datetime | None, datetime | None, str | None]:
    """计算 analytics 的 [start, end) UTC 范围。

    边界对应用户**本地**的月份/年份,不是 UTC —— UTC 计算会把 CST "2026-04-01 00:00"
    当成 UTC "2026-03-31 16:00",落到 3 月,导致月初那笔支出没算进 4 月。

    `tz_offset_minutes`:客户端传的本地时区偏移(正数 = 东半球),与 JavaScript
    `-new Date().getTimezoneOffset()` 同符号。中国是 +480。默认 0 = UTC(老客户端不传
    这个参数时的行为保持 UTC 切月)。
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    tz = timezone(timedelta(minutes=tz_offset_minutes))

    if scope == "all":
        return None, None, None

    if scope == "month":
        target = (
            period.strip()
            if isinstance(period, str) and period.strip()
            else now.astimezone(tz).strftime("%Y-%m")
        )
        try:
            year_part, month_part = target.split("-", 1)
            year = int(year_part)
            month = int(month_part)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Invalid analytics period") from exc
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="Invalid analytics period")
        # 本地时区月初/月末 → 转 UTC 给 SQL 用
        start_local = datetime(year, month, 1, tzinfo=tz)
        end_local = (
            datetime(year + 1, 1, 1, tzinfo=tz)
            if month == 12
            else datetime(year, month + 1, 1, tzinfo=tz)
        )
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), f"{year:04d}-{month:02d}"

    target = (
        period.strip()
        if isinstance(period, str) and period.strip()
        else now.astimezone(tz).strftime("%Y")
    )
    try:
        year = int(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid analytics period") from exc
    start_local = datetime(year, 1, 1, tzinfo=tz)
    end_local = datetime(year + 1, 1, 1, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), f"{year:04d}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


__all__ = [
    'json',
    'datetime',
    'timedelta',
    'timezone',
    'Any',
    'cast',
    'APIRouter',
    'Depends',
    'HTTPException',
    'Query',
    'and_',
    'func',
    'or_',
    'select',
    'true',
    'Session',
    'get_settings',
    'get_db',
    'get_current_user',
    'require_any_scopes',
    'require_scopes',
    'get_accessible_ledger_by_external_id',
    'AttachmentFile',
    'Ledger',
    'ReadAccountProjection',
    'ReadBudgetProjection',
    'ReadCategoryProjection',
    'ReadTagProjection',
    'ReadTxProjection',
    'SyncChange',
    'User',
    'UserProfile',
    'AnalyticsMetric',
    'AnalyticsScope',
    'ReadAccountOut',
    'ReadBudgetOut',
    'ReadCategoryOut',
    'ReadLedgerDetailOut',
    'ReadLedgerOut',
    'ReadSummaryOut',
    'ReadTagOut',
    'ReadTransactionOut',
    'WorkspaceAccountOut',
    'WorkspaceAnalyticsCategoryRankOut',
    'WorkspaceAnalyticsOut',
    'WorkspaceAnalyticsRangeOut',
    'WorkspaceAnalyticsSeriesItemOut',
    'WorkspaceAnalyticsSummaryOut',
    'WorkspaceCategoryOut',
    'WorkspaceLedgerCountsOut',
    'WorkspaceTagOut',
    'WorkspaceTransactionOut',
    'WorkspaceTransactionPageOut',
    'SCOPE_APP_WRITE',
    'SCOPE_WEB_READ',
    'snapshot_cache',
    'router',
    'settings',
    '_READ_SCOPE_DEP',
    '_is_admin',
    '_require_ledger',
    '_get_latest_snapshot',
    '_get_latest_change_id',
    '_owner_map_for_ledgers',
    '_is_ledger_deleted',
    '_snapshot_ledger_info',
    '_resolve_ledger_name',
    '_load_owner_identity',
    '_tags_list',
    '_to_utc',
    '_projection_totals',
    '_bucket_key',
    '_analytics_range',
]
