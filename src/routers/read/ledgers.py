"""账本维度读端点:/ledgers, /ledgers/{id}, /ledgers/{id}/stats,
及 /ledgers/{id}/{transactions,accounts,categories,budgets,tags} 的列表查询。

都是以账本为主键的 projection 查询,不做跨账本聚合。"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — imports + helpers + router

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
        currency = ledger.currency or "CNY"
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
    currency = ledger.currency or "CNY"
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


