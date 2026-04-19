import json
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes, require_scopes
from ..ledger_access import (
    get_accessible_ledger_by_external_id,
)
from ..models import (
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
from ..schemas import (
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
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from .. import snapshot_cache

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
    """优先从 snapshot.ledgerName 拿显示名(mobile 早期没往 Ledger.name 写,
    列表/下拉会出 UUID 样的 external_id)。snapshot_cache 命中后约 1ms。"""
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    if isinstance(snapshot, dict):
        raw = snapshot.get("ledgerName")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ledger.name or ledger.external_id


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
) -> tuple[datetime | None, datetime | None, str | None]:
    now = datetime.now(timezone.utc)
    if scope == "all":
        return None, None, None

    if scope == "month":
        target = period.strip() if isinstance(period, str) and period.strip() else now.strftime("%Y-%m")
        try:
            year_part, month_part = target.split("-", 1)
            year = int(year_part)
            month = int(month_part)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Invalid analytics period") from exc
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="Invalid analytics period")
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(
            year, month + 1, 1, tzinfo=timezone.utc
        )
        return start, end, f"{year:04d}-{month:02d}"

    target = period.strip() if isinstance(period, str) and period.strip() else now.strftime("%Y")
    try:
        year = int(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid analytics period") from exc
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return start, end, f"{year:04d}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/ledgers", response_model=list[ReadLedgerOut])
def list_ledgers(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadLedgerOut]:
    if _is_admin(current_user):
        ledgers = list(db.scalars(select(Ledger).order_by(Ledger.created_at.desc())).all())
    else:
        ledgers = list(
            db.scalars(
                select(Ledger)
                .where(Ledger.user_id == current_user.id)
                .order_by(Ledger.created_at.desc())
            ).all()
        )

    out: list[ReadLedgerOut] = []
    for ledger in ledgers:
        # Hide soft-deleted ledgers.
        if _is_ledger_deleted(db, ledger_id=ledger.id):
            continue
        # currency 暂不做 projection 化 —— 顶层元数据非热点,snapshot_cache 命中
        # 后 ~1ms,偶发 cold miss 50ms 可接受;list_ledgers 本身调用频率低。
        snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
        _, currency = _snapshot_ledger_info(snapshot, ledger=ledger)
        ledger_name = _resolve_ledger_name(db, ledger=ledger)
        tx_count, income_total, expense_total, _ = _projection_totals(db, ledger.id)
        now = datetime.now(timezone.utc)
        role = cast("Any", "owner" if ledger.user_id == current_user.id else "viewer")
        out.append(
            ReadLedgerOut(
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
                currency=currency,
                transaction_count=tx_count,
                income_total=income_total,
                expense_total=expense_total,
                balance=income_total - expense_total,
                exported_at=now,
                updated_at=now,
                role=role,
                is_shared=False,
                member_count=1,
            )
        )
    return out


@router.get("/ledgers/{ledger_external_id}/stats")
def get_ledger_stats(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """给 mobile 的"深度同步检测"用。返回 server 实际的 tx / attachment / budget
    数,mobile 拉下来跟本地 Drift 对比,检测到差异就触发自动 sync。

    tx_count 从最新 snapshot 的 items 长度算(和 /read/ledgers 保持一致)。
    attachment_count 从 attachment_files 表按 ledger_id 直接 COUNT。
    budget_count 从 snapshot.budgets 长度算(Feature 3b 后生效,materializer
    已经把 budget 写进 snapshot 了)。
    """
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=_is_admin(current_user),
    )

    # per-ledger count:单 SQL COUNT,不再 parse snapshot
    def _count(model) -> int:
        return int(db.scalar(
            select(func.count()).select_from(model).where(model.ledger_id == ledger.id)
        ) or 0)

    tx_count = _count(ReadTxProjection)
    budget_count = _count(ReadBudgetProjection)
    account_count = _count(ReadAccountProjection)
    category_count = _count(ReadCategoryProjection)
    tag_count = _count(ReadTagProjection)

    attachment_count = db.scalar(
        select(func.count(AttachmentFile.id)).where(
            AttachmentFile.ledger_id == ledger.id
        )
    ) or 0

    # 全局口径:跨当前用户所有账本。projection 的 user_id 列已经 denormalized,
    # 一次 SQL COUNT + COUNT DISTINCT 就出全量。比原来循环 parse 每个 snapshot
    # 快 N 倍。
    user_ledger_ids_subq = (
        select(Ledger.id).where(Ledger.user_id == current_user.id).scalar_subquery()
    )

    def _count_total(model) -> int:
        return int(db.scalar(
            select(func.count()).select_from(model)
            .where(model.user_id == current_user.id)
        ) or 0)

    def _count_distinct_sync(model) -> int:
        return int(db.scalar(
            select(func.count(func.distinct(model.sync_id)))
            .where(model.user_id == current_user.id)
        ) or 0)

    tx_total = _count_total(ReadTxProjection)
    budget_total = _count_total(ReadBudgetProjection)
    account_total = _count_distinct_sync(ReadAccountProjection)
    category_total = _count_distinct_sync(ReadCategoryProjection)
    tag_total = _count_distinct_sync(ReadTagProjection)

    attachment_total = int(
        db.scalar(
            select(func.count(AttachmentFile.id)).where(
                AttachmentFile.ledger_id.in_(user_ledger_ids_subq)
            )
        )
        or 0
    )

    return {
        "transaction_count": tx_count,
        "transaction_total": tx_total,
        "attachment_count": int(attachment_count),
        "attachment_total": attachment_total,
        "budget_count": budget_count,
        "budget_total": budget_total,
        "account_count": account_count,
        "account_total": account_total,
        "category_count": category_count,
        "category_total": category_total,
        "tag_count": tag_count,
        "tag_total": tag_total,
    }


@router.get("/ledgers/{ledger_external_id}", response_model=ReadLedgerDetailOut)
def get_ledger(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadLedgerDetailOut:
    ledger, member = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=_is_admin(current_user),
    )
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    _, currency = _snapshot_ledger_info(snapshot, ledger=ledger)
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    tx_count, income_total, expense_total, _ = _projection_totals(db, ledger.id)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    now = datetime.now(timezone.utc)
    return ReadLedgerDetailOut(
        ledger_id=ledger.external_id,
        ledger_name=ledger_name,
        currency=currency,
        transaction_count=tx_count,
        income_total=income_total,
        expense_total=expense_total,
        balance=income_total - expense_total,
        exported_at=now,
        updated_at=now,
        source_change_id=source_change_id,
        role=cast("Any", member.role if member is not None else "viewer"),
        is_shared=False,
        member_count=1,
    )


@router.get("/ledgers/{ledger_external_id}/transactions", response_model=list[ReadTransactionOut])
def list_transactions(
    ledger_external_id: str,
    tx_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTransactionOut]:
    # CQRS 读路径:不再 parse snapshot,直接查 read_tx_projection + index。
    # account/category/tag 的 name 已在写入时 denormalized 到 projection 列,
    # rename 时同事务级联更新(见 projection.rename_cascade_*)。
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    owner_id, owner_email, owner_display, owner_avatar, owner_avatar_ver = (
        _load_owner_identity(db, ledger=ledger)
    )

    query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger.id)
    if tx_type:
        query = query.where(ReadTxProjection.tx_type == tx_type)
    if start_at:
        query = query.where(ReadTxProjection.happened_at >= _to_utc(start_at))
    if end_at:
        query = query.where(ReadTxProjection.happened_at <= _to_utc(end_at))
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(
            ReadTxProjection.note.ilike(pattern),
            ReadTxProjection.category_name.ilike(pattern),
            ReadTxProjection.account_name.ilike(pattern),
            ReadTxProjection.from_account_name.ilike(pattern),
            ReadTxProjection.to_account_name.ilike(pattern),
            ReadTxProjection.tags_csv.ilike(pattern),
        ))
    query = query.order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    ).offset(offset).limit(limit)
    rows = db.scalars(query).all()

    results: list[ReadTransactionOut] = []
    for row in rows:
        tag_ids: list[str] = []
        if row.tag_sync_ids_json:
            try:
                maybe = json.loads(row.tag_sync_ids_json)
                if isinstance(maybe, list):
                    tag_ids = [str(t) for t in maybe]
            except json.JSONDecodeError:
                tag_ids = []
        attachments: list[dict[str, Any]] | None = None
        if row.attachments_json:
            try:
                maybe_att = json.loads(row.attachments_json)
                if isinstance(maybe_att, list):
                    attachments = maybe_att
            except json.JSONDecodeError:
                attachments = None
        results.append(
            ReadTransactionOut(
                id=row.sync_id,
                tx_index=row.tx_index,
                tx_type=row.tx_type,
                amount=row.amount,
                happened_at=_to_utc(row.happened_at),
                note=row.note,
                category_name=row.category_name,
                category_kind=row.category_kind,
                account_name=row.account_name,
                from_account_name=row.from_account_name,
                to_account_name=row.to_account_name,
                category_id=row.category_sync_id,
                account_id=row.account_sync_id,
                from_account_id=row.from_account_sync_id,
                to_account_id=row.to_account_sync_id,
                tags=row.tags_csv or None,
                tags_list=_tags_list(row.tags_csv),
                tag_ids=tag_ids,
                attachments=attachments,
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
                created_by_user_id=owner_id,
                created_by_email=owner_email,
                created_by_display_name=owner_display,
                created_by_avatar_url=owner_avatar,
                created_by_avatar_version=owner_avatar_ver,
            )
        )
    return results


@router.get("/ledgers/{ledger_external_id}/accounts", response_model=list[ReadAccountOut])
def list_accounts(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadAccountOut]:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    rows = db.scalars(
        select(ReadAccountProjection)
        .where(ReadAccountProjection.ledger_id == ledger.id)
        .order_by(ReadAccountProjection.name.asc())
    ).all()
    return [
        ReadAccountOut(
            id=row.sync_id,
            name=row.name or "",
            account_type=row.account_type or "",
            currency=row.currency or "",
            initial_balance=float(row.initial_balance or 0.0),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for row in rows
    ]


@router.get("/ledgers/{ledger_external_id}/categories", response_model=list[ReadCategoryOut])
def list_categories(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCategoryOut]:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    rows = db.scalars(
        select(ReadCategoryProjection)
        .where(ReadCategoryProjection.ledger_id == ledger.id)
        .order_by(
            ReadCategoryProjection.kind.asc(),
            ReadCategoryProjection.sort_order.asc(),
            ReadCategoryProjection.name.asc(),
        )
    ).all()
    return [
        ReadCategoryOut(
            id=row.sync_id,
            name=row.name or "",
            kind=row.kind or "",
            level=int(row.level or 0),
            sort_order=int(row.sort_order or 0),
            icon=row.icon,
            icon_type=row.icon_type,
            custom_icon_path=row.custom_icon_path,
            icon_cloud_file_id=row.icon_cloud_file_id,
            icon_cloud_sha256=row.icon_cloud_sha256,
            parent_name=row.parent_name,
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for row in rows
    ]


@router.get("/ledgers/{ledger_external_id}/budgets", response_model=list[ReadBudgetOut])
def list_budgets(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadBudgetOut]:
    """预算只读列表。mobile Feature 3b 之后,snapshot.budgets 由 server
    materializer 维护,这里按 categoryId syncId 反查 category name 填上,
    跟 tx/tag 接口同一套 id→name 映射思路。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    # category name 来自 projection 的 category_name 列 JOIN(SQLAlchemy ORM
    # 做 LEFT JOIN + aliased,确保 category 被删除时也能 fallback None)
    cat_rows = db.execute(
        select(
            ReadCategoryProjection.sync_id,
            ReadCategoryProjection.name,
        ).where(ReadCategoryProjection.ledger_id == ledger.id)
    ).all()
    cat_name_by_sync: dict[str, str] = {r.sync_id: (r.name or "").strip() for r in cat_rows}

    # 展示前做两步脏数据过滤(来自早期同步 bug 遗留):
    #   1) 分类预算但 category_sync_id 为空 —— 孤儿
    #   2) (type, category_sync_id) 维度去重 —— 按 sync_id 字典序最大的留
    raw = db.scalars(
        select(ReadBudgetProjection).where(ReadBudgetProjection.ledger_id == ledger.id)
    ).all()
    dedup: dict[tuple[str, str], ReadBudgetProjection] = {}
    for b in raw:
        btype = b.budget_type or "total"
        if btype == "category" and not b.category_sync_id:
            continue
        key = (btype, b.category_sync_id or "")
        current = dedup.get(key)
        if current is None or current.sync_id < b.sync_id:
            dedup[key] = b

    results: list[ReadBudgetOut] = []
    for b in dedup.values():
        results.append(
            ReadBudgetOut(
                id=b.sync_id,
                type=b.budget_type or "total",
                category_id=b.category_sync_id,
                category_name=cat_name_by_sync.get(b.category_sync_id) if b.category_sync_id else None,
                amount=float(b.amount or 0),
                period=b.period or "monthly",
                start_day=int(b.start_day or 1),
                enabled=bool(b.enabled),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return results


@router.get("/ledgers/{ledger_external_id}/tags", response_model=list[ReadTagOut])
def list_tags(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTagOut]:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    rows = db.scalars(
        select(ReadTagProjection)
        .where(ReadTagProjection.ledger_id == ledger.id)
        .order_by(ReadTagProjection.name.asc())
    ).all()
    return [
        ReadTagOut(
            id=row.sync_id,
            name=row.name or "",
            color=row.color,
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for row in rows
    ]


@router.get("/summary", response_model=ReadSummaryOut)
def get_summary(
    ledger_id: str = Query(..., min_length=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadSummaryOut:
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
        is_admin=is_admin,
    )
    tx_count, income_total, expense_total, latest_happened_at = _projection_totals(db, ledger.id)

    return ReadSummaryOut(
        ledger_id=ledger_id,
        transaction_count=tx_count,
        income_total=income_total,
        expense_total=expense_total,
        balance=income_total - expense_total,
        latest_happened_at=latest_happened_at,
    )


@router.get("/workspace/transactions", response_model=WorkspaceTransactionPageOut)
def list_workspace_transactions(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tx_type: str | None = Query(default=None),
    account_name: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceTransactionPageOut:
    is_admin = _is_admin(current_user)

    # 账本筛选 → 内部 id 列表,projection 用 internal id 对账
    ledger_conditions: list[Any] = []
    if ledger_id:
        ledger_conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            ledger_conditions.append(Ledger.user_id == user_id)
    else:
        ledger_conditions.append(Ledger.user_id == current_user.id)
    ledgers = list(db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all())
    if not ledgers:
        return WorkspaceTransactionPageOut(items=[], total=0, limit=limit, offset=offset)

    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta: dict[str, tuple[str, str]] = {
        l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers
    }
    # 各账本的最新 change_id —— 客户端比对用
    change_id_by_ledger: dict[str, int] = {}
    for l in ledgers:
        change_id_by_ledger[l.id] = _get_latest_change_id(db, ledger_id=l.id)

    owner_map = _owner_map_for_ledgers(db, ledgers)

    # 组装 projection query:filter + sort + paginate 全交给 SQL + index
    query = select(ReadTxProjection).where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
    if tx_type:
        query = query.where(ReadTxProjection.tx_type == tx_type)
    if account_name:
        pattern = f"%{account_name}%"
        query = query.where(or_(
            ReadTxProjection.account_name.ilike(pattern),
            ReadTxProjection.from_account_name.ilike(pattern),
            ReadTxProjection.to_account_name.ilike(pattern),
        ))
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(
            ReadTxProjection.note.ilike(pattern),
            ReadTxProjection.category_name.ilike(pattern),
            ReadTxProjection.account_name.ilike(pattern),
            ReadTxProjection.from_account_name.ilike(pattern),
            ReadTxProjection.to_account_name.ilike(pattern),
            ReadTxProjection.tags_csv.ilike(pattern),
        ))

    total = int(db.scalar(
        select(func.count()).select_from(query.subquery())
    ) or 0)

    query = query.order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    ).offset(offset).limit(limit)
    rows = db.scalars(query).all()

    out_items: list[WorkspaceTransactionOut] = []
    for row in rows:
        led_ext_id, led_name = ledger_meta.get(row.ledger_id, ("", ""))
        change_id = change_id_by_ledger.get(row.ledger_id, 0)
        owner_info = owner_map.get(led_ext_id) or (None, None)

        tag_ids: list[str] = []
        if row.tag_sync_ids_json:
            try:
                maybe = json.loads(row.tag_sync_ids_json)
                if isinstance(maybe, list):
                    tag_ids = [str(t) for t in maybe]
            except json.JSONDecodeError:
                tag_ids = []
        attachments: list[dict[str, Any]] | None = None
        if row.attachments_json:
            try:
                maybe_att = json.loads(row.attachments_json)
                if isinstance(maybe_att, list):
                    attachments = maybe_att
            except json.JSONDecodeError:
                attachments = None

        out_items.append(
            WorkspaceTransactionOut(
                id=row.sync_id,
                tx_index=row.tx_index,
                tx_type=row.tx_type,
                amount=row.amount,
                happened_at=_to_utc(row.happened_at),
                note=row.note,
                category_name=row.category_name,
                category_kind=row.category_kind,
                account_name=row.account_name,
                from_account_name=row.from_account_name,
                to_account_name=row.to_account_name,
                category_id=row.category_sync_id,
                account_id=row.account_sync_id,
                from_account_id=row.from_account_sync_id,
                to_account_id=row.to_account_sync_id,
                tags=row.tags_csv or None,
                tags_list=_tags_list(row.tags_csv),
                tag_ids=tag_ids,
                attachments=attachments,
                last_change_id=change_id,
                ledger_id=led_ext_id,
                ledger_name=led_name,
                created_by_user_id=owner_info[0],
                created_by_email=owner_info[1],
                created_by_display_name=None,
                created_by_avatar_url=None,
                created_by_avatar_version=None,
            )
        )
    return WorkspaceTransactionPageOut(
        items=out_items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/workspace/accounts", response_model=list[WorkspaceAccountOut])
def list_workspace_accounts(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceAccountOut]:
    is_admin = _is_admin(current_user)

    # --- 1. 从 snapshot 聚合账户（手机同步写入的数据） ---
    ledger_conditions: list[Any] = []
    if ledger_id:
        ledger_conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            ledger_conditions.append(Ledger.user_id == user_id)
    else:
        ledger_conditions.append(Ledger.user_id == current_user.id)

    ledgers = db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all()

    ledgers = list(ledgers)
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers}
    change_id_by_ledger = {l.id: _get_latest_change_id(db, ledger_id=l.id) for l in ledgers}

    # 一次 SQL GROUP BY 出所有账户的 tx 聚合(count/income/expense/transfer 进出)。
    # 每条 tx 可能同时计入 from/to/main account,按 (ledger_id, account) 聚合。
    from sqlalchemy import case as sa_case

    # Main account stats: income + expense
    main_stats = db.execute(
        select(
            ReadTxProjection.ledger_id,
            ReadTxProjection.account_sync_id,
            func.count().label("cnt"),
            func.coalesce(func.sum(sa_case(
                (ReadTxProjection.tx_type == "income", ReadTxProjection.amount),
                else_=0.0)), 0.0).label("income"),
            func.coalesce(func.sum(sa_case(
                (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                else_=0.0)), 0.0).label("expense"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.account_sync_id.is_not(None),
            ReadTxProjection.tx_type.in_(["income", "expense"]),
        ).group_by(ReadTxProjection.ledger_id, ReadTxProjection.account_sync_id)
    ).all()

    # Transfer adjustments: from_account = minus, to_account = plus
    transfer_from = db.execute(
        select(
            ReadTxProjection.ledger_id,
            ReadTxProjection.from_account_sync_id,
            func.count().label("cnt"),
            func.coalesce(func.sum(ReadTxProjection.amount), 0.0).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.from_account_sync_id.is_not(None),
        ).group_by(ReadTxProjection.ledger_id, ReadTxProjection.from_account_sync_id)
    ).all()
    transfer_to = db.execute(
        select(
            ReadTxProjection.ledger_id,
            ReadTxProjection.to_account_sync_id,
            func.count().label("cnt"),
            func.coalesce(func.sum(ReadTxProjection.amount), 0.0).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.to_account_sync_id.is_not(None),
        ).group_by(ReadTxProjection.ledger_id, ReadTxProjection.to_account_sync_id)
    ).all()

    # 合并成 per-ledger + per-account 的 dict
    stats: dict[tuple[str, str], dict[str, float | int]] = {}
    for lid, acc, cnt, inc, exp in main_stats:
        stats[(lid, acc)] = {"count": int(cnt), "income": float(inc),
                             "expense": float(exp), "balance": float(inc) - float(exp)}
    for lid, acc, cnt, amt in transfer_from:
        bucket = stats.setdefault((lid, acc),
                                   {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0})
        bucket["count"] = int(bucket["count"]) + int(cnt)
        bucket["balance"] = float(bucket["balance"]) - float(amt)
    for lid, acc, cnt, amt in transfer_to:
        bucket = stats.setdefault((lid, acc),
                                   {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0})
        bucket["count"] = int(bucket["count"]) + int(cnt)
        bucket["balance"] = float(bucket["balance"]) + float(amt)

    # 账户从 projection 列出
    account_query = select(ReadAccountProjection).where(
        ReadAccountProjection.ledger_id.in_(ledger_internal_ids)
    )
    if q:
        account_query = account_query.where(ReadAccountProjection.name.ilike(f"%{q}%"))
    account_rows: list[tuple[str, WorkspaceAccountOut]] = []
    for acct in db.scalars(account_query).all():
        name = (acct.name or "").strip()
        if not name:
            continue
        sync_id = acct.sync_id
        init_bal = float(acct.initial_balance or 0.0)
        led_ext_id, led_name = ledger_meta.get(acct.ledger_id, ("", ""))
        change_id = change_id_by_ledger.get(acct.ledger_id, 0)
        bucket = stats.get((acct.ledger_id, sync_id))
        income_total = float(bucket.get("income", 0.0)) if bucket else 0.0
        expense_total = float(bucket.get("expense", 0.0)) if bucket else 0.0
        tx_count = int(bucket.get("count", 0)) if bucket else 0
        movement = float(bucket.get("balance", 0.0)) if bucket else 0.0
        account_rows.append(
            (
                sync_id.lower() if sync_id else name.lower(),
                WorkspaceAccountOut(
                    id=sync_id,
                    name=name,
                    account_type=acct.account_type,
                    currency=acct.currency,
                    initial_balance=init_bal,
                    last_change_id=change_id,
                    ledger_id=led_ext_id,
                    ledger_name=led_name,
                    created_by_user_id=None,
                    created_by_email=None,
                    tx_count=tx_count,
                    income_total=income_total,
                    expense_total=expense_total,
                    balance=init_bal + movement,
                ),
            )
        )
    best_by_key: dict[str, WorkspaceAccountOut] = {}
    for key, entry in account_rows:
        existing = best_by_key.get(key)
        if existing is None or (entry.last_change_id or 0) > (existing.last_change_id or 0):
            best_by_key[key] = entry
    name_seen_acct: dict[str, WorkspaceAccountOut] = {}
    for entry in best_by_key.values():
        nk = (entry.name or "").lower()
        prev = name_seen_acct.get(nk)
        if prev is None or (entry.last_change_id or 0) > (prev.last_change_id or 0):
            name_seen_acct[nk] = entry
    all_accounts: list[WorkspaceAccountOut] = list(name_seen_acct.values())

    # 历史上这里还会合并 UserAccount 表（web 直接建账户的兜底来源）。
    # 问题：mobile 重命名 A→B，snapshot 更到 B，但 UserAccount 里还有 A（那是
    # 之前 web 给 tx 指定账户时 _get_or_create_user_account 落下的条目），名字
    # 不匹配 → 列表 A/B 同时出现，看起来"新建了一个"。
    # 账户本就是 user-global 的，snapshot 里已经是权威来源；UserAccount 只用来
    # 给 tx write 做 id→name 回填，不再混进列表，避免重命名后陈旧残留。

    # 对来自 snapshot 的条目（ledger_id 非空 + created_by_user_id 为空）
    # 填充账本 owner 身份；单用户单账本模型下这就是真正的创建人。
    owner_map = _owner_map_for_ledgers(db, list(ledgers))
    for e in all_accounts:
        if e.created_by_user_id is None and e.ledger_id in owner_map:
            uid, email = owner_map[e.ledger_id]
            e.created_by_user_id = uid
            e.created_by_email = email

    # Sort by name, then paginate
    all_accounts.sort(key=lambda a: (a.name or "").lower())
    return all_accounts[offset : offset + limit]


@router.get("/workspace/categories", response_model=list[WorkspaceCategoryOut])
def list_workspace_categories(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceCategoryOut]:
    is_admin = _is_admin(current_user)

    # --- 1. 从 snapshot 聚合分类（手机同步写入的数据） ---
    ledger_conditions: list[Any] = []
    if ledger_id:
        ledger_conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            ledger_conditions.append(Ledger.user_id == user_id)
    else:
        ledger_conditions.append(Ledger.user_id == current_user.id)

    ledgers = db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all()

    ledgers = list(ledgers)
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers}
    change_id_by_ledger = {l.id: _get_latest_change_id(db, ledger_id=l.id) for l in ledgers}

    cat_query = select(ReadCategoryProjection).where(
        ReadCategoryProjection.ledger_id.in_(ledger_internal_ids)
    )
    if q:
        cat_query = cat_query.where(ReadCategoryProjection.name.ilike(f"%{q}%"))

    cat_rows: list[tuple[str, WorkspaceCategoryOut]] = []
    for cat in db.scalars(cat_query).all():
        name = (cat.name or "").strip()
        if not name:
            continue
        kind = cat.kind or "expense"
        sync_id = cat.sync_id
        led_ext_id, led_name = ledger_meta.get(cat.ledger_id, ("", ""))
        change_id = change_id_by_ledger.get(cat.ledger_id, 0)
        key = sync_id.lower() if sync_id else f"{kind}:{name.lower()}"
        cat_rows.append(
            (
                key,
                WorkspaceCategoryOut(
                    id=sync_id,
                    name=name,
                    kind=kind,
                    level=int(cat.level or 1),
                    sort_order=int(cat.sort_order or 0),
                    icon=cat.icon,
                    icon_type=cat.icon_type,
                    custom_icon_path=cat.custom_icon_path,
                    icon_cloud_file_id=cat.icon_cloud_file_id,
                    icon_cloud_sha256=cat.icon_cloud_sha256,
                    parent_name=cat.parent_name,
                    last_change_id=change_id,
                    ledger_id=led_ext_id,
                    ledger_name=led_name,
                    created_by_user_id=None,
                    created_by_email=None,
                ),
            )
        )
    best_by_key: dict[str, WorkspaceCategoryOut] = {}
    for key, entry in cat_rows:
        existing = best_by_key.get(key)
        if existing is None or (entry.last_change_id or 0) > (existing.last_change_id or 0):
            best_by_key[key] = entry
    kindname_seen: dict[str, WorkspaceCategoryOut] = {}
    for entry in best_by_key.values():
        kk = f"{entry.kind}:{(entry.name or '').lower()}"
        prev = kindname_seen.get(kk)
        if prev is None or (entry.last_change_id or 0) > (prev.last_change_id or 0):
            kindname_seen[kk] = entry
    all_categories: list[WorkspaceCategoryOut] = list(kindname_seen.values())

    # 不再合并 UserCategory 表。原因同 list_workspace_accounts：
    # UserCategory 是 web 给 tx 指定分类时 _get_or_create_user_category 落下的
    # 旁枝，mobile 重命名后这里会残留旧名字，导致列表"看起来多了一个"。
    # snapshot 是权威来源。

    # Snapshot 条目填 owner 身份，见 list_workspace_accounts 同样处理。
    owner_map = _owner_map_for_ledgers(db, list(ledgers))
    for e in all_categories:
        if e.created_by_user_id is None and e.ledger_id in owner_map:
            uid, email = owner_map[e.ledger_id]
            e.created_by_user_id = uid
            e.created_by_email = email

    # Sort by kind, sort_order, name, then paginate
    all_categories.sort(key=lambda c: (c.kind or "", c.sort_order or 0, (c.name or "").lower()))
    return all_categories[offset : offset + limit]


@router.get("/workspace/tags", response_model=list[WorkspaceTagOut])
def list_workspace_tags(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceTagOut]:
    is_admin = _is_admin(current_user)

    # --- 1. 从 snapshot 聚合标签（手机同步写入的数据） ---
    ledger_conditions: list[Any] = []
    if ledger_id:
        ledger_conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            ledger_conditions.append(Ledger.user_id == user_id)
    else:
        ledger_conditions.append(Ledger.user_id == current_user.id)

    ledgers = db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all()

    all_tags: list[WorkspaceTagOut] = []

    ledgers = list(ledgers)
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers}
    change_id_by_ledger = {l.id: _get_latest_change_id(db, ledger_id=l.id) for l in ledgers}

    tag_query = select(ReadTagProjection).where(
        ReadTagProjection.ledger_id.in_(ledger_internal_ids)
    )
    if q:
        tag_query = tag_query.where(ReadTagProjection.name.ilike(f"%{q}%"))

    tag_rows: list[tuple[str, WorkspaceTagOut]] = []
    for tag in db.scalars(tag_query).all():
        name = (tag.name or "").strip()
        if not name:
            continue
        sync_id = tag.sync_id
        led_ext_id, led_name = ledger_meta.get(tag.ledger_id, ("", ""))
        change_id = change_id_by_ledger.get(tag.ledger_id, 0)
        tag_rows.append(
            (
                sync_id.lower() if sync_id else name.lower(),
                WorkspaceTagOut(
                    id=sync_id,
                    name=name,
                    color=tag.color,
                    last_change_id=change_id,
                    ledger_id=led_ext_id,
                    ledger_name=led_name,
                    created_by_user_id=None,
                    created_by_email=None,
                ),
            )
        )

    # Prefer highest last_change_id per dedup key; then also track name-level
    # dedup so the final list doesn't repeat the same name twice under two
    # different syncIds (mobile fullPush can produce this on legacy data).
    best_by_key: dict[str, WorkspaceTagOut] = {}
    for key, entry in tag_rows:
        existing = best_by_key.get(key)
        if existing is None or (entry.last_change_id or 0) > (existing.last_change_id or 0):
            best_by_key[key] = entry

    name_seen: dict[str, WorkspaceTagOut] = {}
    for entry in best_by_key.values():
        nk = (entry.name or "").lower()
        prev = name_seen.get(nk)
        if prev is None or (entry.last_change_id or 0) > (prev.last_change_id or 0):
            name_seen[nk] = entry

    for entry in name_seen.values():
        all_tags.append(entry)

    # 不再合并 UserTag 表。原因同 accounts/categories：mobile 重命名"出差"→"差旅"
    # 后，snapshot 更新成"差旅"，但 UserTag 里还有"出差"（web 给 tx 指定标签时
    # _get_or_create_user_tag 落下的），名字不同 dedup 折不掉 → 列表同时出现
    # "出差" + "差旅"，看起来 web 没刷新。snapshot 作为唯一权威。

    # Sort by name, then paginate
    # Snapshot 条目填 owner 身份。
    owner_map = _owner_map_for_ledgers(db, list(ledgers))
    for e in all_tags:
        if e.created_by_user_id is None and e.ledger_id in owner_map:
            uid, email = owner_map[e.ledger_id]
            e.created_by_user_id = uid
            e.created_by_email = email

    # 按 tag 聚合全量 tx:用 projection 扫一次(SQL select + index scan),
    # Python 侧按 tag_sync_ids_json / tags_csv 做匹配。projection scan 比
    # 原来 N 次 snapshot parse 快几个量级。
    tag_id_to_stats: dict[str, dict[str, float]] = {
        e.id: {"count": 0.0, "expense": 0.0, "income": 0.0}
        for e in all_tags
        if e.id
    }
    tag_name_to_id: dict[str, str] = {
        (e.name or "").strip().lower(): e.id
        for e in all_tags
        if e.name and e.id
    }
    tx_rows = db.execute(
        select(
            ReadTxProjection.tx_type,
            ReadTxProjection.amount,
            ReadTxProjection.tag_sync_ids_json,
            ReadTxProjection.tags_csv,
        ).where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
    ).all()
    for tx_type_val, amount, tag_ids_json, tags_csv in tx_rows:
        matched_ids: set[str] = set()
        if tag_ids_json:
            try:
                raw_tag_ids = json.loads(tag_ids_json)
                if isinstance(raw_tag_ids, list):
                    for tid in raw_tag_ids:
                        tid_s = str(tid).strip()
                        if tid_s and tid_s in tag_id_to_stats:
                            matched_ids.add(tid_s)
            except json.JSONDecodeError:
                pass
        if not matched_ids and tags_csv:
            for part in str(tags_csv).split(","):
                key = part.strip().lower()
                if key and key in tag_name_to_id:
                    matched_ids.add(tag_name_to_id[key])
        amt = float(amount or 0.0)
        for tid in matched_ids:
            slot = tag_id_to_stats[tid]
            slot["count"] += 1.0
            if tx_type_val == "expense":
                slot["expense"] += amt
            elif tx_type_val == "income":
                slot["income"] += amt
    for e in all_tags:
        if e.id and e.id in tag_id_to_stats:
            s = tag_id_to_stats[e.id]
            e.tx_count = int(s["count"])
            e.expense_total = float(s["expense"])
            e.income_total = float(s["income"])

    all_tags.sort(key=lambda t: (t.name or "").lower())
    return all_tags[offset : offset + limit]


@router.get("/workspace/ledger-counts", response_model=WorkspaceLedgerCountsOut)
def workspace_ledger_counts(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceLedgerCountsOut:
    """账本级全量记账统计：对齐 mobile `getCountsForLedger` (SQL:
    `COUNT(*) + julianday(now) - julianday(MIN(happened_at))`)。不限时间范围。
    首页 Hero 用来展示"记账笔数 / 记账天数"，与 analytics 的 scope=year 脱钩。"""
    is_admin = _is_admin(current_user)
    ledger_conditions: list[Any] = []
    if ledger_id:
        ledger_conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            ledger_conditions.append(Ledger.user_id == user_id)
    else:
        ledger_conditions.append(Ledger.user_id == current_user.id)

    ledgers = list(db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all())
    ledger_internal_ids = [l.id for l in ledgers]
    if not ledger_internal_ids:
        return WorkspaceLedgerCountsOut(
            tx_count=0, days_since_first_tx=0, distinct_days=0, first_tx_at=None,
        )

    # COUNT + MIN 一次 SQL 完事
    row = db.execute(
        select(
            func.count(),
            func.min(ReadTxProjection.happened_at),
        ).where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
    ).one()
    tx_count = int(row[0] or 0)
    first_at = _to_utc(row[1]) if row[1] else None

    # distinct days:需要扫 happened_at 列一次(投不出 SQL 抽象,直接 Python)
    day_set: set[str] = set()
    if tx_count > 0:
        for (ts,) in db.execute(
            select(ReadTxProjection.happened_at)
            .where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
        ).all():
            if ts:
                day_set.add(_to_utc(ts).strftime("%Y-%m-%d"))

    days_since_first_tx = 0
    if first_at is not None:
        now_utc = datetime.now(timezone.utc)
        first_day = first_at.astimezone(timezone.utc).date()
        today_utc = now_utc.date()
        days_since_first_tx = (today_utc - first_day).days + 1

    return WorkspaceLedgerCountsOut(
        tx_count=tx_count,
        days_since_first_tx=days_since_first_tx,
        distinct_days=len(day_set),
        first_tx_at=first_at,
    )


@router.get("/workspace/analytics", response_model=WorkspaceAnalyticsOut)
def workspace_analytics(
    scope: AnalyticsScope = Query(default="month"),
    metric: AnalyticsMetric = Query(default="expense"),
    period: str | None = Query(default=None),
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceAnalyticsOut:
    is_admin = _is_admin(current_user)
    start_at, end_at, normalized_period = _analytics_range(scope=scope, period=period)

    ledger_conditions: list[Any] = []
    if ledger_id:
        ledger_conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            ledger_conditions.append(Ledger.user_id == user_id)
    else:
        ledger_conditions.append(Ledger.user_id == current_user.id)

    ledgers = list(db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all())
    ledger_internal_ids = [l.id for l in ledgers]

    transaction_count = 0
    income_total = 0.0
    expense_total = 0.0
    series_map: dict[str, dict[str, float]] = {}
    category_map: dict[str, dict[str, float]] = {}
    distinct_days_set: set[str] = set()
    first_tx_at: datetime | None = None
    last_tx_at: datetime | None = None

    if ledger_internal_ids:
        tx_query = select(
            ReadTxProjection.tx_type,
            ReadTxProjection.amount,
            ReadTxProjection.happened_at,
            ReadTxProjection.category_name,
        ).where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
        if start_at is not None:
            tx_query = tx_query.where(ReadTxProjection.happened_at >= start_at)
        if end_at is not None:
            tx_query = tx_query.where(ReadTxProjection.happened_at < end_at)

        for tx_type_val, amount, happened_at_raw, cat_name in db.execute(tx_query).all():
            if happened_at_raw is None:
                continue
            happened_at = _to_utc(happened_at_raw)
            amt = float(amount or 0.0)
            transaction_count += 1
            distinct_days_set.add(happened_at.strftime("%Y-%m-%d"))
            if first_tx_at is None or happened_at < first_tx_at:
                first_tx_at = happened_at
            if last_tx_at is None or happened_at > last_tx_at:
                last_tx_at = happened_at
            bucket = _bucket_key(scope, happened_at)
            slot = series_map.setdefault(bucket, {"expense": 0.0, "income": 0.0})
            if tx_type_val == "income":
                income_total += amt
                slot["income"] += amt
            elif tx_type_val == "expense":
                expense_total += amt
                slot["expense"] += amt
            else:
                continue
            category = (cat_name or "").strip() or "Uncategorized"
            category_slot = category_map.setdefault(
                category, {"income": 0.0, "expense": 0.0, "count": 0.0})
            category_slot["count"] += 1.0
            if tx_type_val == "income":
                category_slot["income"] += amt
            elif tx_type_val == "expense":
                category_slot["expense"] += amt

    series = [
        WorkspaceAnalyticsSeriesItemOut(
            bucket=bucket,
            expense=slot["expense"],
            income=slot["income"],
            balance=slot["income"] - slot["expense"],
        )
        for bucket, slot in sorted(series_map.items(), key=lambda x: x[0])
    ]

    category_ranks: list[WorkspaceAnalyticsCategoryRankOut] = []
    if metric != "balance":
        metric_key = "income" if metric == "income" else "expense"
        category_ranks = [
            WorkspaceAnalyticsCategoryRankOut(
                category_name=category_name,
                total=float(values[metric_key]),
                tx_count=int(values["count"]),
            )
            for category_name, values in category_map.items()
            if float(values[metric_key]) > 0
        ]
        category_ranks.sort(key=lambda row: (-row.total, row.category_name))

    return WorkspaceAnalyticsOut(
        summary=WorkspaceAnalyticsSummaryOut(
            transaction_count=transaction_count,
            income_total=income_total,
            expense_total=expense_total,
            balance=income_total - expense_total,
            distinct_days=len(distinct_days_set),
            first_tx_at=first_tx_at,
            last_tx_at=last_tx_at,
        ),
        series=series,
        category_ranks=category_ranks,
        range=WorkspaceAnalyticsRangeOut(
            scope=scope,
            metric=metric,
            period=normalized_period,
            start_at=start_at,
            end_at=end_at - timedelta(seconds=1) if end_at is not None else None,
        ),
    )
