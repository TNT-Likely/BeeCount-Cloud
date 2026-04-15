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
    ACTIVE_MEMBER_STATUS,
    READABLE_ROLES,
    get_accessible_ledger_by_external_id,
)
from ..models import (
    Ledger,
    LedgerMember,
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
) -> tuple[Ledger, LedgerMember | None]:
    if is_admin:
        ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
        if ledger is None:
            raise HTTPException(status_code=404, detail="Ledger not found")
        membership = db.scalar(
            select(LedgerMember).where(
                LedgerMember.ledger_id == ledger.id,
                LedgerMember.user_id == user_id,
                LedgerMember.status == ACTIVE_MEMBER_STATUS,
            )
        )
        return ledger, membership

    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=READABLE_ROLES,
    )
    if row is None:
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


def _snapshot_accounts(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return snapshot.get("accounts") or []


def _snapshot_categories(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    return snapshot.get("categories") or []


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
        rows = db.execute(
            select(Ledger, LedgerMember)
            .outerjoin(
                LedgerMember,
                and_(
                    LedgerMember.ledger_id == Ledger.id,
                    LedgerMember.user_id == current_user.id,
                    LedgerMember.status == ACTIVE_MEMBER_STATUS,
                ),
            )
            .order_by(Ledger.created_at.desc())
        ).all()
    else:
        rows = db.execute(
            select(Ledger, LedgerMember)
            .join(
                LedgerMember,
                and_(
                    LedgerMember.ledger_id == Ledger.id,
                    LedgerMember.user_id == current_user.id,
                    LedgerMember.status == ACTIVE_MEMBER_STATUS,
                ),
            )
            .order_by(Ledger.created_at.desc())
        ).all()

    out: list[ReadLedgerOut] = []
    for ledger, member in rows:
        snapshot = _get_latest_snapshot(db, ledger_id=ledger.id)
        ledger_name, currency = _snapshot_ledger_info(snapshot, ledger=ledger)
        items = _snapshot_transactions(snapshot)
        tx_count, income_total, expense_total = _compute_totals(items)
        now = datetime.now(timezone.utc)
        role = cast("Any", member.role if member is not None else "viewer")
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
        raw_tags = item.get("tags") or ""
        if isinstance(raw_tags, list):
            tags_str = ", ".join(raw_tags)
        else:
            tags_str = str(raw_tags) if raw_tags else ""
        raw_tag_ids = item.get("tagIds") or item.get("tag_ids") or []
        raw_attachments = item.get("attachments")
        sync_id = item.get("syncId") or item.get("sync_id") or item.get("id") or ""
        results.append(
            ReadTransactionOut(
                id=sync_id,
                tx_index=int(item.get("txIndex") or item.get("tx_index") or 0),
                tx_type=item.get("txType") or item.get("tx_type") or item.get("type") or "",
                amount=float(item.get("amount") or 0.0),
                happened_at=happened_at,
                note=item.get("note"),
                category_name=item.get("categoryName") or item.get("category_name"),
                category_kind=item.get("categoryKind") or item.get("category_kind"),
                account_name=item.get("accountName") or item.get("account_name"),
                from_account_name=item.get("fromAccountName") or item.get("from_account_name"),
                to_account_name=item.get("toAccountName") or item.get("to_account_name"),
                category_id=item.get("categoryId") or item.get("category_id"),
                account_id=item.get("accountId") or item.get("account_id"),
                from_account_id=item.get("fromAccountId") or item.get("from_account_id"),
                to_account_id=item.get("toAccountId") or item.get("to_account_id"),
                tags=tags_str or None,
                tags_list=_tags_list(tags_str),
                tag_ids=raw_tag_ids if isinstance(raw_tag_ids, list) else [],
                attachments=raw_attachments if isinstance(raw_attachments, list) else None,
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
                created_by_user_id=None,
                created_by_email=None,
                created_by_display_name=None,
                created_by_avatar_url=None,
                created_by_avatar_version=None,
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

    all_items: list[tuple[dict[str, Any], str, str, int]] = []
    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        led_name, _ = _snapshot_ledger_info(snapshot, ledger=led)
        change_id = _get_latest_change_id(db, ledger_id=led.id)
        for item in _snapshot_transactions(snapshot):
            all_items.append((item, led.external_id, led_name, change_id))

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
        raw_tags = item.get("tags") or ""
        if isinstance(raw_tags, list):
            tags_str = ", ".join(raw_tags)
        else:
            tags_str = str(raw_tags) if raw_tags else ""
        raw_tag_ids = item.get("tagIds") or item.get("tag_ids") or []
        raw_attachments = item.get("attachments")
        sync_id = item.get("syncId") or item.get("sync_id") or item.get("id") or ""
        out_items.append(
            WorkspaceTransactionOut(
                id=sync_id,
                tx_index=int(item.get("txIndex") or item.get("tx_index") or 0),
                tx_type=item.get("txType") or item.get("tx_type") or item.get("type") or "",
                amount=float(item.get("amount") or 0.0),
                happened_at=happened_at,
                note=item.get("note"),
                category_name=item.get("categoryName") or item.get("category_name"),
                category_kind=item.get("categoryKind") or item.get("category_kind"),
                account_name=item.get("accountName") or item.get("account_name"),
                from_account_name=item.get("fromAccountName") or item.get("from_account_name"),
                to_account_name=item.get("toAccountName") or item.get("to_account_name"),
                category_id=item.get("categoryId") or item.get("category_id"),
                account_id=item.get("accountId") or item.get("account_id"),
                from_account_id=item.get("fromAccountId") or item.get("from_account_id"),
                to_account_id=item.get("toAccountId") or item.get("to_account_id"),
                tags=tags_str or None,
                tags_list=_tags_list(tags_str),
                tag_ids=raw_tag_ids if isinstance(raw_tag_ids, list) else [],
                attachments=raw_attachments if isinstance(raw_attachments, list) else None,
                last_change_id=change_id,
                ledger_id=led_ext_id,
                ledger_name=led_name,
                created_by_user_id=None,
                created_by_email=None,
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

    all_accounts: list[WorkspaceAccountOut] = []
    seen_keys: set[str] = set()  # (name_lower) for dedup

    for led in ledgers:
        snapshot = _get_latest_snapshot(db, ledger_id=led.id)
        led_name, _ = _snapshot_ledger_info(snapshot, ledger=led)
        change_id = _get_latest_change_id(db, ledger_id=led.id)
        for acct in _snapshot_accounts(snapshot):
            name = (acct.get("name") or "").strip()
            if not name:
                continue
            if q and q.lower() not in name.lower():
                continue
            key = name.lower()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_accounts.append(WorkspaceAccountOut(
                id=acct.get("syncId") or acct.get("sync_id") or "",
                name=name,
                account_type=acct.get("type"),
                currency=acct.get("currency"),
                initial_balance=_safe_float(acct.get("initialBalance")),
                last_change_id=change_id,
                ledger_id=led.external_id,
                ledger_name=led_name,
                created_by_user_id=None,
                created_by_email=None,
            ))

    # --- 2. 合并 UserAccount 表数据（Web 端直接创建的），保持向后兼容 ---
    dedupe_scope_user_id = user_id if is_admin else current_user.id
    if deduplicate_user_dictionaries(db, user_id=dedupe_scope_user_id):
        db.commit()
    ua_conditions: list[Any] = [UserAccount.deleted_at.is_(None)]
    if q:
        ua_conditions.append(UserAccount.name.ilike(f"%{q}%"))
    if is_admin:
        if user_id:
            ua_conditions.append(UserAccount.user_id == user_id)
    else:
        ua_conditions.append(UserAccount.user_id == current_user.id)

    rows = db.execute(
        select(UserAccount, User.email)
        .join(User, User.id == UserAccount.user_id)
        .where(and_(*ua_conditions))
        .order_by(UserAccount.name.asc())
    ).all()
    for row, email in rows:
        key = (row.name or "").strip().lower()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        all_accounts.append(WorkspaceAccountOut(
            id=row.id,
            name=row.name,
            account_type=row.account_type,
            currency=row.currency,
            initial_balance=row.initial_balance,
            last_change_id=0,
            ledger_id=None,
            ledger_name=None,
            created_by_user_id=row.user_id,
            created_by_email=email,
        ))

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

    all_categories: list[WorkspaceCategoryOut] = []
    seen_keys: set[str] = set()  # (kind + name_lower) for dedup

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
            key = f"{kind}:{name.lower()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_categories.append(WorkspaceCategoryOut(
                id=cat.get("syncId") or cat.get("sync_id") or "",
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
            ))

    # --- 2. 合并 UserCategory 表数据（Web 端直接创建的），保持向后兼容 ---
    dedupe_scope_user_id = user_id if is_admin else current_user.id
    if deduplicate_user_dictionaries(db, user_id=dedupe_scope_user_id):
        db.commit()
    uc_conditions: list[Any] = [UserCategory.deleted_at.is_(None)]
    if q:
        uc_conditions.append(UserCategory.name.ilike(f"%{q}%"))
    if is_admin:
        if user_id:
            uc_conditions.append(UserCategory.user_id == user_id)
    else:
        uc_conditions.append(UserCategory.user_id == current_user.id)

    rows = db.execute(
        select(UserCategory, User.email)
        .join(User, User.id == UserCategory.user_id)
        .where(and_(*uc_conditions))
        .order_by(UserCategory.kind.asc(), UserCategory.sort_order.asc(), UserCategory.name.asc())
    ).all()
    for row, email in rows:
        name = (row.name or "").strip()
        kind = row.kind or "expense"
        key = f"{kind}:{name.lower()}"
        if not name or key in seen_keys:
            continue
        seen_keys.add(key)
        all_categories.append(WorkspaceCategoryOut(
            id=row.id,
            name=row.name,
            kind=kind,
            level=row.level,
            sort_order=row.sort_order,
            icon=row.icon,
            icon_type=row.icon_type,
            custom_icon_path=row.custom_icon_path,
            icon_cloud_file_id=row.icon_cloud_file_id,
            icon_cloud_sha256=row.icon_cloud_sha256,
            parent_name=None,
            last_change_id=0,
            ledger_id=None,
            ledger_name=None,
            created_by_user_id=row.user_id,
            created_by_email=email,
        ))

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
            key = name.lower()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_tags.append(WorkspaceTagOut(
                id=tag.get("syncId") or tag.get("sync_id") or "",
                name=name,
                color=tag.get("color"),
                last_change_id=change_id,
                ledger_id=led.external_id,
                ledger_name=led_name,
                created_by_user_id=None,
                created_by_email=None,
            ))

    # --- 2. 合并 UserTag 表数据（Web 端直接创建的），保持向后兼容 ---
    dedupe_scope_user_id = user_id if is_admin else current_user.id
    if deduplicate_user_dictionaries(db, user_id=dedupe_scope_user_id):
        db.commit()
    ut_conditions: list[Any] = [UserTag.deleted_at.is_(None)]
    if q:
        ut_conditions.append(UserTag.name.ilike(f"%{q}%"))
    if is_admin:
        if user_id:
            ut_conditions.append(UserTag.user_id == user_id)
    else:
        ut_conditions.append(UserTag.user_id == current_user.id)

    rows = db.execute(
        select(UserTag, User.email)
        .join(User, User.id == UserTag.user_id)
        .where(and_(*ut_conditions))
        .order_by(UserTag.name.asc())
    ).all()
    for row, email in rows:
        key = (row.name or "").strip().lower()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        all_tags.append(WorkspaceTagOut(
            id=row.id,
            name=row.name,
            color=row.color,
            last_change_id=0,
            ledger_id=None,
            ledger_name=None,
            created_by_user_id=row.user_id,
            created_by_email=email,
        ))

    # Sort by name, then paginate
    all_tags.sort(key=lambda t: (t.name or "").lower())
    return all_tags[offset : offset + limit]


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

    for item in all_items:
        happened_at = _parse_datetime(item.get("happenedAt") or item.get("happened_at"))
        if happened_at is None:
            continue
        if start_at is not None and happened_at < start_at:
            continue
        if end_at is not None and happened_at >= end_at:
            continue

        tx_type_val = item.get("txType") or item.get("tx_type") or ""
        amount = float(item.get("amount") or 0.0)

        transaction_count += 1
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
