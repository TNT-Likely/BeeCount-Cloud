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
    SyncChange,
    User,
    UserAccount,
    UserCategory,
    UserProfile,
    UserTag,
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
from ..user_dictionary_service import deduplicate_user_dictionaries

router = APIRouter()
settings = get_settings()
_READ_SCOPE_DEP = (
    require_any_scopes(SCOPE_WEB_READ, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_WEB_READ)
)


def _is_admin(current_user: User) -> bool:
    return bool(current_user.is_admin)


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

    The payload_json stored in sync_changes has the shape:
        {"content": "<json-string>", "metadata": {...}}
    We need to parse the inner ``content`` string to get the actual snapshot
    with keys like ``ledgerName``, ``items``, ``accounts``, etc.
    """
    row = db.scalar(
        select(SyncChange.payload_json)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
        )
        .order_by(SyncChange.change_id.desc())
        .limit(1)
    )
    if row is None:
        return None
    if isinstance(row, str):
        row = json.loads(row)
    # payload_json is {"content": "<json>", "metadata": {...}}
    # Extract and parse the inner content string
    if isinstance(row, dict):
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            try:
                return json.loads(content)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                return row  # type: ignore[return-value]
    return row  # type: ignore[return-value]


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


def _snapshot_transactions(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the items array from a snapshot, or empty list."""
    if snapshot is None:
        return []
    return snapshot.get("items") or []


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


def _snapshot_accounts(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return snapshot.get("accounts") or []


def _snapshot_categories(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return snapshot.get("categories") or []


def _snapshot_budgets(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return snapshot.get("budgets") or []


def _snapshot_tags(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return snapshot.get("tags") or []


def _safe_float(val: Any) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _compute_totals(items: list[dict[str, Any]]) -> tuple[int, float, float]:
    """Return (transaction_count, income_total, expense_total) from snapshot items."""
    count = len(items)
    income_total = 0.0
    expense_total = 0.0
    for item in items:
        amount = float(item.get("amount") or 0.0)
        tx_type = item.get("txType") or item.get("tx_type") or item.get("type") or ""
        if tx_type == "income":
            income_total += amount
        elif tx_type == "expense":
            expense_total += amount
    return count, income_total, expense_total


def _tags_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    """Best-effort parse of a datetime from a snapshot item field."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        # Try full ISO 8601 first (handles microseconds + timezone offsets like
        # "2026-04-17T02:49:19.858771+00:00" which strptime cannot match).
        # Accept both "Z" suffix and "+HH:MM" offsets by normalizing.
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00") if value.endswith("Z") else value)
            return _to_utc(dt)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(value, fmt)
                return _to_utc(dt)
            except ValueError:
                continue
    return None


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
        snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
        ledger_name, currency = _snapshot_ledger_info(snapshot, ledger=ledger)
        items = _snapshot_transactions(snapshot)
        tx_count, income_total, expense_total = _compute_totals(items)
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
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    tx_items = _snapshot_transactions(snapshot)
    budget_items = (snapshot or {}).get("budgets") or []
    attachment_count = db.scalar(
        select(func.count(AttachmentFile.id)).where(
            AttachmentFile.ledger_id == ledger.id
        )
    ) or 0
    return {
        "transaction_count": len(tx_items) if isinstance(tx_items, list) else 0,
        "attachment_count": int(attachment_count),
        "budget_count": len(budget_items) if isinstance(budget_items, list) else 0,
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
    ledger_name, currency = _snapshot_ledger_info(snapshot, ledger=ledger)
    items = _snapshot_transactions(snapshot)
    tx_count, income_total, expense_total = _compute_totals(items)
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
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    ledger_name, _ = _snapshot_ledger_info(snapshot, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    items = _snapshot_transactions(snapshot)
    owner_id, owner_email, owner_display, owner_avatar, owner_avatar_ver = (
        _load_owner_identity(db, ledger=ledger)
    )
    # id→name 表：tx 的 account/category/tag 名字优先按 id 反查，id 查不到
    # 时再 fallback 到 item 里存的老字符串。详见 list_workspace_transactions
    # 同段注释。
    acc_map: dict[str, str] = {
        str(a.get("syncId")): (a.get("name") or "").strip()
        for a in _snapshot_accounts(snapshot)
        if a.get("syncId")
    }
    cat_map: dict[str, tuple[str, str]] = {
        str(c.get("syncId")): (
            (c.get("name") or "").strip(),
            (c.get("kind") or "").strip(),
        )
        for c in _snapshot_categories(snapshot)
        if c.get("syncId")
    }
    tag_map: dict[str, tuple[str, str | None]] = {
        str(t.get("syncId")): (
            (t.get("name") or "").strip(),
            t.get("color"),
        )
        for t in _snapshot_tags(snapshot)
        if t.get("syncId")
    }

    # Apply filters in Python
    filtered: list[dict[str, Any]] = []
    for item in items:
        item_tx_type = item.get("txType") or item.get("tx_type") or item.get("type") or ""
        if tx_type and item_tx_type != tx_type:
            continue
        happened_at = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
        if happened_at is None:
            continue
        if start_at and happened_at < _to_utc(start_at):
            continue
        if end_at and happened_at > _to_utc(end_at):
            continue
        if q:
            q_lower = q.lower()
            searchable = " ".join(
                str(item.get(k) or "")
                for k in ("note", "categoryName", "category_name", "accountName", "account_name",
                           "fromAccountName", "from_account_name", "toAccountName", "to_account_name",
                           "tags")
            ).lower()
            if q_lower not in searchable:
                continue
        filtered.append(item)

    # Sort by happened_at descending, then tx_index descending
    def _sort_key(item: dict[str, Any]) -> tuple[datetime, int]:
        dt = _parse_datetime(item.get("happenedAt") or item.get("happened_at")) or datetime.min.replace(tzinfo=timezone.utc)
        idx = int(item.get("txIndex") or item.get("tx_index") or 0)
        return (dt, idx)

    filtered.sort(key=_sort_key, reverse=True)

    # Paginate
    page = filtered[offset : offset + limit]

    results: list[ReadTransactionOut] = []
    for item in page:
        happened_at = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
        if happened_at is None:
            happened_at = datetime.now(timezone.utc)
        raw_attachments = item.get("attachments")
        sync_id = item.get("syncId") or item.get("sync_id") or item.get("id") or ""

        acc_id = item.get("accountId") or item.get("account_id")
        account_name_live = acc_map.get(str(acc_id)) if acc_id else None
        account_name = account_name_live or item.get("accountName") or item.get("account_name")

        from_acc_id = item.get("fromAccountId") or item.get("from_account_id")
        from_account_name_live = acc_map.get(str(from_acc_id)) if from_acc_id else None
        from_account_name = (
            from_account_name_live
            or item.get("fromAccountName")
            or item.get("from_account_name")
        )

        to_acc_id = item.get("toAccountId") or item.get("to_account_id")
        to_account_name_live = acc_map.get(str(to_acc_id)) if to_acc_id else None
        to_account_name = (
            to_account_name_live
            or item.get("toAccountName")
            or item.get("to_account_name")
        )

        cat_id = item.get("categoryId") or item.get("category_id")
        cat_entry = cat_map.get(str(cat_id)) if cat_id else None
        category_name = (
            cat_entry[0] if cat_entry else (item.get("categoryName") or item.get("category_name"))
        )
        category_kind = (
            cat_entry[1] if cat_entry else (item.get("categoryKind") or item.get("category_kind"))
        )

        raw_tag_ids = item.get("tagIds") or item.get("tag_ids") or []
        if isinstance(raw_tag_ids, list) and raw_tag_ids:
            tags_list_live: list[str] = []
            for tid in raw_tag_ids:
                entry = tag_map.get(str(tid))
                if entry and entry[0]:
                    tags_list_live.append(entry[0])
            if tags_list_live:
                tags_str = ",".join(tags_list_live)
            else:
                raw_tags = item.get("tags") or ""
                tags_str = (
                    ", ".join(raw_tags) if isinstance(raw_tags, list) else str(raw_tags)
                )
        else:
            raw_tags = item.get("tags") or ""
            tags_str = (
                ", ".join(raw_tags) if isinstance(raw_tags, list) else str(raw_tags)
            )

        results.append(
            ReadTransactionOut(
                id=sync_id,
                tx_index=int(item.get("txIndex") or item.get("tx_index") or 0),
                tx_type=item.get("txType") or item.get("tx_type") or item.get("type") or "",
                amount=float(item.get("amount") or 0.0),
                happened_at=happened_at,
                note=item.get("note"),
                category_name=category_name,
                category_kind=category_kind,
                account_name=account_name,
                from_account_name=from_account_name,
                to_account_name=to_account_name,
                category_id=cat_id,
                account_id=acc_id,
                from_account_id=from_acc_id,
                to_account_id=to_acc_id,
                tags=tags_str or None,
                tags_list=_tags_list(tags_str),
                tag_ids=raw_tag_ids if isinstance(raw_tag_ids, list) else [],
                attachments=raw_attachments if isinstance(raw_attachments, list) else None,
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
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    ledger_name, _ = _snapshot_ledger_info(snapshot, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    accounts = _snapshot_accounts(snapshot)
    return [
        ReadAccountOut(
            id=acct.get("syncId") or acct.get("id") or "",
            name=acct.get("name") or "",
            account_type=acct.get("type") or "",
            currency=acct.get("currency") or "",
            initial_balance=float(acct.get("initialBalance") or 0.0),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for acct in accounts
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
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    ledger_name, _ = _snapshot_ledger_info(snapshot, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    categories = _snapshot_categories(snapshot)
    return [
        ReadCategoryOut(
            id=cat.get("syncId") or cat.get("id") or "",
            name=cat.get("name") or "",
            kind=cat.get("kind") or "",
            level=int(cat.get("level") or 0),
            sort_order=int(cat.get("sortOrder") or 0),
            icon=cat.get("icon"),
            icon_type=cat.get("iconType"),
            custom_icon_path=cat.get("customIconPath"),
            icon_cloud_file_id=cat.get("iconCloudFileId"),
            icon_cloud_sha256=cat.get("iconCloudSha256"),
            parent_name=cat.get("parentName"),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for cat in categories
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
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    ledger_name, _ = _snapshot_ledger_info(snapshot, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    # 构建 category syncId → name 查找表,避免每条预算都扫一遍 categories 数组
    cat_map: dict[str, str] = {
        str(c.get("syncId")): (c.get("name") or "").strip()
        for c in _snapshot_categories(snapshot)
        if c.get("syncId")
    }

    # 展示前做两步脏数据过滤(这批脏数据来自早期同步链路 bug,已经在新版修了,
    # 老数据还在 snapshot 里。后续清 server / 让 A 重推一次就会彻底没了):
    #   1) 分类预算但 categoryId 为空 —— 孤儿,category 已删 / 创建时没带上
    #   2) (type, categoryId) 维度去重 —— 同一维度只保留一条,挑 syncId
    #      字典序最大的(snapshot items 的顺序等价于 server 先后,最大 sync
    #      相当于"最后一次 push 的"大概率对的)
    raw = _snapshot_budgets(snapshot)
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for b in raw:
        sync_id = str(b.get("syncId") or "").strip()
        if not sync_id:
            continue
        btype = str(b.get("type") or "total")
        category_sync_id = b.get("categoryId")
        if btype == "category" and not category_sync_id:
            continue
        key = (btype, str(category_sync_id or ""))
        current = dedup.get(key)
        if current is None or str(current.get("syncId") or "") < sync_id:
            dedup[key] = b

    results: list[ReadBudgetOut] = []
    for b in dedup.values():
        sync_id = str(b.get("syncId") or "")
        category_sync_id = b.get("categoryId")
        results.append(
            ReadBudgetOut(
                id=sync_id,
                type=str(b.get("type") or "total"),
                category_id=category_sync_id if category_sync_id else None,
                category_name=cat_map.get(str(category_sync_id)) if category_sync_id else None,
                amount=float(b.get("amount") or 0),
                period=str(b.get("period") or "monthly"),
                start_day=int(b.get("startDay") or 1),
                enabled=bool(b.get("enabled", True)),
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
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    ledger_name, _ = _snapshot_ledger_info(snapshot, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    tags = _snapshot_tags(snapshot)
    return [
        ReadTagOut(
            id=tag.get("syncId") or tag.get("id") or "",
            name=tag.get("name") or "",
            color=tag.get("color"),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for tag in tags
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
    snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
    items = _snapshot_transactions(snapshot)
    tx_count, income_total, expense_total = _compute_totals(items)

    latest_happened_at: datetime | None = None
    for item in items:
        dt = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
        if dt is not None:
            if latest_happened_at is None or dt > latest_happened_at:
                latest_happened_at = dt

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

    # 每个账本一张 "syncId → 当前 name" 表，给下面 out_items 组装时按 id 反查用。
    # 为什么要按 id 反查：snapshot.items[i] 里存的 accountName / categoryName /
    # tags (comma-names) 是 tx 写入那一刻的名字，标签/分类/账户之后改名的话，
    # 这些字段就"过期"了。名字改写靠 update_* mutator 的 cascade 或 materialize
    # 的 cascade 来兜，但任何路径漏了一次 cascade 就会永久陈旧。read 时按 id
    # 反查最新名字能彻底绕开 cascade 失败 —— id 没变，snapshot 里实体最新名字
    # 就是最新名字。id 查不到时再 fallback 到 item 里存的 name 兼容老数据。
    per_ledger_maps: dict[
        str,
        tuple[
            dict[str, str],  # account syncId → name
            dict[str, tuple[str, str]],  # category syncId → (name, kind)
            dict[str, tuple[str, str | None]],  # tag syncId → (name, color)
        ],
    ] = {}
    all_items: list[tuple[dict[str, Any], str, str, int]] = []
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        led_name, _ = _snapshot_ledger_info(snapshot, ledger=led)
        change_id = _get_latest_change_id(db, ledger_id=led.id)
        acc_map = {
            str(a.get("syncId")): (a.get("name") or "").strip()
            for a in _snapshot_accounts(snapshot)
            if a.get("syncId")
        }
        cat_map = {
            str(c.get("syncId")): (
                (c.get("name") or "").strip(),
                (c.get("kind") or "").strip(),
            )
            for c in _snapshot_categories(snapshot)
            if c.get("syncId")
        }
        tag_map = {
            str(t.get("syncId")): (
                (t.get("name") or "").strip(),
                t.get("color"),
            )
            for t in _snapshot_tags(snapshot)
            if t.get("syncId")
        }
        per_ledger_maps[led.external_id] = (acc_map, cat_map, tag_map)
        for item in _snapshot_transactions(snapshot):
            all_items.append((item, led.external_id, led_name, change_id))
    # Owner map: 单用户单账本模型下每条交易的创建人 = 账本 owner。
    owner_map = _owner_map_for_ledgers(db, list(ledgers))

    filtered: list[tuple[dict[str, Any], str, str, int]] = []
    for item, led_ext_id, led_name, change_id in all_items:
        item_tx_type = item.get("txType") or item.get("tx_type") or item.get("type") or ""
        if tx_type and item_tx_type != tx_type:
            continue
        if account_name:
            account_like = account_name.lower()
            acct_fields = " ".join(
                str(item.get(k) or "")
                for k in ("accountName", "account_name", "fromAccountName",
                           "from_account_name", "toAccountName", "to_account_name")
            ).lower()
            if account_like not in acct_fields:
                continue
        if q:
            q_lower = q.lower()
            searchable = " ".join(
                str(item.get(k) or "")
                for k in ("note", "categoryName", "category_name", "accountName", "account_name",
                           "fromAccountName", "from_account_name", "toAccountName", "to_account_name",
                           "tags")
            ).lower()
            if q_lower not in searchable:
                continue
        filtered.append((item, led_ext_id, led_name, change_id))

    total = len(filtered)

    def _sort_key(entry: tuple[dict[str, Any], str, str, int]) -> tuple[datetime, int]:
        item = entry[0]
        dt = _parse_datetime(item.get("happenedAt") or item.get("happened_at")) or datetime.min.replace(tzinfo=timezone.utc)
        idx = int(item.get("txIndex") or item.get("tx_index") or 0)
        return (dt, idx)

    filtered.sort(key=_sort_key, reverse=True)
    page = filtered[offset : offset + limit]

    out_items: list[WorkspaceTransactionOut] = []
    for item, led_ext_id, led_name, change_id in page:
        happened_at = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
        if happened_at is None:
            happened_at = datetime.now(timezone.utc)
        raw_attachments = item.get("attachments")
        sync_id = item.get("syncId") or item.get("sync_id") or item.get("id") or ""
        owner_info = owner_map.get(led_ext_id) or (None, None)

        acc_map, cat_map, tag_map = per_ledger_maps.get(led_ext_id, ({}, {}, {}))

        # 账户：id 查到就用最新 name，查不到 fallback 到 item 里存的名字。
        acc_id = item.get("accountId") or item.get("account_id")
        account_name_live = acc_map.get(str(acc_id)) if acc_id else None
        account_name = account_name_live or item.get("accountName") or item.get("account_name")

        from_acc_id = item.get("fromAccountId") or item.get("from_account_id")
        from_account_name_live = acc_map.get(str(from_acc_id)) if from_acc_id else None
        from_account_name = (
            from_account_name_live
            or item.get("fromAccountName")
            or item.get("from_account_name")
        )

        to_acc_id = item.get("toAccountId") or item.get("to_account_id")
        to_account_name_live = acc_map.get(str(to_acc_id)) if to_acc_id else None
        to_account_name = (
            to_account_name_live
            or item.get("toAccountName")
            or item.get("to_account_name")
        )

        # 分类：同上
        cat_id = item.get("categoryId") or item.get("category_id")
        cat_entry = cat_map.get(str(cat_id)) if cat_id else None
        category_name = (
            cat_entry[0] if cat_entry else (item.get("categoryName") or item.get("category_name"))
        )
        category_kind = (
            cat_entry[1] if cat_entry else (item.get("categoryKind") or item.get("category_kind"))
        )

        # 标签：优先按 tagIds 顺序解析实时名字；没 ids 就 fallback 到 item.tags
        # comma-string。id 存在但查不到（tag 被删）忽略该项。
        raw_tag_ids = item.get("tagIds") or item.get("tag_ids") or []
        if isinstance(raw_tag_ids, list) and raw_tag_ids:
            tags_list_live: list[str] = []
            for tid in raw_tag_ids:
                entry = tag_map.get(str(tid))
                if entry and entry[0]:
                    tags_list_live.append(entry[0])
            if tags_list_live:
                tags_str = ",".join(tags_list_live)
            else:
                # 所有 id 都查不到：退回到 item.tags 历史字符串。
                raw_tags = item.get("tags") or ""
                tags_str = (
                    ", ".join(raw_tags) if isinstance(raw_tags, list) else str(raw_tags)
                )
        else:
            raw_tags = item.get("tags") or ""
            tags_str = (
                ", ".join(raw_tags) if isinstance(raw_tags, list) else str(raw_tags)
            )

        out_items.append(
            WorkspaceTransactionOut(
                id=sync_id,
                tx_index=int(item.get("txIndex") or item.get("tx_index") or 0),
                tx_type=item.get("txType") or item.get("tx_type") or item.get("type") or "",
                amount=float(item.get("amount") or 0.0),
                happened_at=happened_at,
                note=item.get("note"),
                category_name=category_name,
                category_kind=category_kind,
                account_name=account_name,
                from_account_name=from_account_name,
                to_account_name=to_account_name,
                category_id=cat_id,
                account_id=acc_id,
                from_account_id=from_acc_id,
                to_account_id=to_acc_id,
                tags=tags_str or None,
                tags_list=_tags_list(tags_str),
                tag_ids=raw_tag_ids if isinstance(raw_tag_ids, list) else [],
                attachments=raw_attachments if isinstance(raw_attachments, list) else None,
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

    # 见 list_workspace_tags 的注释：先收集所有 ledger 的账户快照，再按
    # syncId/name 取 last_change_id 最大的那份。否则 web 刚在 L1 改了账户，
    # 另一账本的陈旧副本会被 dedup 保留，用户看到"改了没生效"。
    account_rows: list[tuple[str, WorkspaceAccountOut]] = []
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        led_name, _ = _snapshot_ledger_info(snapshot, ledger=led)
        change_id = _get_latest_change_id(db, ledger_id=led.id)

        # 一次遍历 items，按 accountId 汇总；支持 transfer 的 fromAccountId /
        # toAccountId 两端（一进一出，不计入 income/expense，只影响 balance）。
        items = snapshot.get("items") or []
        per_acct: dict[str, dict[str, float | int]] = {}
        for item in items:
            amt = _safe_float(item.get("amount")) or 0.0
            tx_type = (
                item.get("txType") or item.get("tx_type") or item.get("type") or ""
            )
            acct_id = item.get("accountId") or item.get("account_id")
            from_id = item.get("fromAccountId") or item.get("from_account_id")
            to_id = item.get("toAccountId") or item.get("to_account_id")
            if tx_type == "income" and acct_id:
                bucket = per_acct.setdefault(
                    str(acct_id), {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0}
                )
                bucket["count"] = int(bucket["count"]) + 1
                bucket["income"] = float(bucket["income"]) + amt
                bucket["balance"] = float(bucket["balance"]) + amt
            elif tx_type == "expense" and acct_id:
                bucket = per_acct.setdefault(
                    str(acct_id), {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0}
                )
                bucket["count"] = int(bucket["count"]) + 1
                bucket["expense"] = float(bucket["expense"]) + amt
                bucket["balance"] = float(bucket["balance"]) - amt
            elif tx_type == "transfer":
                if from_id:
                    bucket = per_acct.setdefault(
                        str(from_id),
                        {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0},
                    )
                    bucket["count"] = int(bucket["count"]) + 1
                    bucket["balance"] = float(bucket["balance"]) - amt
                if to_id:
                    bucket = per_acct.setdefault(
                        str(to_id),
                        {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0},
                    )
                    bucket["count"] = int(bucket["count"]) + 1
                    bucket["balance"] = float(bucket["balance"]) + amt

        for acct in _snapshot_accounts(snapshot):
            name = (acct.get("name") or "").strip()
            if not name:
                continue
            if q and q.lower() not in name.lower():
                continue
            sync_id = acct.get("syncId") or acct.get("sync_id") or ""
            init_bal = _safe_float(acct.get("initialBalance")) or 0.0
            stats = per_acct.get(str(sync_id)) if sync_id else None
            income_total = float(stats.get("income", 0.0)) if stats else 0.0
            expense_total = float(stats.get("expense", 0.0)) if stats else 0.0
            tx_count = int(stats.get("count", 0)) if stats else 0
            movement = float(stats.get("balance", 0.0)) if stats else 0.0
            account_rows.append(
                (
                    sync_id.lower() if sync_id else name.lower(),
                    WorkspaceAccountOut(
                        id=sync_id,
                        name=name,
                        account_type=acct.get("type"),
                        currency=acct.get("currency"),
                        initial_balance=init_bal,
                        last_change_id=change_id,
                        ledger_id=led.external_id,
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

    # 见 list_workspace_tags 的注释：同名同 kind 的分类可能在多个 ledger
    # snapshot 里都有，其中一份是最新的（web 刚改过）而其他是旧副本。按
    # last_change_id 取最大者，避免用户看到"改了没生效"。
    cat_rows: list[tuple[str, WorkspaceCategoryOut]] = []
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        led_name, _ = _snapshot_ledger_info(snapshot, ledger=led)
        change_id = _get_latest_change_id(db, ledger_id=led.id)
        for cat in _snapshot_categories(snapshot):
            name = (cat.get("name") or "").strip()
            if not name:
                continue
            if q and q.lower() not in name.lower():
                continue
            kind = cat.get("kind") or cat.get("categoryKind") or "expense"
            sync_id = cat.get("syncId") or cat.get("sync_id") or ""
            key = sync_id.lower() if sync_id else f"{kind}:{name.lower()}"
            cat_rows.append(
                (
                    key,
                    WorkspaceCategoryOut(
                        id=sync_id,
                        name=name,
                        kind=kind,
                        level=int(cat.get("level") or 1),
                        sort_order=int(cat.get("sortOrder") or cat.get("sort_order") or 0),
                        icon=cat.get("icon"),
                        icon_type=cat.get("iconType") or cat.get("icon_type"),
                        custom_icon_path=cat.get("customIconPath") or cat.get("custom_icon_path"),
                        icon_cloud_file_id=cat.get("iconCloudFileId") or cat.get("icon_cloud_file_id"),
                        icon_cloud_sha256=cat.get("iconCloudSha256") or cat.get("icon_cloud_sha256"),
                        parent_name=cat.get("parentName") or cat.get("parent_name"),
                        last_change_id=change_id,
                        ledger_id=led.external_id,
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
    seen_keys: set[str] = set()  # name_lower for dedup

    # Collect all tag entries from every ledger snapshot first, tracking which
    # ledger + which change_id each came from. Then for each (syncId or name)
    # pick the entry with the highest per-tag SyncChange row — i.e. the most
    # recently-updated copy. This prevents "web changed tag color in L1 but
    # the list still shows L2's old copy because L2 came first in the dedup".
    tag_rows: list[tuple[str, WorkspaceTagOut]] = []
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        led_name, _ = _snapshot_ledger_info(snapshot, ledger=led)
        change_id = _get_latest_change_id(db, ledger_id=led.id)
        for tag in _snapshot_tags(snapshot):
            name = (tag.get("name") or "").strip()
            if not name:
                continue
            if q and q.lower() not in name.lower():
                continue
            sync_id = tag.get("syncId") or tag.get("sync_id") or ""
            tag_rows.append(
                (
                    sync_id.lower() if sync_id else name.lower(),
                    WorkspaceTagOut(
                        id=sync_id,
                        name=name,
                        color=tag.get("color"),
                        last_change_id=change_id,
                        ledger_id=led.external_id,
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

    # 按 tag 聚合跨所有账本的全部交易 —— 用 tx.tagIds（当前真实引用）优先匹配，
    # 查不到再回退到 tx.tags（comma-name）按 name 匹配。这样 web 的标签页统计
    # 笔数/支出/收入不再依赖前端抓到的分页 transactions，而是全量汇总。
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
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        for tx in _snapshot_transactions(snapshot):
            tx_type_val = (
                tx.get("txType") or tx.get("tx_type") or tx.get("type") or ""
            )
            amount = float(tx.get("amount") or 0.0)
            # 先按 tagIds 解析
            matched_ids: set[str] = set()
            raw_tag_ids = tx.get("tagIds") or tx.get("tag_ids")
            if isinstance(raw_tag_ids, list):
                for tid in raw_tag_ids:
                    tid_s = str(tid).strip()
                    if tid_s and tid_s in tag_id_to_stats:
                        matched_ids.add(tid_s)
            # 没命中就按 name 解析（老 tx 没有 tagIds，或 id 被删了）
            if not matched_ids:
                raw_tags = tx.get("tags")
                if isinstance(raw_tags, str) and raw_tags.strip():
                    for part in raw_tags.split(","):
                        key = part.strip().lower()
                        if key and key in tag_name_to_id:
                            matched_ids.add(tag_name_to_id[key])
                elif isinstance(raw_tags, list):
                    for part in raw_tags:
                        key = str(part).strip().lower()
                        if key and key in tag_name_to_id:
                            matched_ids.add(tag_name_to_id[key])
            for tid in matched_ids:
                slot = tag_id_to_stats[tid]
                slot["count"] += 1.0
                if tx_type_val == "expense":
                    slot["expense"] += amount
                elif tx_type_val == "income":
                    slot["income"] += amount
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

    ledgers = db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all()

    tx_count = 0
    day_set: set[str] = set()
    first_at: datetime | None = None
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        for item in _snapshot_transactions(snapshot):
            ts = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
            if ts is None:
                continue
            tx_count += 1
            day_set.add(ts.strftime("%Y-%m-%d"))
            if first_at is None or ts < first_at:
                first_at = ts

    # "记账天数" = 从首次记账那天到今天（含当天）。对齐 mobile
    # `julianday(now) - julianday(MIN(happened_at)) + 1` 语义。以 UTC 为基准。
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

    ledgers = db.execute(
        select(Ledger).where(and_(*ledger_conditions) if ledger_conditions else true())
    ).scalars().all()

    all_items: list[dict[str, Any]] = []
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        all_items.extend(_snapshot_transactions(snapshot))

    transaction_count = 0
    income_total = 0.0
    expense_total = 0.0
    series_map: dict[str, dict[str, float]] = {}
    category_map: dict[str, dict[str, float]] = {}
    # 记账天数按 distinct date；首页 hero 用来展示"已持续记账 X 天"。
    distinct_days_set: set[str] = set()
    first_tx_at: datetime | None = None
    last_tx_at: datetime | None = None

    for item in all_items:
        happened_at = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
        if happened_at is None:
            continue
        if start_at is not None and happened_at < start_at:
            continue
        if end_at is not None and happened_at >= end_at:
            continue

        # 对齐 snapshot_mutator：item 里这个字段叫 "type"，不是 "txType"。少写
        # 这个 fallback 会让 analytics 拿不到任何 income/expense → 本月 0 / Top 5 空。
        tx_type_val = (
            item.get("txType") or item.get("tx_type") or item.get("type") or ""
        )
        amount = float(item.get("amount") or 0.0)

        transaction_count += 1
        distinct_days_set.add(happened_at.strftime("%Y-%m-%d"))
        if first_tx_at is None or happened_at < first_tx_at:
            first_tx_at = happened_at
        if last_tx_at is None or happened_at > last_tx_at:
            last_tx_at = happened_at
        bucket = _bucket_key(scope, happened_at)
        slot = series_map.setdefault(bucket, {"expense": 0.0, "income": 0.0})
        if tx_type_val == "income":
            income_total += amount
            slot["income"] += amount
        elif tx_type_val == "expense":
            expense_total += amount
            slot["expense"] += amount
        else:
            continue

        category_name_raw = item.get("categoryName") or item.get("category_name") or ""
        category = category_name_raw.strip() if isinstance(category_name_raw, str) and category_name_raw.strip() else "Uncategorized"
        category_slot = category_map.setdefault(category, {"income": 0.0, "expense": 0.0, "count": 0.0})
        category_slot["count"] += 1.0
        if tx_type_val == "income":
            category_slot["income"] += amount
        elif tx_type_val == "expense":
            category_slot["expense"] += amount

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
