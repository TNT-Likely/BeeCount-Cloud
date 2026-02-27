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
    User,
    UserProfile,
    UserAccount,
    UserCategory,
    UserTag,
    SyncChange,
    WebAccountProjection,
    WebCategoryProjection,
    WebLedgerProjection,
    WebTagProjection,
    WebTransactionProjection,
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


def _require_ledger_projection(db: Session, *, ledger_id: str) -> WebLedgerProjection:
    row = db.scalar(select(WebLedgerProjection).where(WebLedgerProjection.ledger_id == ledger_id))
    if row is not None:
        return row

    ledger = db.scalar(select(Ledger).where(Ledger.id == ledger_id).limit(1))
    if ledger is None:
        raise HTTPException(status_code=404, detail="Ledger not found")

    where_clause = WebTransactionProjection.ledger_id == ledger_id
    transaction_count = int(
        db.scalar(select(func.count()).where(where_clause)) or 0
    )
    income_total = float(
        db.scalar(
            select(func.coalesce(func.sum(WebTransactionProjection.amount), 0.0)).where(
                where_clause,
                WebTransactionProjection.tx_type == "income",
            )
        )
        or 0.0
    )
    expense_total = float(
        db.scalar(
            select(func.coalesce(func.sum(WebTransactionProjection.amount), 0.0)).where(
                where_clause,
                WebTransactionProjection.tx_type == "expense",
            )
        )
        or 0.0
    )
    source_change_id = int(
        db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
        )
        or 0
    )
    now = datetime.now(timezone.utc)
    row = WebLedgerProjection(
        ledger_id=ledger_id,
        ledger_name=(ledger.name or ledger.external_id).strip() or ledger.external_id,
        currency="CNY",
        transaction_count=transaction_count,
        income_total=income_total,
        expense_total=expense_total,
        balance=income_total - expense_total,
        exported_at=now,
        updated_at=now,
        source_change_id=source_change_id,
    )
    db.add(row)
    db.flush()
    return row


def _apply_non_admin_creator_filter(
    conditions: list[Any],
    *,
    is_admin: bool,
    current_user: User,
    field: Any,
) -> None:
    if not is_admin:
        conditions.append(field == current_user.id)


def _tags_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _avatar_url(*, user_id: str, avatar_version: int | None = None) -> str:
    base = f"{settings.api_prefix}/profile/avatar/{user_id}"
    if avatar_version is None:
        return base
    return f"{base}?v={avatar_version}"


def _active_member_count(db: Session, *, ledger_id: str) -> int:
    count = db.scalar(
        select(func.count()).where(
            LedgerMember.ledger_id == ledger_id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
    )
    return int(count or 0)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def _bucket_key(scope: AnalyticsScope, happened_at: datetime) -> str:
    normalized = _to_utc(happened_at)
    if scope == "month":
        return normalized.strftime("%Y-%m-%d")
    return normalized.strftime("%Y-%m")


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

    ledger_ids = [ledger.id for ledger, _ in rows]
    member_counts: dict[str, int] = {}
    if ledger_ids:
        count_rows = db.execute(
            select(
                LedgerMember.ledger_id,
                func.count().label("member_count"),
            )
            .where(
                LedgerMember.ledger_id.in_(ledger_ids),
                LedgerMember.status == ACTIVE_MEMBER_STATUS,
            )
            .group_by(LedgerMember.ledger_id)
        ).all()
        member_counts = {
            ledger_id: int(member_count or 0)
            for ledger_id, member_count in count_rows
        }

    out: list[ReadLedgerOut] = []
    for ledger, member in rows:
        projection = _require_ledger_projection(db, ledger_id=ledger.id)
        role = cast("Any", member.role if member is not None else "viewer")
        member_count = member_counts.get(ledger.id, 0)
        out.append(
            ReadLedgerOut(
                ledger_id=ledger.external_id,
                ledger_name=projection.ledger_name,
                currency=projection.currency,
                transaction_count=projection.transaction_count,
                income_total=projection.income_total,
                expense_total=projection.expense_total,
                balance=projection.balance,
                exported_at=projection.exported_at,
                updated_at=projection.updated_at,
                role=role,
                is_shared=member_count > 1,
                member_count=member_count,
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
    row = _require_ledger_projection(db, ledger_id=ledger.id)
    member_count = _active_member_count(db, ledger_id=ledger.id)
    return ReadLedgerDetailOut(
        ledger_id=ledger.external_id,
        ledger_name=row.ledger_name,
        currency=row.currency,
        transaction_count=row.transaction_count,
        income_total=row.income_total,
        expense_total=row.expense_total,
        balance=row.balance,
        exported_at=row.exported_at,
        updated_at=row.updated_at,
        source_change_id=row.source_change_id,
        role=cast("Any", member.role if member is not None else "viewer"),
        is_shared=member_count > 1,
        member_count=member_count,
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
    projection = _require_ledger_projection(db, ledger_id=ledger.id)

    conditions: list[Any] = [WebTransactionProjection.ledger_id == ledger.id]
    if tx_type:
        conditions.append(WebTransactionProjection.tx_type == tx_type)
    if start_at:
        conditions.append(WebTransactionProjection.happened_at >= start_at)
    if end_at:
        conditions.append(WebTransactionProjection.happened_at <= end_at)
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                WebTransactionProjection.note.ilike(like),
                WebTransactionProjection.category_name.ilike(like),
                WebTransactionProjection.account_name.ilike(like),
                WebTransactionProjection.from_account_name.ilike(like),
                WebTransactionProjection.to_account_name.ilike(like),
                WebTransactionProjection.tags.ilike(like),
            )
        )

    rows = db.execute(
        select(
            WebTransactionProjection,
            User.email,
            UserProfile.display_name,
            UserProfile.avatar_file_id,
            UserProfile.avatar_version,
        )
        .outerjoin(User, User.id == WebTransactionProjection.created_by_user_id)
        .outerjoin(UserProfile, UserProfile.user_id == WebTransactionProjection.created_by_user_id)
        .where(and_(*conditions))
        .order_by(WebTransactionProjection.happened_at.desc(), WebTransactionProjection.tx_index.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        ReadTransactionOut(
            id=row.sync_id,
            tx_index=row.tx_index,
            tx_type=row.tx_type,
            amount=row.amount,
            happened_at=row.happened_at,
            note=row.note,
            category_name=row.category_name,
            category_kind=row.category_kind,
            account_name=row.account_name,
            from_account_name=row.from_account_name,
            to_account_name=row.to_account_name,
            tags=row.tags,
            tags_list=_tags_list(row.tags),
            tag_ids=row.tag_ids_json or [],
            attachments=row.attachments_json,
            last_change_id=projection.source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=projection.ledger_name,
            account_id=row.account_id,
            from_account_id=row.from_account_id,
            to_account_id=row.to_account_id,
            category_id=row.category_id,
            created_by_user_id=row.created_by_user_id,
            created_by_email=email,
            created_by_display_name=display_name,
            created_by_avatar_url=_avatar_url(
                user_id=row.created_by_user_id,
                avatar_version=avatar_version,
            )
            if row.created_by_user_id and avatar_file_id
            else None,
            created_by_avatar_version=avatar_version,
        )
        for row, email, display_name, avatar_file_id, avatar_version in rows
    ]


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
    projection = _require_ledger_projection(db, ledger_id=ledger.id)
    conditions: list[Any] = [WebAccountProjection.ledger_id == ledger.id]
    rows = db.execute(
        select(WebAccountProjection, User.email)
        .outerjoin(User, User.id == WebAccountProjection.created_by_user_id)
        .where(and_(*conditions))
        .order_by(WebAccountProjection.name.asc())
    ).all()
    return [
        ReadAccountOut(
            id=row.sync_id,
            name=row.name,
            account_type=row.account_type,
            currency=row.currency,
            initial_balance=row.initial_balance,
            last_change_id=projection.source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=projection.ledger_name,
            created_by_user_id=row.created_by_user_id,
            created_by_email=email,
        )
        for row, email in rows
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
    projection = _require_ledger_projection(db, ledger_id=ledger.id)
    conditions: list[Any] = [WebCategoryProjection.ledger_id == ledger.id]
    rows = db.execute(
        select(WebCategoryProjection, User.email)
        .outerjoin(User, User.id == WebCategoryProjection.created_by_user_id)
        .where(and_(*conditions))
        .order_by(WebCategoryProjection.kind.asc(), WebCategoryProjection.sort_order.asc())
    ).all()
    return [
        ReadCategoryOut(
            id=row.sync_id,
            name=row.name,
            kind=row.kind,
            level=row.level,
            sort_order=row.sort_order,
            icon=row.icon,
            icon_type=row.icon_type,
            custom_icon_path=row.custom_icon_path,
            icon_cloud_file_id=row.icon_cloud_file_id,
            icon_cloud_sha256=row.icon_cloud_sha256,
            parent_name=row.parent_name,
            last_change_id=projection.source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=projection.ledger_name,
            created_by_user_id=row.created_by_user_id,
            created_by_email=email,
        )
        for row, email in rows
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
    projection = _require_ledger_projection(db, ledger_id=ledger.id)
    conditions: list[Any] = [WebTagProjection.ledger_id == ledger.id]
    rows = db.execute(
        select(WebTagProjection, User.email)
        .outerjoin(User, User.id == WebTagProjection.created_by_user_id)
        .where(and_(*conditions))
        .order_by(WebTagProjection.name.asc())
    ).all()
    return [
        ReadTagOut(
            id=row.sync_id,
            name=row.name,
            color=row.color,
            last_change_id=projection.source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=projection.ledger_name,
            created_by_user_id=row.created_by_user_id,
            created_by_email=email,
        )
        for row, email in rows
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

    conditions: list[Any] = [WebTransactionProjection.ledger_id == ledger.id]
    where_clause = and_(*conditions)

    transaction_count = int(
        db.scalar(select(func.count()).where(where_clause)) or 0
    )
    income_total = float(
        db.scalar(
            select(func.coalesce(func.sum(WebTransactionProjection.amount), 0.0)).where(
                where_clause,
                WebTransactionProjection.tx_type == "income",
            )
        )
        or 0.0
    )
    expense_total = float(
        db.scalar(
            select(func.coalesce(func.sum(WebTransactionProjection.amount), 0.0)).where(
                where_clause,
                WebTransactionProjection.tx_type == "expense",
            )
        )
        or 0.0
    )
    latest_happened_at = db.scalar(
        select(func.max(WebTransactionProjection.happened_at)).where(where_clause)
    )
    return ReadSummaryOut(
        ledger_id=ledger_id,
        transaction_count=transaction_count,
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
    conditions: list[Any] = []
    if ledger_id:
        conditions.append(Ledger.external_id == ledger_id)
    if tx_type:
        conditions.append(WebTransactionProjection.tx_type == tx_type)
    if account_name:
        account_like = f"%{account_name}%"
        conditions.append(
            or_(
                WebTransactionProjection.account_name.ilike(account_like),
                WebTransactionProjection.from_account_name.ilike(account_like),
                WebTransactionProjection.to_account_name.ilike(account_like),
            )
        )
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                WebTransactionProjection.note.ilike(like),
                WebTransactionProjection.category_name.ilike(like),
                WebTransactionProjection.account_name.ilike(like),
                WebTransactionProjection.from_account_name.ilike(like),
                WebTransactionProjection.to_account_name.ilike(like),
                WebTransactionProjection.tags.ilike(like),
            )
        )

    if is_admin:
        if user_id:
            conditions.append(WebTransactionProjection.created_by_user_id == user_id)
    else:
        conditions.append(WebTransactionProjection.created_by_user_id == current_user.id)

    where_clause = and_(*conditions) if conditions else true()
    total = int(
        db.scalar(
            select(func.count())
            .select_from(WebTransactionProjection)
            .join(Ledger, Ledger.id == WebTransactionProjection.ledger_id)
            .where(where_clause)
        )
        or 0
    )

    rows = db.execute(
        select(
            WebTransactionProjection,
            Ledger.external_id,
            WebLedgerProjection.ledger_name,
            WebLedgerProjection.source_change_id,
            User.email,
            UserProfile.display_name,
            UserProfile.avatar_file_id,
            UserProfile.avatar_version,
        )
        .join(Ledger, Ledger.id == WebTransactionProjection.ledger_id)
        .join(WebLedgerProjection, WebLedgerProjection.ledger_id == WebTransactionProjection.ledger_id)
        .outerjoin(User, User.id == WebTransactionProjection.created_by_user_id)
        .outerjoin(UserProfile, UserProfile.user_id == WebTransactionProjection.created_by_user_id)
        .where(where_clause)
        .order_by(WebTransactionProjection.happened_at.desc(), WebTransactionProjection.tx_index.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return WorkspaceTransactionPageOut(
        items=[
            WorkspaceTransactionOut(
                id=row.sync_id,
                tx_index=row.tx_index,
                tx_type=row.tx_type,
                amount=row.amount,
                happened_at=row.happened_at,
                note=row.note,
                category_name=row.category_name,
                category_kind=row.category_kind,
                account_name=row.account_name,
                from_account_name=row.from_account_name,
                to_account_name=row.to_account_name,
                tags=row.tags,
                tags_list=_tags_list(row.tags),
                tag_ids=row.tag_ids_json or [],
                attachments=row.attachments_json,
                last_change_id=source_change_id,
                ledger_id=ledger_external_id,
                ledger_name=ledger_name,
                account_id=row.account_id,
                from_account_id=row.from_account_id,
                to_account_id=row.to_account_id,
                category_id=row.category_id,
                created_by_user_id=row.created_by_user_id,
                created_by_email=email,
                created_by_display_name=display_name,
                created_by_avatar_url=_avatar_url(
                    user_id=row.created_by_user_id,
                    avatar_version=avatar_version,
                )
                if row.created_by_user_id and avatar_file_id
                else None,
                created_by_avatar_version=avatar_version,
            )
            for (
                row,
                ledger_external_id,
                ledger_name,
                source_change_id,
                email,
                display_name,
                avatar_file_id,
                avatar_version,
            ) in rows
        ],
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
    dedupe_scope_user_id = user_id if is_admin else current_user.id
    if deduplicate_user_dictionaries(db, user_id=dedupe_scope_user_id):
        db.commit()
    conditions: list[Any] = []
    _ = ledger_id
    if q:
        like = f"%{q}%"
        conditions.append(UserAccount.name.ilike(like))
    conditions.append(UserAccount.deleted_at.is_(None))
    if is_admin:
        if user_id:
            conditions.append(UserAccount.user_id == user_id)
    else:
        conditions.append(UserAccount.user_id == current_user.id)

    rows = db.execute(
        select(
            UserAccount,
            User.email,
        )
        .join(User, User.id == UserAccount.user_id)
        .where(and_(*conditions) if conditions else true())
        .order_by(UserAccount.name.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        WorkspaceAccountOut(
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
        )
        for row, email in rows
    ]


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
    dedupe_scope_user_id = user_id if is_admin else current_user.id
    if deduplicate_user_dictionaries(db, user_id=dedupe_scope_user_id):
        db.commit()
    conditions: list[Any] = []
    _ = ledger_id
    if q:
        like = f"%{q}%"
        conditions.append(UserCategory.name.ilike(like))
    conditions.append(UserCategory.deleted_at.is_(None))
    if is_admin:
        if user_id:
            conditions.append(UserCategory.user_id == user_id)
    else:
        conditions.append(UserCategory.user_id == current_user.id)

    rows = db.execute(
        select(
            UserCategory,
            User.email,
        )
        .join(User, User.id == UserCategory.user_id)
        .where(and_(*conditions) if conditions else true())
        .order_by(UserCategory.kind.asc(), UserCategory.sort_order.asc(), UserCategory.name.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        WorkspaceCategoryOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
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
        )
        for row, email in rows
    ]


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
    dedupe_scope_user_id = user_id if is_admin else current_user.id
    if deduplicate_user_dictionaries(db, user_id=dedupe_scope_user_id):
        db.commit()
    conditions: list[Any] = []
    _ = ledger_id
    if q:
        like = f"%{q}%"
        conditions.append(UserTag.name.ilike(like))
    conditions.append(UserTag.deleted_at.is_(None))
    if is_admin:
        if user_id:
            conditions.append(UserTag.user_id == user_id)
    else:
        conditions.append(UserTag.user_id == current_user.id)

    rows = db.execute(
        select(
            UserTag,
            User.email,
        )
        .join(User, User.id == UserTag.user_id)
        .where(and_(*conditions) if conditions else true())
        .order_by(UserTag.name.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        WorkspaceTagOut(
            id=row.id,
            name=row.name,
            color=row.color,
            last_change_id=0,
            ledger_id=None,
            ledger_name=None,
            created_by_user_id=row.user_id,
            created_by_email=email,
        )
        for row, email in rows
    ]


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
    conditions: list[Any] = []
    if ledger_id:
        conditions.append(Ledger.external_id == ledger_id)
    if start_at is not None:
        conditions.append(WebTransactionProjection.happened_at >= start_at)
    if end_at is not None:
        conditions.append(WebTransactionProjection.happened_at < end_at)

    if is_admin:
        if user_id:
            conditions.append(WebTransactionProjection.created_by_user_id == user_id)
    else:
        conditions.append(WebTransactionProjection.created_by_user_id == current_user.id)

    rows = db.execute(
        select(
            WebTransactionProjection.tx_type,
            WebTransactionProjection.amount,
            WebTransactionProjection.happened_at,
            WebTransactionProjection.category_name,
        )
        .join(Ledger, Ledger.id == WebTransactionProjection.ledger_id)
        .where(and_(*conditions) if conditions else true())
        .order_by(WebTransactionProjection.happened_at.asc())
    ).all()

    transaction_count = 0
    income_total = 0.0
    expense_total = 0.0
    series_map: dict[str, dict[str, float]] = {}
    category_map: dict[str, dict[str, float]] = {}

    for tx_type, amount, happened_at, category_name in rows:
        if happened_at is None:
            continue
        transaction_count += 1
        normalized_amount = float(amount or 0.0)
        bucket = _bucket_key(scope, happened_at)
        slot = series_map.setdefault(bucket, {"expense": 0.0, "income": 0.0})
        if tx_type == "income":
            income_total += normalized_amount
            slot["income"] += normalized_amount
        elif tx_type == "expense":
            expense_total += normalized_amount
            slot["expense"] += normalized_amount
        else:
            # transfer does not affect income/expense summary in analytics.
            continue

        category = category_name.strip() if isinstance(category_name, str) and category_name.strip() else "Uncategorized"
        category_slot = category_map.setdefault(category, {"income": 0.0, "expense": 0.0, "count": 0.0})
        category_slot["count"] += 1.0
        if tx_type == "income":
            category_slot["income"] += normalized_amount
        elif tx_type == "expense":
            category_slot["expense"] += normalized_amount

    series = [
        WorkspaceAnalyticsSeriesItemOut(
            bucket=bucket,
            expense=slot["expense"],
            income=slot["income"],
            balance=slot["income"] - slot["expense"],
        )
        for bucket, slot in sorted(series_map.items(), key=lambda item: item[0])
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
