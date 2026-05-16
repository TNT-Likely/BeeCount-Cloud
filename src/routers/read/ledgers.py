"""账本维度读端点:/ledgers, /ledgers/{id}, /ledgers/{id}/stats,
及 /ledgers/{id}/{transactions,accounts,categories,budgets,tags} 的列表查询。

都是以账本为主键的 projection 查询,不做跨账本聚合。"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — imports + helpers + router


def _dedupe_by_sync_id(rows):
    """跨 ledger 同 sync_id 取一份。用 dict 顺序保留:第一次见到 sync_id 时
    收下,后续重复跳过 —— 上游 SQL 已经按 `source_change_id DESC` 排序,所以
    第一份就是最新的。"""
    seen: dict[str, object] = {}
    for r in rows:
        if r.sync_id not in seen:
            seen[r.sync_id] = r
    return list(seen.values())


@router.get("/ledgers", response_model=list[ReadLedgerOut])
def list_ledgers(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadLedgerOut]:
    # 共享账本 Phase 1:JOIN ledger_members 一次性拿 (ledger, caller_role),
    # 再为每条 ledger 查 member_count + owner_user_id。
    # admin 不在此路径里跨用户(走专门管理后台)。
    if _is_admin(current_user):
        rows: list[tuple[Ledger, str]] = [
            (l, "owner") for l in db.scalars(
                select(Ledger).order_by(Ledger.created_at.desc())
            ).all()
        ]
    else:
        rows = list(db.execute(
            select(Ledger, LedgerMember.role)
            .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
            .where(LedgerMember.user_id == current_user.id)
            .order_by(Ledger.created_at.desc())
        ).all())

    out: list[ReadLedgerOut] = []
    now = datetime.now(timezone.utc)
    for ledger, role in rows:
        # Hide soft-deleted ledgers.
        if _is_ledger_deleted(db, ledger_id=ledger.id):
            continue
        currency = ledger.currency or "CNY"
        ledger_name = _resolve_ledger_name(db, ledger=ledger)
        tx_count, income_total, expense_total, _ = _projection_totals(db, ledger.id)
        member_count = db.scalar(
            select(func.count(LedgerMember.user_id))
            .where(LedgerMember.ledger_id == ledger.id)
        ) or 1
        owner_uid = db.scalar(
            select(LedgerMember.user_id).where(
                LedgerMember.ledger_id == ledger.id,
                LedgerMember.role == "owner",
            )
        )
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
                role=cast("Any", role),
                is_shared=(member_count > 1),
                member_count=int(member_count),
                owner_user_id=owner_uid,
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
    # account / category / tag 是 user-global —— "per-ledger count" 在这里
    # 没意义,跟 total 同口径:COUNT DISTINCT sync_id WHERE user_id。下面的
    # _count_distinct_sync 之后会复用同一份。
    def _count_distinct_sync_for(model) -> int:
        return int(db.scalar(
            select(func.count(func.distinct(model.sync_id)))
            .where(model.user_id == current_user.id)
        ) or 0)

    account_count = _count_distinct_sync_for(ReadAccountProjection)
    category_count = _count_distinct_sync_for(ReadCategoryProjection)
    tag_count = _count_distinct_sync_for(ReadTagProjection)

    # 附件计数按 attachment_kind 区分:
    #   - attachment_count / attachment_total: tx 附件(挂在 ledger 上)
    #   - category_attachment_total: 分类自定义图标(user-global,无 ledger)
    # 老数据(0006 migration 前)已经在 migration 里按 read_category_projection
    # 的引用反向标记到 category_icon kind,这里直接按 kind 过滤即可。
    attachment_count = db.scalar(
        select(func.count(AttachmentFile.id)).where(
            AttachmentFile.ledger_id == ledger.id,
            AttachmentFile.attachment_kind == "transaction",
        )
    ) or 0

    # 全局口径:跨当前用户所有账本。projection 的 user_id 列已经 denormalized,
    # 一次 SQL COUNT + COUNT DISTINCT 就出全量。比原来循环 parse 每个 snapshot
    # 快 N 倍。
    user_ledger_ids_subq = accessible_ledger_ids_subquery(
        user_id=current_user.id
    ).scalar_subquery()

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
                AttachmentFile.ledger_id.in_(user_ledger_ids_subq),
                AttachmentFile.attachment_kind == "transaction",
            )
        )
        or 0
    )

    # 分类自定义图标是 user-global,按 user_id + kind 算总数(不分账本)。
    category_attachment_total = int(
        db.scalar(
            select(func.count(AttachmentFile.id)).where(
                AttachmentFile.user_id == current_user.id,
                AttachmentFile.attachment_kind == "category_icon",
            )
        )
        or 0
    )

    return {
        "transaction_count": tx_count,
        "transaction_total": tx_total,
        "attachment_count": int(attachment_count),
        "attachment_total": attachment_total,
        "category_attachment_total": category_attachment_total,
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
    # 共享账本字段:跟 list 路径一致填充
    member_count = db.scalar(
        select(func.count(LedgerMember.user_id))
        .where(LedgerMember.ledger_id == ledger.id)
    ) or 1
    owner_uid = db.scalar(
        select(LedgerMember.user_id).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.role == "owner",
        )
    )
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
        role=cast("Any", member if member is not None else "viewer"),
        is_shared=(member_count > 1),
        member_count=int(member_count),
        owner_user_id=owner_uid,
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
    # account / category / tag 是 user-global,读端按 user_id 列出该用户的
    # **唯一一份**(同 sync_id 在不同 ledger 的 projection 中可能有多行残留 —
    # snapshot fullPush 时按 ledger fanout,delete 已修复为跨 ledger 删,
    # 但存量数据可能仍有重复)。这里用 _dedupe_by_sync_id 去重,优先取
    # source_change_id 最大(最新)的一份。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(ReadAccountProjection)
            .where(ReadAccountProjection.user_id == current_user.id)
            .order_by(
                ReadAccountProjection.sync_id.asc(),
                ReadAccountProjection.source_change_id.desc(),
            )
        ).all()
    )
    rows.sort(key=lambda r: (r.name or "").lower())
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
            note=row.note,
            credit_limit=row.credit_limit,
            billing_day=row.billing_day,
            payment_due_day=row.payment_due_day,
            bank_name=row.bank_name,
            card_last_four=row.card_last_four,
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
    # user-global,跨 ledger 同 sync_id 去重(优先最新 source_change_id 那份)
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(ReadCategoryProjection)
            .where(ReadCategoryProjection.user_id == current_user.id)
            .order_by(
                ReadCategoryProjection.sync_id.asc(),
                ReadCategoryProjection.source_change_id.desc(),
            )
        ).all()
    )
    rows.sort(key=lambda r: (
        r.kind or "",
        r.sort_order or 0,
        (r.name or "").lower(),
    ))
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

    # category name 来自 projection,user-global 维度查询(同 sync_id 跨 ledger
    # 重复时取最新一份 —— SQL 按 source_change_id DESC 排,字典写入用第一个胜出)
    cat_rows = db.execute(
        select(
            ReadCategoryProjection.sync_id,
            ReadCategoryProjection.name,
            ReadCategoryProjection.source_change_id,
        )
        .where(ReadCategoryProjection.user_id == current_user.id)
        .order_by(
            ReadCategoryProjection.sync_id.asc(),
            ReadCategoryProjection.source_change_id.desc(),
        )
    ).all()
    cat_name_by_sync: dict[str, str] = {}
    for r in cat_rows:
        if r.sync_id not in cat_name_by_sync:
            cat_name_by_sync[r.sync_id] = (r.name or "").strip()

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
    # user-global,同 sync_id 跨 ledger 去重
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(ReadTagProjection)
            .where(ReadTagProjection.user_id == current_user.id)
            .order_by(
                ReadTagProjection.sync_id.asc(),
                ReadTagProjection.source_change_id.desc(),
            )
        ).all()
    )
    rows.sort(key=lambda r: (r.name or "").lower())
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


