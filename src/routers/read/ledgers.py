"""账本维度读端点:/ledgers, /ledgers/{id}, /ledgers/{id}/stats,
及 /ledgers/{id}/{transactions,accounts,categories,budgets,tags} 的列表查询。

都是以账本为主键的 projection 查询,不做跨账本聚合。"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import false as sa_false

from ...models import CardRewardPayout
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
    # 共享账本 Phase 1:走 LedgerMember 表拿 caller 能访问的全部 ledger(含
    # 自己 owner 的 + 加入的共享账本)。admin 用户直接看所有(管理后台需求)。
    from ...ledger_access import list_accessible_memberships, count_ledger_members

    if _is_admin(current_user):
        rows = list(db.scalars(select(Ledger).order_by(Ledger.created_at.desc())).all())
        memberships: list[tuple[Ledger, str | None]] = [(lg, None) for lg in rows]
    else:
        memberships = list_accessible_memberships(db, user_id=current_user.id)

    out: list[ReadLedgerOut] = []
    for ledger, role in memberships:
        # Hide soft-deleted ledgers.
        if _is_ledger_deleted(db, ledger_id=ledger.id):
            continue
        # currency 暂不做 projection 化 —— 顶层元数据非热点,snapshot_cache 命中
        # 后 ~1ms,偶发 cold miss 50ms 可接受;list_ledgers 本身调用频率低。
        currency = ledger.currency or "CNY"
        ledger_name = _resolve_ledger_name(db, ledger=ledger)
        tx_count, income_total, expense_total, balance_all, _ = _projection_totals(db, ledger.id)
        now = datetime.now(timezone.utc)
        member_count = count_ledger_members(db, ledger_id=ledger.id)
        effective_role = role or ("owner" if ledger.user_id == current_user.id else "viewer")
        out.append(
            ReadLedgerOut(
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
                currency=currency,
                month_start_day=ledger.month_start_day or 1,
                transaction_count=tx_count,
                income_total=income_total,
                expense_total=expense_total,
                balance=balance_all,
                exported_at=now,
                updated_at=now,
                role=cast("Any", effective_role),
                is_shared=member_count > 1,
                member_count=member_count,
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

    # user-global tables 都用 user_id PK,count distinct 就是 count rows。
    account_count = _count_distinct_sync_for(UserAccountProjection)
    category_count = _count_distinct_sync_for(UserCategoryProjection)
    tag_count = _count_distinct_sync_for(UserTagProjection)

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
    # 共享账本:走 LedgerMember 维度,Editor 也算上 Owner 的账本(否则附件
    # 总数等指标在 Editor 视角里少算)。
    user_ledger_ids_subq = (
        select(LedgerMember.ledger_id)
        .where(LedgerMember.user_id == current_user.id)
        .scalar_subquery()
    )

    def _count_distinct_sync(model) -> int:
        return int(db.scalar(
            select(func.count(func.distinct(model.sync_id)))
            .where(model.user_id == current_user.id)
        ) or 0)

    # tx / budget 是 ledger-scoped projection。Editor 视角下,Owner 创建的
    # tx 在 ReadTxProjection.user_id 是 Owner,不是 Editor — 用 user_id
    # 过滤会把共享账本的 tx 全漏掉。改走 LedgerMember 维度,跟 attachment_total
    # 已有的口径一致 + 对齐 mobile 本地 db.transactions 全表统计(那边包含
    # 同步下来的共享账本 tx)。
    def _count_ledger_scoped(model) -> int:
        return int(db.scalar(
            select(func.count()).select_from(model)
            .where(model.ledger_id.in_(user_ledger_ids_subq))
        ) or 0)

    tx_total = _count_ledger_scoped(ReadTxProjection)
    budget_total = _count_ledger_scoped(ReadBudgetProjection)
    account_total = _count_distinct_sync(UserAccountProjection)
    category_total = _count_distinct_sync(UserCategoryProjection)
    tag_total = _count_distinct_sync(UserTagProjection)

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
    ledger, role = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=_is_admin(current_user),
    )
    currency = ledger.currency or "CNY"
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    tx_count, income_total, expense_total, balance_all, _ = _projection_totals(db, ledger.id)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    now = datetime.now(timezone.utc)
    # 共享账本 Phase 1:member_count 从 ledger_members 表实时数。is_shared = count > 1。
    from ...ledger_access import count_ledger_members
    member_count = count_ledger_members(db, ledger_id=ledger.id)
    return ReadLedgerDetailOut(
        ledger_id=ledger.external_id,
        ledger_name=ledger_name,
        currency=currency,
        month_start_day=ledger.month_start_day or 1,
        transaction_count=tx_count,
        income_total=income_total,
        expense_total=expense_total,
        balance=balance_all,
        exported_at=now,
        updated_at=now,
        source_change_id=source_change_id,
        role=cast("Any", role or "viewer"),
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
    project_id: str | None = Query(default=None),
    category_id: str | None = Query(default=None),
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
    if project_id:
        query = query.where(ReadTxProjection.project_sync_id == project_id)
    if category_id:
        query = query.where(ReadTxProjection.category_sync_id == category_id)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(
            ReadTxProjection.note.ilike(pattern),
            ReadTxProjection.merchant.ilike(pattern),
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

    # 退款反查(§2.12.3):批次查这一页交易里,谁被退过款(refund_of_sync_id
    # 指向当页 sync_id 集合),group by 原交易 id 组成 refunds 列表。只看当页
    # 已知限制:refund_of 指向不在当页的旧交易时,不会显示出来(交易列表本身
    # 就是分页的,这是既有限制的延伸,不是本次改动引入的新问题)。
    page_sync_ids = [row.sync_id for row in rows]
    refunds_by_target: dict[str, list[ReadTxRefundSummaryOut]] = {}
    if page_sync_ids:
        refund_rows = db.execute(
            select(
                ReadTxProjection.refund_of_sync_id,
                ReadTxProjection.sync_id,
                ReadTxProjection.amount,
                ReadTxProjection.happened_at,
            ).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.refund_of_sync_id.in_(page_sync_ids),
            )
        ).all()
        for target_id, refund_sync_id, refund_amount, refund_happened_at in refund_rows:
            refunds_by_target.setdefault(target_id, []).append(
                ReadTxRefundSummaryOut(
                    id=refund_sync_id,
                    amount=refund_amount,
                    happened_at=_to_utc(refund_happened_at),
                )
            )

    # 借還款追蹤(§2.5 體驗補強):批次查這一頁交易關聯到的欠款,建
    # sync_id -> (counterparty_name, direction) 字典,讓前端不用額外查表
    # 就能直接顯示欠款資訊(跟上面 refund 的批次 join 同一套模式)。
    debt_sync_ids = {row.debt_sync_id for row in rows if row.debt_sync_id}
    debt_info_by_id: dict[str, tuple[str | None, str | None]] = {}
    if debt_sync_ids:
        debt_info_rows = db.execute(
            select(
                ReadDebtProjection.sync_id,
                ReadDebtProjection.counterparty_name,
                ReadDebtProjection.direction,
            ).where(
                ReadDebtProjection.ledger_id == ledger.id,
                ReadDebtProjection.sync_id.in_(debt_sync_ids),
            )
        ).all()
        for debt_sid, debt_counterparty_name, debt_direction in debt_info_rows:
            debt_info_by_id[debt_sid] = (debt_counterparty_name, debt_direction)

    # 專案(Phase 13,docs/PH13_PROJECT_SD.md):同款批次反查模式,建
    # sync_id -> name 字典,讓前端不用額外查表就能顯示交易掛的專案名稱。
    project_sync_ids = {row.project_sync_id for row in rows if row.project_sync_id}
    project_name_by_id: dict[str, str] = {}
    if project_sync_ids:
        project_rows = db.execute(
            select(
                ReadProjectProjection.sync_id,
                ReadProjectProjection.name,
            ).where(
                ReadProjectProjection.ledger_id == ledger.id,
                ReadProjectProjection.sync_id.in_(project_sync_ids),
            )
        ).all()
        for project_sid, project_name in project_rows:
            project_name_by_id[project_sid] = project_name

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
        debt_info = debt_info_by_id.get(row.debt_sync_id) if row.debt_sync_id else None
        results.append(
            ReadTransactionOut(
                id=row.sync_id,
                tx_index=row.tx_index,
                tx_type=row.tx_type,
                amount=row.amount,
                happened_at=_to_utc(row.happened_at),
                note=row.note,
                merchant=row.merchant,
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
                exclude_from_stats=bool(row.exclude_from_stats),
                exclude_from_budget=bool(row.exclude_from_budget),
                currency_code=row.currency_code,
                native_amount=row.native_amount,
                to_amount=row.to_amount,
                base_amount=row.base_amount,
                fee_amount=row.fee_amount,
                fee_label=row.fee_label,
                discount_amount=row.discount_amount,
                discount_label=row.discount_label,
                refund_of_id=row.refund_of_sync_id,
                installment_plan_id=row.installment_plan_sync_id,
                recurring_rule_id=row.recurring_rule_sync_id,
                recurring_occurrence_overridden=bool(row.recurring_occurrence_overridden),
                refunds=refunds_by_target.get(row.sync_id, []),
                has_splits=bool(row.has_splits),
                splits=_tx_splits_list(row.splits_json),
                debt_id=row.debt_sync_id,
                debt_counterparty_name=debt_info[0] if debt_info else None,
                debt_direction=cast("Any", debt_info[1]) if debt_info else None,
                project_id=row.project_sync_id,
                project_name=project_name_by_id.get(row.project_sync_id) if row.project_sync_id else None,
                reward_rule_ids=_reward_rule_ids_list(row.reward_rule_sync_ids_json),
                reward_source_tx_id=row.reward_source_tx_sync_id,
                deferred_posting_at=row.deferred_posting_at,
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
    # user-global per-user 表已经唯一,_dedupe_by_sync_id 是 no-op,但保留
     # 调用以兼容历史 helper 签名,不影响行为。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(UserAccountProjection)
            .where(UserAccountProjection.user_id == current_user.id)
            .order_by(UserAccountProjection.sync_id.asc())
        ).all()
    )
    # 帳戶清單拖曳排序(2026-09-05):按 sort_order 排,None(舊資料/舊版 App)
    # fallback 排到最後,同 sort_order/都缺值時用名稱當 tiebreaker。
    rows.sort(
        key=lambda r: (
            r.sort_order if r.sort_order is not None else 10**9,
            (r.name or "").lower(),
        )
    )
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
            parent_account_id=row.parent_account_id,
            hidden=row.hidden,
            auto_pay_enabled=row.auto_pay_enabled,
            auto_pay_from_account_id=row.auto_pay_from_account_id,
            avatar_cloud_file_id=row.avatar_cloud_file_id,
            avatar_cloud_sha256=row.avatar_cloud_sha256,
            swipesmart_card_id=row.swipesmart_card_id,
            include_in_total=row.include_in_total,
            sort_order=row.sort_order,
            investment_settings=_investment_settings_or_none(row.investment_settings_json),
        )
        for row in rows
    ]


@router.get(
    "/ledgers/{ledger_external_id}/card-recommendation",
    response_model=list[ReadCardRecommendationOut],
)
async def get_card_recommendation(
    ledger_external_id: str,
    amount: float = Query(..., gt=0),
    merchant: str = Query(default=""),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCardRecommendationOut]:
    """SwipeSmart 刷卡建議(Phase 14,docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md
    §3.3.3)。帳戶/Personal API Key 都是 user-global,跟 ledger 無關,這裡的
    `ledger_external_id` 只用來確認呼叫者對這個帳本有權限(跟其它讀端點一致
    的存取控制慣例),不影響建議結果本身。

    沒設定 Personal API Key,或 SwipeSmart 逾時/失敗,一律優雅降級回 []
    (§3.3.3 第 6 點的硬性容錯要求)——絕不能因為這個可選功能擋住記帳流程或
    回錯誤碼。
    """
    is_admin = _is_admin(current_user)
    _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    encrypted = profile.swipesmart_api_key_encrypted if profile is not None else None
    if not encrypted:
        return []
    try:
        api_key = secret_crypto.decrypt(encrypted)
    except ValueError:
        return []

    # 已對照 swipesmart_card_id 的信用卡帳戶 → 反查用的 {CardId: (account_id,
    # account_name)} 字典(§3.3.3 第 5 點)。
    mapped_rows = db.scalars(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.account_type == "credit_card",
            UserAccountProjection.swipesmart_card_id.isnot(None),
        )
    ).all()
    account_by_card_id = {
        row.swipesmart_card_id: (row.sync_id, row.name or "")
        for row in mapped_rows
        if row.swipesmart_card_id
    }

    # 刻意偏離 SD §3.3.3 第 3 點的做法:直接透傳 SwipeSmart 自己的
    # GET /api/user/usages(真實 usedCapAmount),不用 credit_card_billing 自己
    # 近似——見 docs/PH14 plan 的「偏離」說明,避免 CapAmount/UsedCapAmount
    # 語意落差。
    user_usages = await swipesmart_client.fetch_user_usages(api_key)
    results = await swipesmart_client.recommend(
        api_key, amount=amount, merchant=merchant, user_usages=user_usages,
    )
    if not results:
        return []

    out: list[ReadCardRecommendationOut] = []
    for r in results:
        card = r.get("card") or {}
        card_id = card.get("cardId")
        mapped = account_by_card_id.get(card_id)
        out.append(
            ReadCardRecommendationOut(
                card_id=card_id or "",
                bank_name=card.get("bankName") or "",
                card_name=card.get("cardName") or "",
                rule_name=r.get("ruleName"),
                estimated_reward=float(r.get("estimatedReward") or 0.0),
                effective_rate=float(r.get("effectiveRate") or 0.0),
                note=r.get("note"),
                alert_messages=list(r.get("alertMessages") or []),
                account_id=mapped[0] if mapped else None,
                account_name=mapped[1] if mapped else None,
            )
        )
    return out


def _date_to_utc_dt(d: date, *, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _require_credit_card_schedule(account: UserAccountProjection) -> tuple[int, int]:
    if account.billing_day is None or account.payment_due_day is None:
        raise HTTPException(
            status_code=400,
            detail="account has no billing_day/payment_due_day configured",
        )
    return account.billing_day, account.payment_due_day


def _require_billing_root(account: UserAccountProjection) -> None:
    """主帳戶(§2.9 Phase 4,2026-08-02 改版 + 2026-08-02 第二輪單卡放寬)。
    合併帳單只能對「群組」或「沒有掛靠任何群組的獨立信用卡」查詢 ——
    見 `credit_card_billing.is_billing_root` 的完整說明。已經掛靠某個群組
    的子卡不能被直接查(要透過它的群組)。"""
    if not credit_card_billing.is_billing_root(account):
        raise HTTPException(
            status_code=400,
            detail=(
                "account is not billable directly; use an account_group, or a "
                "credit_card with no parent_account_id"
            ),
        )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/billing-summary",
    response_model=ReadAccountBillingSummaryOut,
)
def get_account_billing_summary(
    ledger_external_id: str,
    account_id: str,
    cycle_offset: int = Query(default=0, ge=-60, le=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadAccountBillingSummaryOut:
    """信用卡合併帳單(§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md,2026-08-02 改版
    為群組模型,同日第二輪放寬到單卡)。`account_id` 必須通過
    `credit_card_billing.is_billing_root` 檢查:要嘛是 `account_type ==
    "account_group"` 的純管理容器帳戶(`billing_day`/`payment_due_day`/
    `credit_limit` 設在群組自己身上,代表發卡行給這個群組共用的額度/結帳
    週期,實際刷卡消費/收付款的是掛在它底下的子帳戶,群組自己不是成員、
    不計入消費),要嘛是一張**沒有掛靠任何群組**的獨立信用卡(這時它自己
    就是唯一成員,`billing_day` 等設定直接設在它自己身上)。

    `remaining_due` 用「終身跑動餘額」計算(累計至最近一次已結束帳單週期
    為止的所有子帳戶消費,減掉所有子帳戶+群組自己收到的還款轉帳,不分
    週期窗口):這樣任何一期的溢繳都會自動結轉到未來各期,不會在下一期
    結帳日一過就從計算窗口裡消失(原本按「結帳日之後」窗口查 paid_amount
    的寫法有這個 carry-forward 遺失問題,這次順便修掉)。`statement_amount`
    維持「僅本期」窗口,單純做資訊性顯示用。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    _require_billing_root(account)
    billing_day, payment_due_day = _require_credit_card_schedule(account)

    children = credit_card_billing.resolve_billing_children(db, account=account)
    member_ids = [row.sync_id for row in children]

    now = datetime.now(timezone.utc)
    billing = credit_card_billing.compute_group_billing(
        db, ledger_id=ledger.id, group=account, children=children, now=now,
    )

    period = credit_card_billing.compute_cycle_period_billing(
        db, ledger_id=ledger.id, group=account, children=children, now=now, cycle_offset=cycle_offset,
    )
    installment_summary = credit_card_billing.compute_installment_summary(
        db, ledger_id=ledger.id, member_ids=member_ids,
    )

    # §2.9.6 Phase 7(2026-08-07 使用者反饋):每個 member 附上自己的本期新增
    # 花費(period_new_spend,來自上面剛算好的 per_member_new_spend)+ 自己的
    # 終身跑動餘額(remaining_due,來自 per_child_remaining_due)——子卡詳情
    # 頁改顯示這兩個「自己的」數字,不再借用整組合併金額。
    members_out = [
        ReadAccountBillingMemberOut(
            account_id=row.sync_id,
            account_name=row.name or "",
            cycle_spend=round(billing["per_child_cycle_spend"].get(row.sync_id, 0.0), 2),
            period_new_spend=round(period["per_member_new_spend"].get(row.sync_id, 0.0), 2),
            remaining_due=round(billing["per_child_remaining_due"].get(row.sync_id, 0.0), 2),
        )
        for row in children
    ]

    remaining_due = billing["remaining_due"]
    # 2026-08-03 使用者反饋 #2:轉分期不算已繳,額度不該恢復 —— 用不扣分期
    # 沖銷的 credit_used 算可用額度,remaining_due(當期應繳)維持扣沖銷後
    # 的數字不變。
    credit_used = billing["credit_used"]
    available_credit = (account.credit_limit - credit_used) if account.credit_limit is not None else None

    return ReadAccountBillingSummaryOut(
        account_id=account.sync_id,
        account_name=account.name or "",
        billing_day=billing_day,
        payment_due_day=payment_due_day,
        member_account_ids=member_ids,
        members=members_out,
        cycle_start=_date_to_utc_dt(billing["cycle_start"]),
        cycle_end=_date_to_utc_dt(billing["cycle_end"]),
        due_date=_date_to_utc_dt(billing["due_date"]),
        statement_amount=round(billing["statement_amount"], 2),
        paid_amount=round(billing["paid_amount"], 2),
        remaining_due=round(remaining_due, 2),
        open_cycle_start=_date_to_utc_dt(billing["open_cycle_start"]),
        open_cycle_end=_date_to_utc_dt(billing["open_cycle_end"]),
        open_cycle_due_date=_date_to_utc_dt(billing["open_cycle_due_date"]),
        open_cycle_spend=round(billing["open_cycle_spend"], 2),
        credit_limit=account.credit_limit,
        available_credit=round(available_credit, 2) if available_credit is not None else None,
        period_cycle_start=_date_to_utc_dt(period["cycle_start"]),
        period_cycle_end=_date_to_utc_dt(period["cycle_end"]),
        period_due_date=_date_to_utc_dt(period["due_date"]),
        period_new_spend=period["new_spend"],
        period_carryover_due=period["carryover_due"],
        period_total_due=period["total_due"],
        period_paid_in_cycle=period["paid_in_cycle"],
        period_remaining_due=period["remaining_due"],
        period_has_older=period["has_older"],
        period_has_newer=period["has_newer"],
        period_installment_active_count=installment_summary["active_count"],
        period_installment_paid_periods=installment_summary["paid_periods"],
        period_installment_periods=installment_summary["periods"],
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/billing-periods",
    response_model=ReadBillingPeriodListOut,
)
def list_account_billing_periods(
    ledger_external_id: str,
    account_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadBillingPeriodListOut:
    """「選擇區間」清單(2026-09-06,對齊 mobile app `account_detail_page.dart`
    同名功能,web 端原本只有 `get_account_billing_summary` 的 cycle_offset
    前後箭頭,一期一期翻很慢):列出這個帳戶(含合併帳單子卡)所有實際有
    交易資料涵蓋到的帳單週期,供前端一次跳轉。`offset` 跟 `get_account_
    billing_summary` 的 `cycle_offset` 同語意——`+1`(目前還在累積中的那期)
    永遠列出,即使還沒有任何交易;往回只列到最早一筆交易所在的週期為止,
    跟 `compute_cycle_period_billing` 判斷 `has_older` 用的是同一個
    `MIN(happened_at)` 查詢,兩處判斷結果一定一致,不會有清單漏列/多列
    `has_older` 判定為 true 的那一期。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    _require_billing_root(account)
    billing_day, _payment_due_day = _require_credit_card_schedule(account)

    children = credit_card_billing.resolve_billing_children(db, account=account)
    member_ids = credit_card_billing.billing_member_ids(account, children)

    now = datetime.now(timezone.utc)
    base_start, base_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

    earliest = db.scalar(
        select(func.min(ReadTxProjection.happened_at)).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.account_sync_id.in_(member_ids),
        )
    )
    if earliest is not None and earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    earliest_date = earliest.date() if earliest is not None else None

    # `+1`(目前還在累積中的那期)一律列出當作清單最新一列;往回一期一期看,
    # 只要「這一期的起點之後還有更早的資料」(跟 has_older 同一個判斷式)就
    # 繼續往回收一期,直到收到涵蓋 earliest_date 那一期為止。-600 是防禦性
    # 上限(理論上不會有帳戶累積這麼多期資料),避免資料異常時無限迴圈。
    oldest_offset = 1
    if earliest_date is not None:
        while oldest_offset > -600:
            cycle_start, _cycle_end = credit_card.shift_cycle(
                base_start, base_end, billing_day, oldest_offset,
            )
            if cycle_start <= earliest_date:
                break
            oldest_offset -= 1

    periods = []
    for offset in range(1, oldest_offset - 1, -1):
        cycle_start, cycle_end = credit_card.shift_cycle(base_start, base_end, billing_day, offset)
        periods.append(
            ReadBillingPeriodOptionOut(
                offset=offset,
                cycle_start=_date_to_utc_dt(cycle_start),
                cycle_end=_date_to_utc_dt(cycle_end),
            )
        )
    return ReadBillingPeriodListOut(periods=periods)


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/interest-free-suggestion",
    response_model=ReadInterestFreeSuggestionOut,
)
def get_account_interest_free_suggestion(
    ledger_external_id: str,
    account_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadInterestFreeSuggestionOut:
    """信用卡免息期推薦(§2.9 Phase 4,2026-08-02 改版)。純計算,不查交易,
    只依賴群組帳戶自己的 `billing_day`/`payment_due_day`(跟 billing-summary
    同一個群組模型:結帳週期設在 `account_type == "account_group"` 上)。"""
    is_admin = _is_admin(current_user)
    _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    _require_billing_root(account)
    billing_day, payment_due_day = _require_credit_card_schedule(account)

    now = datetime.now(timezone.utc)
    suggestion = credit_card.interest_free_suggestion(now.date(), billing_day, payment_due_day)
    return ReadInterestFreeSuggestionOut(
        account_id=account.sync_id,
        as_of=now,
        billing_day=suggestion["billing_day"],
        payment_due_day=suggestion["payment_due_day"],
        current_cycle_start=_date_to_utc_dt(suggestion["current_cycle_start"]),
        current_cycle_end=_date_to_utc_dt(suggestion["current_cycle_end"]),
        current_cycle_due_date=_date_to_utc_dt(suggestion["current_cycle_due_date"]),
        next_cycle_start=_date_to_utc_dt(suggestion["next_cycle_start"]),
        next_cycle_end=_date_to_utc_dt(suggestion["next_cycle_end"]),
        next_cycle_due_date=_date_to_utc_dt(suggestion["next_cycle_due_date"]),
        recommended_purchase_after=_date_to_utc_dt(suggestion["recommended_purchase_after"]),
        min_interest_free_days=suggestion["min_interest_free_days"],
        max_interest_free_days=suggestion["max_interest_free_days"],
    )


def _get_own_credit_card_account(
    db: Session, *, user_id: str, account_id: str,
) -> UserAccountProjection:
    """信用卡紅利回饋(§2.9.5)規則/計算都綁在一張真實的 `credit_card` 帳戶
    上,不是 account_group(群組純管理容器自己不會被刷卡)。"""
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    if account.account_type != "credit_card":
        raise HTTPException(status_code=400, detail="account is not a credit_card")
    return account


def _card_reward_rule_to_out(
    row: ReadCardRewardRuleProjection, *, last_change_id: int, db: Session,
) -> ReadCardRewardRuleOut:
    category_ids: list[str] | None = None
    if row.category_sync_ids_json:
        try:
            parsed = json.loads(row.category_sync_ids_json)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            category_ids = [str(c) for c in parsed]
    return ReadCardRewardRuleOut(
        id=row.sync_id,
        account_id=row.account_sync_id,
        label=row.label or "",
        category_ids=category_ids,
        rate_type=cast("Any", row.rate_type or "percentage"),
        rate_value=row.rate_value,
        rounding=cast("Any", row.rounding or "round"),
        total_rounding=cast("Any", row.total_rounding or "round"),
        calc_basis=cast("Any", row.calc_basis or "transaction_date"),
        interval=cast("Any", row.interval or "billing_cycle"),
        min_spend_threshold=row.min_spend_threshold,
        min_tx_amount=row.min_tx_amount,
        cap_amount=row.cap_amount,
        cap_shared_key=row.cap_shared_key,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        settlement_type=cast("Any", row.settlement_type or "manual"),
        settlement_days=row.settlement_days,
        settlement_month_offset=row.settlement_month_offset,
        settlement_day_of_month=row.settlement_day_of_month,
        reward_account_id=row.reward_account_id,
        note=row.note,
        enabled=row.enabled,
        locked=card_rewards.rule_has_history(db, user_id=row.user_id, rule_id=row.sync_id),
        last_change_id=last_change_id,
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/card-reward-rules",
    response_model=list[ReadCardRewardRuleOut],
)
def list_card_reward_rules(
    ledger_external_id: str,
    account_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCardRewardRuleOut]:
    """信用卡紅利回饋規則只读列表(§2.9.5 Phase 4.5)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    _get_own_credit_card_account(db, user_id=current_user.id, account_id=account_id)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.account_sync_id == account_id,
        ).order_by(ReadCardRewardRuleProjection.sync_id.asc())
    ).all()
    return [_card_reward_rule_to_out(row, last_change_id=source_change_id, db=db) for row in rows]


@router.get(
    "/ledgers/{ledger_external_id}/card-reward-rules",
    response_model=list[ReadCardRewardRuleOut],
)
def list_all_card_reward_rules(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadCardRewardRuleOut]:
    """信用卡紅利回饋規則跨卡列表(§2.9.5.4):回傳目前使用者名下**所有**
    信用卡的所有回饋規則(不限某一張卡),給共用上限群組跨卡挑選 UI 用
    (見 `CardRewardRuleFormDialog`)。`ledger_external_id` 只是拿來過既有
    的帳本成員校驗(`card_reward_rule` 是 user-global 實體,不挂任何
    ledger,同 `list_card_reward_rules`)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)
    rows = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
        ).order_by(ReadCardRewardRuleProjection.sync_id.asc())
    ).all()
    return [_card_reward_rule_to_out(row, last_change_id=source_change_id, db=db) for row in rows]


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/card-rewards",
    response_model=ReadCardRewardsOut,
)
def get_account_card_rewards(
    ledger_external_id: str,
    account_id: str,
    period_offset: int = Query(default=0, ge=-120, le=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCardRewardsOut:
    """信用卡紅利回饋當期計算(§2.9.5 Phase 4.5)。規則本身不落庫回饋金額,
    這裡即時從交易加總算出,見 `services.card_rewards` docstring。
    `period_offset` 跟 billing-summary 的 `cycle_offset` 同款語意:`0` 是
    目前這一期(`billing_cycle` 規則是「還在累積、尚未結束」的那期,對齊
    使用者「現在刷卡能拿多少回饋」的直覺;`calendar_month` 規則是本月),
    負數往回看歷史期別。下界比 billing-summary 的 `cycle_offset`(`-60`)
    寬,因為前端(`AccountDetailDialog.tsx`)換算成
    `period_offset = cycleOffset - 1` 傳進來(§2.9.5.4 修正兩者過去各自
    獨立實作、對「0」定義差一期的 bug)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = _get_own_credit_card_account(db, user_id=current_user.id, account_id=account_id)

    own_rules = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.account_sync_id == account_id,
        ).order_by(ReadCardRewardRuleProjection.sync_id.asc())
    ).all()
    # 共用上限群組是跨卡的(§2.9.5.4),把同組的其它卡規則也一起丟進去算
    # cap_shared_key 分攤,但只把這張卡自己的規則回給前端。
    all_rules = card_rewards.fetch_cap_group_rules(db, user_id=current_user.id, base_rules=own_rules)

    now = datetime.now(timezone.utc)
    results = card_rewards.compute_account_card_rewards(
        db, ledger_id=ledger.id, account=account, rules=all_rules, now=now, period_offset=period_offset,
    )
    card_rewards.apply_caps(results)

    # Phase 22(2026-08 使用者反饋):`calendar_month` 規則橫跨帳單週期時,
    # `results` 裡同一條規則可能有 1~2 筆(每個自然月各一筆)——先依 rule_id
    # 分組,再依原本 `own_rules` 的順序組裝,`periods` 陣列內維持
    # `compute_account_card_rewards` 回傳的時間升序。
    own_rule_ids = {r.sync_id for r in own_rules}
    periods_by_rule: dict[str, list] = {}
    for r in results:
        if r["rule"].sync_id not in own_rule_ids:
            continue
        periods_by_rule.setdefault(r["rule"].sync_id, []).append(r)

    items = [
        ReadCardRewardRuleUsageOut(
            rule_id=rule.sync_id,
            label=rule.label or "",
            cap_amount=rule.cap_amount,
            cap_shared_key=rule.cap_shared_key,
            periods=[
                ReadCardRewardPeriodUsageOut(
                    period_start=_date_to_utc_dt(r["period_start"]),
                    period_end=_date_to_utc_dt(r["period_end"]),
                    status=cast("Any", r["status"]),
                    qualifying_spend=r["qualifying_spend"],
                    threshold_met=r["threshold_met"],
                    raw_reward=r["raw_reward"],
                    capped_reward=r["capped_reward"],
                    remaining_reward_room=r["remaining_reward_room"],
                    remaining_spend_room=r["remaining_spend_room"],
                )
                for r in entries
            ],
        )
        for rule in own_rules
        if (entries := periods_by_rule.get(rule.sync_id))
    ]
    return ReadCardRewardsOut(
        account_id=account.sync_id,
        as_of=now,
        items=items,
        total_reward=round(sum(p.capped_reward for i in items for p in i.periods), 2),
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/card-reward-rules/{rule_id}/transactions",
    response_model=ReadCardRewardRuleTransactionsOut,
)
def get_card_reward_rule_transactions(
    ledger_external_id: str,
    account_id: str,
    rule_id: str,
    period_offset: int = Query(default=0, ge=-120, le=1),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCardRewardRuleTransactionsOut:
    """單一規則的交易明細彈窗(§2.9.5.3):命中哪些交易 + 剩餘回饋額度。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = _get_own_credit_card_account(db, user_id=current_user.id, account_id=account_id)
    rule = db.scalar(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.sync_id == rule_id,
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="card reward rule not found")
    if rule.account_sync_id != account_id:
        raise HTTPException(status_code=400, detail="rule_id does not belong to this account")

    now = datetime.now(timezone.utc)
    detail = card_rewards.list_rule_qualifying_transactions(
        db, ledger_id=ledger.id, account=account, rule=rule, now=now, period_offset=period_offset,
    )

    # 2026-08 使用者反饋(對帳明細可編輯回饋金額):逐筆結算的 `CardRewardPayout.
    # dedup_key` 就是來源消費本身的 sync_id,反查得到這筆消費實際結算入帳的
    # 回饋交易 id;再拿這批回饋交易目前的實際金額,取代重新按公式算出來的
    # `reward_amount`——避免使用者編輯過金額後,下次打開明細又被算回原值
    # (見 ReadCardRewardQualifyingTxOut docstring)。Phase 22:一次跨所有
    # `periods` 收集 dedup_keys 一起查,不用每個期間各自查一次 DB。
    all_dedup_keys = [
        item["tx"].sync_id for period in detail["periods"] for item in period["items"]
    ]
    payout_tx_by_dedup: dict[str, str] = {}
    if all_dedup_keys:
        payout_rows = db.execute(
            select(CardRewardPayout.dedup_key, CardRewardPayout.payout_tx_sync_id).where(
                CardRewardPayout.user_id == current_user.id,
                CardRewardPayout.rule_sync_id == rule.sync_id,
                CardRewardPayout.dedup_key.in_(all_dedup_keys),
            )
        ).all()
        payout_tx_by_dedup = {
            dedup_key: payout_tx_id for dedup_key, payout_tx_id in payout_rows if payout_tx_id
        }
    payout_amount_by_tx_id: dict[str, float] = {}
    if payout_tx_by_dedup:
        amount_rows = db.execute(
            select(ReadTxProjection.sync_id, ReadTxProjection.amount).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.sync_id.in_(payout_tx_by_dedup.values()),
            )
        ).all()
        payout_amount_by_tx_id = dict(amount_rows)

    periods = []
    for period in detail["periods"]:
        items = []
        for item in period["items"]:
            payout_tx_id = payout_tx_by_dedup.get(item["tx"].sync_id)
            actual_amount = payout_amount_by_tx_id.get(payout_tx_id) if payout_tx_id else None
            items.append(
                ReadCardRewardQualifyingTxOut(
                    tx_id=item["tx"].sync_id,
                    happened_at=item["tx"].happened_at,
                    amount=item["tx"].amount,
                    note=item["tx"].note,
                    category_name=item["tx"].category_name,
                    reward_amount=actual_amount if actual_amount is not None else item["reward_amount"],
                    settlement_date=(
                        _date_to_utc_dt(settlement_date)
                        if (settlement_date := card_rewards.compute_settlement_date(
                            rule, tx_happened_at=item["tx"].happened_at, period_end=period["period_end"],
                        )) is not None
                        else None
                    ),
                    payout_tx_id=payout_tx_id,
                )
            )
        periods.append(
            ReadCardRewardPeriodTransactionsOut(
                period_start=_date_to_utc_dt(period["period_start"]),
                period_end=_date_to_utc_dt(period["period_end"]),
                status=cast("Any", period["status"]),
                qualifying_spend=period["qualifying_spend"],
                raw_reward=period["raw_reward"],
                capped_reward=period["capped_reward"],
                remaining_reward_room=period["remaining_reward_room"],
                remaining_spend_room=period["remaining_spend_room"],
                items=items,
            )
        )

    return ReadCardRewardRuleTransactionsOut(
        rule_id=rule.sync_id,
        label=rule.label or "",
        cap_amount=rule.cap_amount,
        cap_shared_key=rule.cap_shared_key,
        periods=periods,
    )


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
    # user-global per-user 表已经唯一,_dedupe_by_sync_id 是 no-op。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(UserCategoryProjection)
            .where(UserCategoryProjection.user_id == current_user.id)
            .order_by(UserCategoryProjection.sync_id.asc())
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
            color=row.color,
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
            created_by_user_id=None,
            created_by_email=None,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 分類/帳戶智慧推薦(Phase 21,docs/PH17_USER_FEEDBACK_2026-08_SD.md)
# 純唯讀彙總查詢,不寫入任何資料,不影響既有 sync entity 結構,不適用
# CLAUDE.md「新增或修改 Sync Entity 檢查清單」7 步 SOP。
# ---------------------------------------------------------------------------

_SUGGESTION_LOOKBACK_DAYS = 180
_SUGGESTION_HALF_LIFE_DAYS = 30.0
_SUGGESTION_HOUR_BONUS = 1.5
_SUGGESTION_ACCOUNT_BONUS = 1.5
_SUGGESTION_HOUR_TOLERANCE_HOURS = 2


def _suggestion_decay_weight(happened_at: datetime, *, now: datetime) -> float:
    """近期交易權重較高,半衰期 30 天(具體衰減公式留待實作後依實際資料量
    調校,SD 階段不鎖死精確參數)。"""
    age_days = max((now - _to_utc(happened_at)).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / _SUGGESTION_HALF_LIFE_DAYS)


@router.get(
    "/ledgers/{ledger_external_id}/category-suggestions",
    response_model=ReadCategorySuggestionsOut,
)
def get_category_suggestions(
    ledger_external_id: str,
    tx_type: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    hour: int | None = Query(default=None, ge=0, le=23),
    tz_offset_minutes: int = Query(default=0),
    limit: int = Query(default=10, ge=1, le=20),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCategorySuggestionsOut:
    """依「同 tx_type 整體使用頻率＋同時段＋同帳戶」三種訊號加權排序回傳
    category_id 清單(由高到低)。`hour`/`tz_offset_minutes` 比照
    `_bucket_key` 既有慣例:`tz_offset_minutes` 是客户端本地时区偏移
    (`-new Date().getTimezoneOffset()`,CST 传 +480),用来把 `happened_at`
    折成使用者本地時區的小時,再跟 `hour`(使用者本地當下小時)比對。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    since = datetime.now(timezone.utc) - timedelta(days=_SUGGESTION_LOOKBACK_DAYS)
    query = select(
        ReadTxProjection.category_sync_id,
        ReadTxProjection.account_sync_id,
        ReadTxProjection.happened_at,
    ).where(
        ReadTxProjection.ledger_id == ledger.id,
        ReadTxProjection.user_id == current_user.id,
        ReadTxProjection.category_sync_id.is_not(None),
        ReadTxProjection.happened_at >= since,
    )
    if tx_type:
        query = query.where(ReadTxProjection.tx_type == tx_type)
    rows = db.execute(query).all()

    now = datetime.now(timezone.utc)
    scores: dict[str, float] = {}
    for category_sync_id, account_sync_id, happened_at in rows:
        weight = _suggestion_decay_weight(happened_at, now=now)
        if hour is not None:
            local_hour = (_to_utc(happened_at) + timedelta(minutes=tz_offset_minutes)).hour
            diff = abs(local_hour - hour)
            diff = min(diff, 24 - diff)
            if diff <= _SUGGESTION_HOUR_TOLERANCE_HOURS:
                weight += _SUGGESTION_HOUR_BONUS
        if account_id and account_sync_id == account_id:
            weight += _SUGGESTION_ACCOUNT_BONUS
        scores[category_sync_id] = scores.get(category_sync_id, 0.0) + weight

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ReadCategorySuggestionsOut(category_ids=[cid for cid, _ in ranked[:limit]])


@router.get(
    "/ledgers/{ledger_external_id}/account-suggestions",
    response_model=ReadAccountSuggestionsOut,
)
def get_account_suggestions(
    ledger_external_id: str,
    category_id: str = Query(...),
    limit: int = Query(default=10, ge=1, le=20),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadAccountSuggestionsOut:
    """依「該分類最近/最常使用的帳戶」加權排序回傳 account_id 清單(由高到
    低)。只吃 `account_sync_id` 非空的交易(轉帳沒有這個欄位,天然排除)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    since = datetime.now(timezone.utc) - timedelta(days=_SUGGESTION_LOOKBACK_DAYS)
    rows = db.execute(
        select(ReadTxProjection.account_sync_id, ReadTxProjection.happened_at).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.user_id == current_user.id,
            ReadTxProjection.category_sync_id == category_id,
            ReadTxProjection.account_sync_id.is_not(None),
            ReadTxProjection.happened_at >= since,
        )
    ).all()

    now = datetime.now(timezone.utc)
    scores: dict[str, float] = {}
    for account_sync_id, happened_at in rows:
        scores[account_sync_id] = scores.get(account_sync_id, 0.0) + _suggestion_decay_weight(happened_at, now=now)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ReadAccountSuggestionsOut(account_ids=[aid for aid, _ in ranked[:limit]])


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
            UserCategoryProjection.sync_id,
            UserCategoryProjection.name,
            UserCategoryProjection.source_change_id,
        )
        .where(UserCategoryProjection.user_id == current_user.id)
        .order_by(UserCategoryProjection.sync_id.asc())
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


@router.get(
    "/ledgers/{ledger_external_id}/budgets/usage",
    response_model=ReadBudgetUsageOut,
)
def list_budgets_usage(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadBudgetUsageOut:
    """每个 enabled budget 当前周期已用金额(后端 SQL 聚合)。

    跟手机端 `local_budget_repository.getBudgetUsage` 同语义:
    - total 预算: 该 ledger 当周期内全部 expense SUM
    - category 预算: 预算关联分类自身 + 所有 parent_sync_id 指向它的子分类的
      expense SUM(父分类预算自动覆盖子分类支出)

    取代"前端循环 fetch /workspace/transactions + reduce"的旧路径:
    - N 次 HTTP → 1 次
    - 计算下沉到 SQL,不受 limit=1000 截断
    - 子分类展开在 server 完成,前端无需感知 parent_sync_id
    """
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        is_admin=is_admin,
    )

    # 跟 list_budgets 一致:不 filter enabled,以便前端 join 时不丢 budget。
    raw = db.scalars(
        select(ReadBudgetProjection).where(
            ReadBudgetProjection.ledger_id == ledger.id,
        )
    ).all()

    # 跟 list_budgets 同款脏数据去重: (type, category_sync_id) 维度,sync_id
    # 字典序最大胜出。usage 跟 list 必须用同一份 budget 才一致。
    dedup: dict[tuple[str, str], ReadBudgetProjection] = {}
    for b in raw:
        btype = b.budget_type or "total"
        if btype == "category" and not b.category_sync_id:
            continue
        key = (btype, b.category_sync_id or "")
        current = dedup.get(key)
        if current is None or current.sync_id < b.sync_id:
            dedup[key] = b

    now = datetime.now(timezone.utc)

    # 预算周期跟随账本 month_start_day(设计 D5:budget.start_day 弃用,
    # 与 mobile local_budget_repository 同口径)
    period_day = ledger.month_start_day or 1

    start, end = _current_period_range(period_day, now)

    items: list[ReadBudgetUsageItemOut] = []
    for b in dedup.values():
        # 预算金额本身是账本本位币,用量必须同计量单位:
        # 折本位币口径(0018)读 native_amount,NULL 回退 amount。
        base_q = select(func.coalesce(func.sum(
            func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
        ), 0.0)).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.tx_type == "expense",
            ReadTxProjection.happened_at >= start,
            ReadTxProjection.happened_at < end,
            # D2: 预算用量仅看 exclude_from_budget,与 exclude_from_stats 独立。
            # 标记排除预算的交易不计入用量(total + category 共用此 base_q)。
            ReadTxProjection.exclude_from_budget == sa_false(),
        )
        if (b.budget_type or "total") == "category" and b.category_sync_id:
            # parent + 所有 parent_sync_id 指向它的子分类
            child_ids = list(db.scalars(
                select(UserCategoryProjection.sync_id).where(
                    UserCategoryProjection.user_id == ledger.user_id,
                    UserCategoryProjection.parent_sync_id == b.category_sync_id,
                )
            ).all())
            ids = [b.category_sync_id, *child_ids]
            # 拆帳(§2.4):has_splits=True 的父行 category_sync_id 是 NULL,
            # 下面这个条件天然排除拆帳交易(NULL IN (...) 不成立)—— 分到这个
            # 分类的那部分金额要另外从 read_tx_split_projection 查,按整笔的
            # native/amount 折算比例缩放后累加,两边加总才是这个分类的真实用量。
            non_split_used = float(db.scalar(
                base_q.where(ReadTxProjection.category_sync_id.in_(ids))
            ) or 0.0)
            scale_expr = (
                func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
                / func.nullif(ReadTxProjection.amount, 0.0)
            )
            split_used = float(db.scalar(
                select(func.coalesce(func.sum(ReadTxSplitProjection.amount * scale_expr), 0.0))
                .select_from(ReadTxSplitProjection)
                .join(
                    ReadTxProjection,
                    (ReadTxProjection.ledger_id == ReadTxSplitProjection.ledger_id)
                    & (ReadTxProjection.sync_id == ReadTxSplitProjection.tx_sync_id),
                )
                .where(
                    ReadTxSplitProjection.ledger_id == ledger.id,
                    ReadTxSplitProjection.category_sync_id.in_(ids),
                    ReadTxProjection.tx_type == "expense",
                    ReadTxProjection.happened_at >= start,
                    ReadTxProjection.happened_at < end,
                    ReadTxProjection.exclude_from_budget == sa_false(),
                )
            ) or 0.0)
            used = non_split_used + split_used
        else:
            used = float(db.scalar(base_q) or 0.0)
        items.append(ReadBudgetUsageItemOut(budget_id=b.sync_id, used=abs(used)))

    return ReadBudgetUsageOut(items=items)


@router.get(
    "/ledgers/{ledger_external_id}/recurring-rules",
    response_model=list[ReadRecurringRuleOut],
)
def list_recurring_rules(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadRecurringRuleOut]:
    """週期性收支规则只读列表(§2.2)。到期后的实际生成交易走
    services.recurring_materializer,不在这个端点里处理。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    cat_rows = db.execute(
        select(UserCategoryProjection.sync_id, UserCategoryProjection.name)
        .where(UserCategoryProjection.user_id == current_user.id)
    ).all()
    cat_name_by_sync = {r.sync_id: (r.name or "").strip() for r in cat_rows}
    proj_rows = db.execute(
        select(ReadProjectProjection.sync_id, ReadProjectProjection.name)
        .where(ReadProjectProjection.ledger_id == ledger.id)
    ).all()
    proj_name_by_sync = {r.sync_id: (r.name or "").strip() for r in proj_rows}

    rows = db.scalars(
        select(ReadRecurringRuleProjection).where(
            ReadRecurringRuleProjection.ledger_id == ledger.id,
        ).order_by(ReadRecurringRuleProjection.next_run_at.asc())
    ).all()
    out: list[ReadRecurringRuleOut] = []
    for row in rows:
        tag_ids: list[str] = []
        if row.tag_sync_ids_json:
            try:
                parsed = json.loads(row.tag_sync_ids_json)
                if isinstance(parsed, list):
                    tag_ids = [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass
        reward_rule_ids: list[str] = []
        if row.reward_rule_sync_ids_json:
            try:
                parsed_rewards = json.loads(row.reward_rule_sync_ids_json)
                if isinstance(parsed_rewards, list):
                    reward_rule_ids = [str(v) for v in parsed_rewards]
            except json.JSONDecodeError:
                pass
        out.append(
            ReadRecurringRuleOut(
                id=row.sync_id,
                tx_type=row.tx_type,
                amount=float(row.amount or 0),
                note=row.note,
                category_id=row.category_sync_id,
                category_name=cat_name_by_sync.get(row.category_sync_id) if row.category_sync_id else None,
                account_id=row.account_sync_id,
                from_account_id=row.from_account_sync_id,
                to_account_id=row.to_account_sync_id,
                merchant=row.merchant,
                project_id=row.project_sync_id,
                project_name=proj_name_by_sync.get(row.project_sync_id) if row.project_sync_id else None,
                tag_ids=tag_ids,
                frequency=cast("Any", row.frequency or "monthly"),
                interval=int(row.interval or 1),
                next_run_at=row.next_run_at,
                end_at=row.end_at,
                enabled=bool(row.enabled),
                generated_until_at=row.generated_until_at,
                advanced_rule_json=_parse_advanced_rule_json(row.advanced_rule_json),
                base_amount=row.base_amount,
                fee_amount=row.fee_amount,
                fee_label=row.fee_label,
                discount_amount=row.discount_amount,
                discount_label=row.discount_label,
                reward_rule_ids=reward_rule_ids,
                kind=cast("Any", row.kind or "general"),
                market=row.market,
                symbol=row.symbol,
                security_name=row.security_name,
                stock_fee_rate=row.stock_fee_rate,
                stock_fee_min=row.stock_fee_min,
                upcoming_run_at=_upcoming_run_at(row),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


def _upcoming_run_at(row: ReadRecurringRuleProjection) -> datetime | None:
    if row.tx_type != "transfer" or not row.enabled or row.next_run_at is None:
        return None
    from ...services.recurring_materializer import next_pending_occurrence

    nxt = next_pending_occurrence(row, _parse_advanced_rule_json(row.advanced_rule_json))
    if nxt is None:
        return None
    if row.end_at is not None:
        end_at = row.end_at if row.end_at.tzinfo else row.end_at.replace(tzinfo=timezone.utc)
        if nxt > end_at:
            return None
    return nxt


def _parse_advanced_rule_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@router.get(
    "/ledgers/{ledger_external_id}/installment-plans",
    response_model=list[ReadInstallmentPlanOut],
)
def list_installment_plans(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadInstallmentPlanOut]:
    """分期付款计划只读列表(§2.3)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadInstallmentPlanProjection).where(
            ReadInstallmentPlanProjection.ledger_id == ledger.id,
        ).order_by(ReadInstallmentPlanProjection.first_period_at.desc())
    ).all()
    now = datetime.now(timezone.utc)
    # paid_periods/next_period_at 不再信任 projection 里那两个不被排程更新的
    # 历史相容字段(见 ReadInstallmentPlanProjection docstring),改成从
    # read_installment_period_projection 即时算出,跟 snapshot_builder.build
    # 的口径保持一致。
    period_rows = db.execute(
        select(
            ReadInstallmentPeriodProjection.plan_sync_id,
            ReadInstallmentPeriodProjection.due_at,
            ReadInstallmentPeriodProjection.total_amount,
        ).where(ReadInstallmentPeriodProjection.ledger_id == ledger.id)
    ).all()
    periods_by_plan: dict[str, list[tuple[datetime, float]]] = {}
    for plan_sid, due_at, total_amount in period_rows:
        periods_by_plan.setdefault(plan_sid, []).append((_to_utc(due_at), float(total_amount or 0)))

    out: list[ReadInstallmentPlanOut] = []
    for row in rows:
        periods_sorted = sorted(periods_by_plan.get(row.sync_id) or [])
        due_dates = [d for d, _ in periods_sorted]
        paid_periods = sum(1 for d in due_dates if d <= now)
        future_periods = [(d, amt) for d, amt in periods_sorted if d > now]
        if future_periods:
            next_period_at, current_period_amount = future_periods[0]
        elif periods_sorted:
            next_period_at, current_period_amount = periods_sorted[-1]
        else:
            next_period_at, current_period_amount = _to_utc(row.first_period_at), float(row.period_amount or 0)
        out.append(
            ReadInstallmentPlanOut(
                id=row.sync_id,
                total_amount=float(row.total_amount or 0),
                periods=int(row.periods or 1),
                period_amount=current_period_amount,
                first_period_at=row.first_period_at,
                next_period_at=next_period_at,
                paid_periods=paid_periods,
                account_id=row.account_sync_id,
                category_id=row.category_sync_id,
                note=row.note,
                status=cast("Any", row.status or "active"),
                repayment_method=cast("Any", row.repayment_method or "equal_principal"),
                interest_period=cast("Any", row.interest_period or "monthly"),
                interest_rate=float(row.interest_rate or 0.0),
                round_amounts=bool(row.round_amounts),
                remainder_position=cast("Any", row.remainder_position or "last"),
                grace_period_months=int(row.grace_period_months or 0),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


@router.get(
    "/ledgers/{ledger_external_id}/installment-plans/{plan_id}/periods",
    response_model=list[ReadInstallmentPeriodOut],
)
def list_installment_periods(
    ledger_external_id: str,
    plan_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadInstallmentPeriodOut]:
    """分期计划的每期明细(§2.12.1 Phase 1.5)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    rows = db.scalars(
        select(ReadInstallmentPeriodProjection).where(
            ReadInstallmentPeriodProjection.ledger_id == ledger.id,
            ReadInstallmentPeriodProjection.plan_sync_id == plan_id,
        ).order_by(ReadInstallmentPeriodProjection.period_no.asc())
    ).all()
    # 单期退款(§2.6/§2.12.1)反查:哪期收到过退款,同 ledgers.py 里给普通交易
    # 用的 refunds_by_target 是同一个模式,只是 join key 换成 period.tx_sync_id
    # (退款交易的 refund_of_sync_id 指向的是"原本那期的 tx",不是 period 自己
    # 的 sync_id)。一期理论上只会被退一次,多笔情况下取最新一笔。
    period_tx_ids = [row.tx_sync_id for row in rows if row.tx_sync_id]
    refund_by_tx_id: dict[str, tuple[str, float, datetime]] = {}
    if period_tx_ids:
        refund_rows = db.execute(
            select(
                ReadTxProjection.refund_of_sync_id,
                ReadTxProjection.sync_id,
                ReadTxProjection.amount,
                ReadTxProjection.happened_at,
            ).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.refund_of_sync_id.in_(period_tx_ids),
            )
        ).all()
        for target_tx_id, refund_sync_id, refund_amount, refund_happened_at in refund_rows:
            happened_at_utc = _to_utc(refund_happened_at)
            existing = refund_by_tx_id.get(target_tx_id)
            if existing is None or happened_at_utc > existing[2]:
                refund_by_tx_id[target_tx_id] = (refund_sync_id, float(refund_amount), happened_at_utc)

    out: list[ReadInstallmentPeriodOut] = []
    for row in rows:
        refund_info = refund_by_tx_id.get(row.tx_sync_id) if row.tx_sync_id else None
        out.append(
            ReadInstallmentPeriodOut(
                id=row.sync_id,
                plan_id=row.plan_sync_id,
                period_no=row.period_no,
                due_at=row.due_at,
                principal_amount=float(row.principal_amount or 0.0),
                interest_amount=float(row.interest_amount or 0.0),
                total_amount=float(row.total_amount or 0.0),
                status=cast("Any", row.status or "generated"),
                tx_id=row.tx_sync_id,
                refund_tx_id=refund_info[0] if refund_info else None,
                refund_amount=refund_info[1] if refund_info else None,
                refunded_at=refund_info[2] if refund_info else None,
            )
        )
    return out


@router.get(
    "/ledgers/{ledger_external_id}/debts",
    response_model=list[ReadDebtOut],
)
def list_debts(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadDebtOut]:
    """借還款追蹤只读列表(§2.5)。`remaining_amount`/`status` 不落库,这里
    从 `read_tx_projection.debt_sync_id` 反查交易即时汇总算出(见
    `ReadDebtProjection` docstring)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadDebtProjection).where(
            ReadDebtProjection.ledger_id == ledger.id,
        ).order_by(ReadDebtProjection.due_at.asc().nulls_last())
    ).all()
    if not rows:
        return []

    debt_ids = [row.sync_id for row in rows]
    repayment_rows = db.execute(
        select(
            ReadTxProjection.debt_sync_id,
            ReadTxProjection.sync_id,
            ReadTxProjection.amount,
            ReadTxProjection.happened_at,
        ).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.debt_sync_id.in_(debt_ids),
        ).order_by(ReadTxProjection.happened_at.desc())
    ).all()
    repayments_by_debt: dict[str, list[ReadDebtRepaymentOut]] = {}
    repaid_by_debt: dict[str, float] = {}
    for debt_sid, tx_sid, amount, happened_at in repayment_rows:
        repayments_by_debt.setdefault(debt_sid, []).append(
            ReadDebtRepaymentOut(id=tx_sid, amount=float(amount or 0), happened_at=happened_at)
        )
        repaid_by_debt[debt_sid] = repaid_by_debt.get(debt_sid, 0.0) + abs(float(amount or 0))

    # 欠款紀錄本身(起點交易摘要),跟上面的還款記錄平行——只有 mobile 建立
    # 連帶起點交易的欠款才有 origin_tx_sync_id;若該交易已被刪除(或欠款是
    # web 建的,原本就是 None),這裡就查不到,維持 None。
    origin_tx_ids = [row.origin_tx_sync_id for row in rows if row.origin_tx_sync_id]
    origin_tx_by_debt: dict[str, ReadDebtRepaymentOut] = {}
    if origin_tx_ids:
        origin_tx_rows = db.execute(
            select(
                ReadTxProjection.sync_id,
                ReadTxProjection.amount,
                ReadTxProjection.happened_at,
            ).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.sync_id.in_(origin_tx_ids),
            )
        ).all()
        tx_by_sync_id = {
            tx_sid: (amount, happened_at) for tx_sid, amount, happened_at in origin_tx_rows
        }
        for row in rows:
            if not row.origin_tx_sync_id:
                continue
            found = tx_by_sync_id.get(row.origin_tx_sync_id)
            if found is None:
                continue
            amount, happened_at = found
            origin_tx_by_debt[row.sync_id] = ReadDebtRepaymentOut(
                id=row.origin_tx_sync_id, amount=float(amount or 0), happened_at=happened_at,
            )

    out: list[ReadDebtOut] = []
    for row in rows:
        principal = float(row.principal_amount or 0)
        repaid = repaid_by_debt.get(row.sync_id, 0.0)
        remaining = max(principal - repaid, 0.0)
        if row.closed_at is not None:
            debt_status = "closed"
        elif remaining <= 0.01:
            debt_status = "settled"
        elif repaid > 0:
            debt_status = "partial"
        else:
            debt_status = "open"
        out.append(
            ReadDebtOut(
                id=row.sync_id,
                direction=cast("Any", row.direction or "payable"),
                counterparty_name=row.counterparty_name or "",
                principal_amount=principal,
                remaining_amount=remaining,
                status=cast("Any", debt_status),
                due_at=row.due_at,
                note=row.note,
                repayments=repayments_by_debt.get(row.sync_id, []),
                closed_at=row.closed_at,
                category_id=row.category_sync_id,
                origin_tx_id=row.origin_tx_sync_id,
                origin_transaction=origin_tx_by_debt.get(row.sync_id),
                excluded_from_total=bool(row.excluded_from_total),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


def _project_period_range(
    period_type: str, period_start, period_end, now: datetime, offset: int = 0, tz_offset_minutes: int = 0,
    month_start_day: int = 1,
) -> tuple[datetime, datetime] | None:
    """專案(Phase 13)當期/往期起訖窗口:
    - `fixed`:直接用 period_start/period_end(轉成當天 UTC 零點 ~ 隔天零點,
      含頭尾兩天)。缺欄位時視為無有效窗口(彙總回 0),不拋錯——歷史髒資料
      不該讓整個列表 500。忽略 `offset`(只有單一區間)。
    - `monthly`:比照預算週期(`_current_period_range`)跟隨帳本
      `month_start_day` 滾動計算(2026-09-07 修正:專案原本固定用日曆月 1
      號起算,跟帳本自訂記帳週期起始日對不上,使用者手動設定的月結日在專案
      頁面上完全被忽略),`offset` 往回推算第幾期(0=當期,1=上一期...),供
      §4 期間切換使用。
    - `yearly`:仍固定用本地日曆年 1 號起算(帳本沒有自訂的年週期起始設定)。
      `tz_offset_minutes` 跟 `_analytics_range`/`_bucket_key` 同慣例(JS
      `-new Date().getTimezoneOffset()`,CST 傳 +480):先在本地時區切邊界,
      再轉回 UTC 給 SQL 用,避免 UTC 裸切把本地月初/月底附近的交易歸到錯誤
      月份(這是本地時間 07/31 23:00 存成 UTC 08/01 15:00、跟一般分類明細/
      analytics 對不上的根因)。
    """
    if period_type == "fixed":
        if period_start is None or period_end is None:
            return None
        start = datetime(period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc)
        end_day = datetime(period_end.year, period_end.month, period_end.day, tzinfo=timezone.utc)
        end = end_day + timedelta(days=1)
        return start, end
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    local_now = now.astimezone(tz)
    if period_type == "yearly":
        start_local = local_now.replace(
            year=local_now.year - offset, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end_local = start_local.replace(year=start_local.year + 1)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    # monthly(默认兜底):跟 `_current_period_range` 同款演算法,把「年*12+月」
    # 换成一个连续整数再减 offset,避免月份借位要手写進位判斷;錨點日改用
    # 帳本 month_start_day(邊界收斂到 [1, 28],避免 29/30/31 在 2 月翻车)。
    anchor_day = max(1, min(28, month_start_day or 1))
    if local_now.day >= anchor_day:
        base_index = local_now.year * 12 + (local_now.month - 1)
    else:
        base_index = local_now.year * 12 + (local_now.month - 1) - 1
    month_index = base_index - offset
    anchor_year, anchor_month0 = divmod(month_index, 12)
    start_local = local_now.replace(
        year=anchor_year, month=anchor_month0 + 1, day=anchor_day, hour=0, minute=0, second=0, microsecond=0
    )
    if start_local.month == 12:
        end_local = start_local.replace(year=start_local.year + 1, month=1, day=anchor_day)
    else:
        end_local = start_local.replace(month=start_local.month + 1, day=anchor_day)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _project_carried_over(
    db: Session, *, ledger, project_sync_id: str, period_type: str, period_start, period_end,
    now: datetime, effective_offset: int, tz_offset_minutes: int, month_start_day: int,
    budget_amount: float | None, carryover_enabled: bool,
) -> float | None:
    """結轉(carryover_enabled)金額,`list_projects`/`get_project_breakdown` 共用
    —— 比照 App 端 `LocalProjectRepository.getProjectUsage` 的口徑:只算「上一
    期」一次,不做多期遞迴結轉,`fixed` 週期沒有「上一期」概念故不生效。
    carried_over = 上一期的名目 budget_amount - 上一期的實際支出(只算
    tx_type=='expense' 且未標記 exclude_from_budget 的交易,同「預算用量」口徑)。
    """
    if not carryover_enabled or period_type == "fixed" or budget_amount is None or budget_amount <= 0:
        return None
    prev_window = _project_period_range(
        period_type, period_start, period_end, now, effective_offset + 1,
        tz_offset_minutes=tz_offset_minutes, month_start_day=month_start_day,
    )
    if prev_window is None:
        return None
    prev_start, prev_end = prev_window
    prev_spent = float(db.scalar(
        select(func.coalesce(func.sum(
            func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
        ), 0.0)).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.project_sync_id == project_sync_id,
            ReadTxProjection.tx_type == "expense",
            ReadTxProjection.exclude_from_budget == sa_false(),
            ReadTxProjection.happened_at >= prev_start,
            ReadTxProjection.happened_at < prev_end,
        )
    ) or 0.0)
    return budget_amount - abs(prev_spent)


@router.get(
    "/ledgers/{ledger_external_id}/projects",
    response_model=list[ReadProjectOut],
)
def list_projects(
    ledger_external_id: str,
    tz_offset_minutes: int = Query(default=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadProjectOut]:
    """專案只读列表(Phase 13,docs/PH13_PROJECT_SD.md)。`spent`/`remaining`/
    `progress_pct`/`status` 不落库,从 `read_tx_projection.project_sync_id`
    反查交易,依 period_type 算出當期起訖窗口即時彙總算出(见
    `ReadProjectProjection` docstring)。`spent`/收入併入預算/結轉的口徑跟
    `get_project_breakdown` 一致(比照 App 端 `ProjectRepository.getProjectUsage`
    ——`spent` 只算 tx_type=='expense' 且未標記 `exclude_from_budget` 的交易,
    `effective_budget` = budget_amount + 本期收入(income_included_in_budget
    時)+ 結轉(carryover_enabled 時),避免這個列表端點跟詳情頁/App 端算出不
    同的「已超支」判斷)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadProjectProjection).where(
            ReadProjectProjection.ledger_id == ledger.id,
        ).order_by(ReadProjectProjection.sort_order.asc(), ReadProjectProjection.sync_id.asc())
    ).all()
    if not rows:
        return []

    now = datetime.now(timezone.utc)
    month_start_day = ledger.month_start_day or 1
    out: list[ReadProjectOut] = []
    for row in rows:
        period_type = row.period_type or "monthly"
        window = _project_period_range(
            period_type, row.period_start, row.period_end, now,
            tz_offset_minutes=tz_offset_minutes,
            month_start_day=month_start_day,
        )
        spent = income_for_budget = 0.0
        if window is not None:
            start, end = window
            budget_stats_rows = db.execute(
                select(
                    ReadTxProjection.tx_type,
                    func.sum(func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)),
                ).where(
                    ReadTxProjection.ledger_id == ledger.id,
                    ReadTxProjection.project_sync_id == row.sync_id,
                    ReadTxProjection.tx_type.in_(["expense", "income"]),
                    ReadTxProjection.exclude_from_budget == sa_false(),
                    ReadTxProjection.happened_at >= start,
                    ReadTxProjection.happened_at < end,
                ).group_by(ReadTxProjection.tx_type)
            ).all()
            for tx_type, total in budget_stats_rows:
                if tx_type == "expense":
                    spent = abs(float(total or 0.0))
                elif tx_type == "income":
                    income_for_budget = abs(float(total or 0.0))
        budget_amount = float(row.budget_amount) if row.budget_amount is not None else None
        carried_over = _project_carried_over(
            db, ledger=ledger, project_sync_id=row.sync_id,
            period_type=period_type, period_start=row.period_start, period_end=row.period_end,
            now=now, effective_offset=0, tz_offset_minutes=tz_offset_minutes,
            month_start_day=month_start_day,
            budget_amount=budget_amount, carryover_enabled=bool(row.carryover_enabled),
        )
        effective_budget: float | None = None
        remaining: float | None = None
        progress_pct: float | None = None
        project_status: str = "ok"
        if budget_amount is not None and budget_amount > 0:
            effective_budget = (
                budget_amount
                + (income_for_budget if row.income_included_in_budget else 0.0)
                + (carried_over or 0.0)
            )
            remaining = effective_budget - spent
            progress_pct = round(min(spent / effective_budget, 999.0) * 100.0, 2) if effective_budget > 0 else None
            if spent >= effective_budget:
                project_status = "over"
            elif spent >= effective_budget * 0.8:
                project_status = "warning"
        out.append(
            ReadProjectOut(
                id=row.sync_id,
                name=row.name or "",
                icon=row.icon,
                budget_amount=budget_amount,
                effective_budget=effective_budget,
                carried_over=carried_over,
                period_type=cast("Any", row.period_type or "monthly"),
                period_start=row.period_start,
                period_end=row.period_end,
                carryover_enabled=bool(row.carryover_enabled),
                visible_on_home=bool(row.visible_on_home),
                enabled=bool(row.enabled),
                sort_order=int(row.sort_order or 0),
                income_included_in_budget=bool(row.income_included_in_budget),
                daily_budget_enabled=bool(row.daily_budget_enabled),
                daily_budget_mode=cast("Any", row.daily_budget_mode),
                reminder_threshold_percent=row.reminder_threshold_percent,
                spent=spent,
                remaining=remaining,
                progress_pct=progress_pct,
                status=cast("Any", project_status),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


@router.get(
    "/ledgers/{ledger_external_id}/projects/{project_id}/category-budgets",
    response_model=list[ReadProjectCategoryBudgetOut],
)
def list_project_category_budgets(
    ledger_external_id: str,
    project_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadProjectCategoryBudgetOut]:
    """專案分類子預算只读列表(docs/2026-09-06-project-category-budget-period-
    switch-design.md §2.2/§7.2)。只回傳分配設定本身,不含花費統計(見
    `ReadProjectCategoryBudgetOut` docstring)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    rows = db.scalars(
        select(ReadProjectCategoryBudgetProjection).where(
            ReadProjectCategoryBudgetProjection.ledger_id == ledger.id,
            ReadProjectCategoryBudgetProjection.project_sync_id == project_id,
        ).order_by(
            ReadProjectCategoryBudgetProjection.sort_order.asc(),
            ReadProjectCategoryBudgetProjection.sync_id.asc(),
        )
    ).all()
    return [
        ReadProjectCategoryBudgetOut(
            id=row.sync_id,
            project_id=row.project_sync_id,
            category_id=row.category_sync_id,
            mode=cast("Any", row.mode or "fixed"),
            fixed_amount=float(row.fixed_amount) if row.fixed_amount is not None else None,
            percentage=float(row.percentage) if row.percentage is not None else None,
            carryover_enabled=bool(row.carryover_enabled),
            sort_order=int(row.sort_order or 0),
            last_change_id=source_change_id,
            ledger_id=ledger.external_id,
            ledger_name=ledger_name,
        )
        for row in rows
    ]


def _resolve_project_category_budget_target(
    row: ReadProjectCategoryBudgetProjection, budget_amount: float | None,
) -> float | None:
    """把一筆分類子預算解析成金額。`percentage` 模式用專案原始
    `budget_amount`(不是收入併入後的 `effective_budget`),跟既有前端
    `ProjectCategoryBudgetsSection` 的 `allocatedTotal` 計算口徑保持一致。"""
    if (row.mode or "fixed") == "percentage":
        if budget_amount is None or row.percentage is None:
            return None
        return budget_amount * float(row.percentage) / 100.0
    return float(row.fixed_amount) if row.fixed_amount is not None else None


@router.get(
    "/ledgers/{ledger_external_id}/projects/{project_id}/breakdown",
    response_model=ReadProjectBreakdownOut,
)
def get_project_breakdown(
    ledger_external_id: str,
    project_id: str,
    period_offset: int = Query(default=0, ge=0, le=1200),
    tz_offset_minutes: int = Query(default=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadProjectBreakdownOut:
    """專案詳情頁:期間切換 + 統計條 + 分類拆解(docs/2026-09-06-project-
    category-budget-period-switch-design.md §4)。`period_offset` 語意同
    `_project_period_range`:0=當期,正整數=往回第幾期;`fixed` 週期只有單一
    區間,忽略請求的 offset,一律視為 0。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    project = db.scalar(
        select(ReadProjectProjection).where(
            ReadProjectProjection.ledger_id == ledger.id,
            ReadProjectProjection.sync_id == project_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    period_type = project.period_type or "monthly"
    effective_offset = 0 if period_type == "fixed" else period_offset
    now = datetime.now(timezone.utc)
    window = _project_period_range(
        period_type, project.period_start, project.period_end, now, effective_offset,
        tz_offset_minutes=tz_offset_minutes,
        month_start_day=ledger.month_start_day or 1,
    )
    if window is None:
        start = end = now
    else:
        start, end = window
    period_has_newer = period_type != "fixed" and effective_offset > 0
    period_has_older = period_type != "fixed"

    # 統計條(出帳/入帳):只看 exclude_from_stats,跟預算用量的
    # exclude_from_budget 是獨立開關(D2 慣例,見 list_budget_usage 附近註解)。
    stats_rows = db.execute(
        select(
            ReadTxProjection.tx_type,
            func.sum(func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)),
            func.count(),
        ).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.project_sync_id == project_id,
            ReadTxProjection.tx_type.in_(["expense", "income"]),
            ReadTxProjection.exclude_from_stats == sa_false(),
            ReadTxProjection.happened_at >= start,
            ReadTxProjection.happened_at < end,
        ).group_by(ReadTxProjection.tx_type)
    ).all()
    expense_total = expense_count = income_total = income_count = 0.0
    for tx_type, total, count in stats_rows:
        if tx_type == "expense":
            expense_total, expense_count = abs(float(total or 0.0)), int(count or 0)
        elif tx_type == "income":
            income_total, income_count = abs(float(total or 0.0)), int(count or 0)

    # 預算用量(spent)/收入併入預算(income_included_in_budget)都只看
    # exclude_from_budget,一次 group by 查兩個 tx_type 取代分開查詢。
    budget_stats_rows = db.execute(
        select(
            ReadTxProjection.tx_type,
            func.sum(func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)),
        ).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.project_sync_id == project_id,
            ReadTxProjection.tx_type.in_(["expense", "income"]),
            ReadTxProjection.exclude_from_budget == sa_false(),
            ReadTxProjection.happened_at >= start,
            ReadTxProjection.happened_at < end,
        ).group_by(ReadTxProjection.tx_type)
    ).all()
    spent = income_for_budget = 0.0
    for tx_type, total in budget_stats_rows:
        if tx_type == "expense":
            spent = abs(float(total or 0.0))
        elif tx_type == "income":
            income_for_budget = abs(float(total or 0.0))

    budget_amount = float(project.budget_amount) if project.budget_amount is not None else None
    carried_over = _project_carried_over(
        db, ledger=ledger, project_sync_id=project_id,
        period_type=period_type, period_start=project.period_start, period_end=project.period_end,
        now=now, effective_offset=effective_offset, tz_offset_minutes=tz_offset_minutes,
        month_start_day=ledger.month_start_day or 1,
        budget_amount=budget_amount, carryover_enabled=bool(project.carryover_enabled),
    )

    effective_budget: float | None = None
    remaining: float | None = None
    progress_pct: float | None = None
    project_status: str = "ok"
    if budget_amount is not None and budget_amount > 0:
        effective_budget = (
            budget_amount
            + (income_for_budget if project.income_included_in_budget else 0.0)
            + (carried_over or 0.0)
        )
        remaining = effective_budget - spent
        progress_pct = round(min(spent / effective_budget, 999.0) * 100.0, 2) if effective_budget > 0 else None
        if spent >= effective_budget:
            project_status = "over"
        elif spent >= effective_budget * 0.8:
            project_status = "warning"

    # 分類子預算配置(期間無關,不受 offset 影響)。
    budget_rows = db.scalars(
        select(ReadProjectCategoryBudgetProjection).where(
            ReadProjectCategoryBudgetProjection.ledger_id == ledger.id,
            ReadProjectCategoryBudgetProjection.project_sync_id == project_id,
        )
    ).all()
    budget_by_category = {b.category_sync_id: b for b in budget_rows if b.category_sync_id}
    allocated_total = 0.0
    for b in budget_rows:
        target = _resolve_project_category_budget_target(b, budget_amount)
        if target is not None:
            allocated_total += target
    unallocated_amount = (effective_budget - allocated_total) if effective_budget is not None else None

    # 這期各分類花費(跟 spent 同一組過濾條件:exclude_from_budget)。拆帳
    # (has_splits)父行 category_sync_id 是 NULL,group by 天然跳過,這次不做
    # 拆帳折算(見計畫「明確不做的部分」)。直接依交易自己的 category_sync_id
    # 分組,不論該分類是一級或二級(比照 mobile
    # `getProjectCategoryBreakdown`「依交易自己的 category_id 分組」的口徑,
    # 不做子分類 -> 父分類上卷)。
    spend_rows = db.execute(
        select(
            ReadTxProjection.category_sync_id,
            ReadTxProjection.tx_type,
            func.sum(func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)),
            func.count(),
        ).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.project_sync_id == project_id,
            ReadTxProjection.tx_type.in_(["expense", "income"]),
            ReadTxProjection.exclude_from_budget == sa_false(),
            ReadTxProjection.happened_at >= start,
            ReadTxProjection.happened_at < end,
            ReadTxProjection.category_sync_id.isnot(None),
        ).group_by(ReadTxProjection.category_sync_id, ReadTxProjection.tx_type)
    ).all()
    # (expense_total, income_total, count) 三元組,跟 mobile app
    # `getProjectCategoryBreakdown` 的 expense_total/income_total 雙軌口徑一致
    # ——純退款分類(只有 income 交易)也要能落進這個 dict,而不是被过滤掉。
    spend_by_category: dict[str, tuple[float, float, int]] = {}
    for cat_id, tx_type, total, count in spend_rows:
        cat_expense, cat_income, cat_count = spend_by_category.get(cat_id, (0.0, 0.0, 0))
        amount = abs(float(total or 0.0))
        if tx_type == "expense":
            cat_expense += amount
        else:
            cat_income += amount
        spend_by_category[cat_id] = (cat_expense, cat_income, cat_count + int(count or 0))

    # 分類全集:使用者所有一級分類(給「未設定預算」列出從沒使用過的分類用,
    # 收支兩種 kind 都要涵蓋——否則像「退款」這種收入類分類永遠不會出現在任
    # 一個分組) + 這個專案已設定分配的分類 + 這期實際有交易的分類(可能是二
    # 級子分類——分類子預算設計雖然只做到一級`docs/2026-09-06-project-
    # category-budget-period-switch-design.md §3.2/§11`,但拆解統計不能因此漏
    # 掉二級分類的交易,否則那筆交易連在畫面上都看不到,見「專案詳情頁看不到
    # 二級分類交易」修正)。
    level1_category_ids = list(db.scalars(
        select(UserCategoryProjection.sync_id).where(
            UserCategoryProjection.user_id == ledger.user_id,
            UserCategoryProjection.level == 1,
        ).order_by(UserCategoryProjection.sort_order.asc(), UserCategoryProjection.sync_id.asc())
    ).all())
    category_universe = list(dict.fromkeys([
        *level1_category_ids, *budget_by_category.keys(), *spend_by_category.keys(),
    ]))

    allocated_categories: list[ReadProjectBreakdownCategoryOut] = []
    unallocated_categories: list[ReadProjectBreakdownCategoryOut] = []
    unset_categories: list[ReadProjectBreakdownCategoryOut] = []
    for cat_id in category_universe:
        cat_spent, cat_income_spent, cat_count = spend_by_category.get(cat_id, (0.0, 0.0, 0))
        budget_row = budget_by_category.get(cat_id)
        if cat_count > 0 and budget_row is not None:
            target = _resolve_project_category_budget_target(budget_row, budget_amount)
            # 進度以淨額(支出-收入/退款)計算,跟 mobile app 的
            # `_CategoryBudgetTile` netAmount 口徑一致,退款會把已用進度打回去。
            cat_net = cat_spent - cat_income_spent
            cat_progress = (
                round(min(cat_net / target, 999.0) * 100.0, 2) if target is not None and target > 0 else None
            )
            allocated_categories.append(ReadProjectBreakdownCategoryOut(
                category_id=cat_id, spent=cat_spent, income_spent=cat_income_spent,
                count=cat_count, has_budget=True,
                budget_mode=cast("Any", budget_row.mode or "fixed"), budget_target=target,
                progress_pct=cat_progress,
            ))
        elif cat_count > 0:
            unallocated_categories.append(ReadProjectBreakdownCategoryOut(
                category_id=cat_id, spent=cat_spent, income_spent=cat_income_spent,
                count=cat_count, has_budget=False,
            ))
        else:
            unset_categories.append(ReadProjectBreakdownCategoryOut(
                category_id=cat_id, spent=0.0, count=0, has_budget=budget_row is not None,
            ))
    allocated_categories.sort(key=lambda c: c.spent - c.income_spent, reverse=True)
    unallocated_categories.sort(key=lambda c: c.spent - c.income_spent, reverse=True)

    return ReadProjectBreakdownOut(
        project_id=project_id,
        period_type=cast("Any", period_type),
        period_start=start,
        period_end=end,
        period_offset=effective_offset,
        period_has_newer=period_has_newer,
        period_has_older=period_has_older,
        expense_total=expense_total,
        expense_count=int(expense_count),
        income_total=income_total,
        income_count=int(income_count),
        budget_amount=budget_amount,
        effective_budget=effective_budget,
        carried_over=carried_over,
        spent=spent,
        remaining=remaining,
        progress_pct=progress_pct,
        status=cast("Any", project_status),
        allocated_total=allocated_total,
        unallocated_amount=unallocated_amount,
        allocated_categories=allocated_categories,
        unallocated_categories=unallocated_categories,
        unset_categories=unset_categories,
    )


@router.get(
    "/ledgers/{ledger_external_id}/accounts/{account_id}/statement",
    response_model=StatementPeriodOut,
)
def get_account_statement(
    ledger_external_id: str,
    account_id: str,
    cycle_offset: int = Query(default=0, ge=-120, le=12),
    sort_desc: bool = Query(default=False),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatementPeriodOut:
    """對帳模式(§2.10 Phase 5,2026-08-09 改版,對齊
    doc.moze.app/reconciliation/statement-mode):進入對帳模式看到的是「這期
    帳單」的交易清單本身(依卡分組 + 筆數/金額小計),不是輸入一個對帳單
    餘額數字去比對——v1(單筆「餘額比對記錄」CRUD)不符合原文設計,整個
    重做。`account_id` 範圍限制同 `get_account_billing_summary`:必須通過
    `credit_card_billing.is_billing_root`(account_group 或沒掛靠群組的獨立
    信用卡),因為原文入口本來就是「信用卡交易明細頁」,不是任意帳戶。
    `cycle_offset` 語意跟 billing-summary 的週期瀏覽一致:0 = 最近一次已
    結束的週期,正數往未來翻,負數往過去翻。`statement_total`/單筆
    `amount` 的正負號口徑跟 `compute_cycle_period_billing.new_spend` 一致
    (expense 為正、income/退款為負);每筆交易可以在對帳模式裡被勾選確認
    (`reconciled_at`,對應原文右滑「完成對帳確認」)或延後入帳
    (`deferred_posting_at`,對應原文左滑「延後入帳到下期帳單」)——web 版
    用按鈕+日期選擇器取代滑動手勢,兩者都透過既有的通用
    `PATCH .../transactions/{id}` 端點完成(帶 `reconciled_at`/
    `deferred_posting_at`),不需要為此新增專門的 write endpoint。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    _require_billing_root(account)
    _require_credit_card_schedule(account)

    children = credit_card_billing.resolve_billing_children(db, account=account)
    member_ids = credit_card_billing.billing_member_ids(account, children)
    now = datetime.now(timezone.utc)
    billing = credit_card_billing.compute_cycle_period_billing(
        db, ledger_id=ledger.id, group=account, children=children, now=now, cycle_offset=cycle_offset,
    )
    cycle_start_dt = credit_card_billing.date_to_utc_dt(billing["cycle_start"], end_of_day=True)
    cycle_end_dt = credit_card_billing.date_to_utc_dt(billing["cycle_end"], end_of_day=True)

    attr_date = attribution_date_expr()
    order_col = ReadTxProjection.happened_at.desc() if sort_desc else ReadTxProjection.happened_at.asc()
    # Phase 6(docs/PH6_USER_FEEDBACK_2026-08_SD.md 需求 #1):轉入這張卡/群組
    # 的轉帳(還款/預繳)原本完全沒進這個查詢——tx_type 白名單不含
    # "transfer",且轉帳交易的金額歸屬欄位是 to_account_sync_id,不是
    # account_sync_id。改成 OR 兩個分支:一般消費/收入沿用原本
    # account_sync_id 篩選;轉帳只收「轉入」(to_account_sync_id 命中這張卡),
    # 轉出這張卡的錢語意上不是消費,維持排除。
    rows = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger.id,
            or_(
                and_(
                    ReadTxProjection.tx_type.in_(["expense", "income"]),
                    ReadTxProjection.account_sync_id.in_(member_ids),
                ),
                and_(
                    ReadTxProjection.tx_type == "transfer",
                    ReadTxProjection.to_account_sync_id.in_(member_ids),
                ),
            ),
            attr_date > cycle_start_dt,
            attr_date <= cycle_end_dt,
        ).order_by(order_col)
    ).all()

    # 2026-09 使用者反饋:轉入這張卡/群組的轉帳裡,「信用卡繳款」
    # (routers/write/accounts.py::card_payment_ep)/「自動扣繳」
    # (services/credit_card_autopay.py)產生的還款轉帳,是清償某一期已結
    # 帳單的動作本身,不是這一期(它落入的那一期,通常是繳款當下所在的
    # 下一期)新增的消費/待確認項目——`compute_cycle_period_billing` 已經用
    # 不分時間窗口的 lifetime `paid_total` 把它算進 remaining_due/
    # carryover_due,這裡再收一次會讓使用者看到一筆「多出來」、金額對不上
    # 真實消費的轉帳列(見 credit_card_billing.is_card_settlement_note
    # docstring)。使用者自己手動轉帳/自訂了 note 不受影響,仍照 Phase 6
    # 既有語意當「還款/預繳」收進清單。
    rows = [
        row for row in rows
        if not (row.tx_type == "transfer" and credit_card_billing.is_card_settlement_note(row.note))
    ]

    # 2026-08 使用者反饋(需求 #7 改版):同一個回饋方案(rule)在這期帳單內
    # 的所有回饋入帳交易合併成一列顯示總金額,點擊才展開看原始消費明細(前端
    # 另外呼叫既有的 card-reward-rules/{rule_id}/transactions 端點,這裡只
    # 負責合併)。反查靠 `CardRewardPayout.payout_tx_sync_id`——手動記的
    # 「回饋金」分類交易(不是系統自動入帳)查不到對應紀錄,維持合併前的
    # 單筆顯示(reward_rule_id 留 None)。
    reward_tx_ids = [
        row.sync_id for row in rows
        if row.tx_type == "income" and row.category_name == card_rewards.REWARD_CATEGORY_NAME
    ]
    payout_rule_by_tx: dict[str, str] = {}
    if reward_tx_ids:
        payout_rows = db.execute(
            select(CardRewardPayout.payout_tx_sync_id, CardRewardPayout.rule_sync_id).where(
                CardRewardPayout.user_id == current_user.id,
                CardRewardPayout.payout_tx_sync_id.in_(reward_tx_ids),
            )
        ).all()
        payout_rule_by_tx = {tx_id: rule_id for tx_id, rule_id in payout_rows if tx_id}
    rule_label_by_id: dict[str, str] = {}
    if payout_rule_by_tx:
        rule_rows = db.execute(
            select(ReadCardRewardRuleProjection.sync_id, ReadCardRewardRuleProjection.label).where(
                ReadCardRewardRuleProjection.user_id == current_user.id,
                ReadCardRewardRuleProjection.sync_id.in_(set(payout_rule_by_tx.values())),
            )
        ).all()
        rule_label_by_id = {sync_id: (label or "") for sync_id, label in rule_rows}

    reward_group_rows: dict[str, list[ReadTxProjection]] = {}
    flat_rows: list[ReadTxProjection] = []
    for row in rows:
        rule_id = payout_rule_by_tx.get(row.sync_id)
        if rule_id is None:
            flat_rows.append(row)
        else:
            reward_group_rows.setdefault(rule_id, []).append(row)

    transactions_out: list[StatementTransactionOut] = []
    account_totals: dict[str, dict[str, Any]] = {}
    # "新增消費"(statement_total)刻意只算 expense/income,比照
    # `credit_card_billing.compute_cycle_period_billing.new_spend` 的口徑
    # ——轉入是還款/預繳,不是消費,不能被誤算進這格(SD 需求 #1)。
    statement_total = 0.0
    confirmed_count = 0
    confirmed_total = 0.0
    for row in flat_rows:
        is_transfer = row.tx_type == "transfer"
        if is_transfer:
            # 轉入視為還款/預繳,比照 income 記為負值(減少應繳餘額);金額
            # 歸屬欄位改用 to_account_sync_id/to_account_name。跨幣別轉帳
            # (2026-08):這張卡看到的應該是轉入卡片自身幣別的金額,不是轉出
            # 端的 amount——同幣種轉帳 to_amount 是 NULL,回退 amount。
            transfer_amount = row.to_amount if row.to_amount is not None else row.amount
            signed = -transfer_amount
            bucket_account_id = row.to_account_sync_id
            bucket_account_name = row.to_account_name
        else:
            signed = row.amount if row.tx_type == "expense" else -row.amount
            statement_total += signed
            bucket_account_id = row.account_sync_id
            bucket_account_name = row.account_name
        bucket = account_totals.setdefault(
            bucket_account_id, {"account_name": bucket_account_name, "count": 0, "total": 0.0},
        )
        bucket["count"] += 1
        bucket["total"] += signed
        if row.reconciled_at is not None:
            confirmed_count += 1
            confirmed_total += signed
        transactions_out.append(
            StatementTransactionOut(
                id=row.sync_id,
                account_id=bucket_account_id or "",
                account_name=bucket_account_name,
                tx_type=row.tx_type,
                amount=transfer_amount if is_transfer else row.amount,
                category_name=row.category_name,
                note=row.note,
                happened_at=row.happened_at,
                deferred_posting_at=row.deferred_posting_at,
                reconciled_at=row.reconciled_at,
                is_reward=row.category_name == card_rewards.REWARD_CATEGORY_NAME,
                member_tx_ids=[row.sync_id],
                fee_amount=row.fee_amount,
                fee_label=row.fee_label,
                discount_amount=row.discount_amount,
                discount_label=row.discount_label,
            )
        )

    for rule_id, members in reward_group_rows.items():
        members_sorted = sorted(members, key=lambda r: r.happened_at)
        total_amount = sum(m.amount for m in members_sorted)
        signed = -total_amount
        statement_total += signed
        bucket_account_id = members_sorted[0].account_sync_id
        bucket_account_name = members_sorted[0].account_name
        bucket = account_totals.setdefault(
            bucket_account_id, {"account_name": bucket_account_name, "count": 0, "total": 0.0},
        )
        bucket["count"] += len(members_sorted)
        bucket["total"] += signed
        all_confirmed = all(m.reconciled_at is not None for m in members_sorted)
        all_deferred = all(m.deferred_posting_at is not None for m in members_sorted)
        if all_confirmed:
            confirmed_count += 1
            confirmed_total += signed
        latest = members_sorted[-1]
        transactions_out.append(
            StatementTransactionOut(
                id=f"reward-group:{rule_id}",
                account_id=bucket_account_id or "",
                account_name=bucket_account_name,
                tx_type="income",
                amount=total_amount,
                category_name=card_rewards.REWARD_CATEGORY_NAME,
                note=None,
                happened_at=latest.happened_at,
                deferred_posting_at=latest.deferred_posting_at if all_deferred else None,
                reconciled_at=latest.reconciled_at if all_confirmed else None,
                is_reward=True,
                reward_rule_id=rule_id,
                reward_rule_label=rule_label_by_id.get(rule_id) or None,
                member_tx_ids=[m.sync_id for m in members_sorted],
            )
        )

    transactions_out.sort(key=lambda t: t.happened_at, reverse=sort_desc)

    accounts_out = [
        StatementAccountTotalOut(
            account_id=aid,
            account_name=data["account_name"],
            count=data["count"],
            total=round(data["total"], 2),
        )
        for aid, data in account_totals.items()
    ]

    return StatementPeriodOut(
        account_id=account.sync_id,
        account_name=account.name or "",
        cycle_start=billing["cycle_start"],
        cycle_end=billing["cycle_end"],
        due_date=billing["due_date"],
        cycle_offset=cycle_offset,
        has_older=billing["has_older"],
        has_newer=billing["has_newer"],
        statement_count=len(transactions_out),
        statement_total=round(statement_total, 2),
        confirmed_count=confirmed_count,
        confirmed_total=round(confirmed_total, 2),
        accounts=accounts_out,
        transactions=transactions_out,
    )


@router.get(
    "/ledgers/{ledger_external_id}/tx-templates",
    response_model=list[ReadTxTemplateOut],
)
def list_tx_templates(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTxTemplateOut]:
    """交易範本只读列表(§2.7)。"""
    is_admin = _is_admin(current_user)
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id, is_admin=is_admin,
    )
    ledger_name = _resolve_ledger_name(db, ledger=ledger)
    source_change_id = _get_latest_change_id(db, ledger_id=ledger.id)

    cat_rows = db.execute(
        select(UserCategoryProjection.sync_id, UserCategoryProjection.name)
        .where(UserCategoryProjection.user_id == current_user.id)
    ).all()
    cat_name_by_sync = {r.sync_id: (r.name or "").strip() for r in cat_rows}
    acc_rows = db.execute(
        select(UserAccountProjection.sync_id, UserAccountProjection.name)
        .where(UserAccountProjection.user_id == current_user.id)
    ).all()
    acc_name_by_sync = {r.sync_id: (r.name or "").strip() for r in acc_rows}

    rows = db.scalars(
        select(ReadTxTemplateProjection).where(
            ReadTxTemplateProjection.ledger_id == ledger.id,
        ).order_by(ReadTxTemplateProjection.sort_order.asc(), ReadTxTemplateProjection.name.asc())
    ).all()
    out: list[ReadTxTemplateOut] = []
    for row in rows:
        tag_ids: list[str] = []
        if row.tag_sync_ids_json:
            try:
                parsed = json.loads(row.tag_sync_ids_json)
                if isinstance(parsed, list):
                    tag_ids = [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass
        out.append(
            ReadTxTemplateOut(
                id=row.sync_id,
                name=row.name or "",
                tx_type=row.tx_type or "expense",
                amount=float(row.amount or 0),
                note=row.note,
                category_id=row.category_sync_id,
                category_name=cat_name_by_sync.get(row.category_sync_id) if row.category_sync_id else None,
                account_id=row.account_sync_id,
                account_name=acc_name_by_sync.get(row.account_sync_id) if row.account_sync_id else None,
                from_account_id=row.from_account_sync_id,
                from_account_name=acc_name_by_sync.get(row.from_account_sync_id) if row.from_account_sync_id else None,
                to_account_id=row.to_account_sync_id,
                to_account_name=acc_name_by_sync.get(row.to_account_sync_id) if row.to_account_sync_id else None,
                tag_ids=tag_ids,
                sort_order=int(row.sort_order or 0),
                last_change_id=source_change_id,
                ledger_id=ledger.external_id,
                ledger_name=ledger_name,
            )
        )
    return out


def _current_period_range(
    start_day: int, now: datetime
) -> tuple[datetime, datetime]:
    """跟手机端 `local_budget_repository.getBudgetUsage` 同款月周期算法:
    - 当天 >= start_day → 本月 start_day 起,下月 start_day 止
    - 当天 < start_day → 上月 start_day 起,本月 start_day 止
    边界统一到 [1, 28],避免 29/30/31 在 2 月翻车。
    调用方现统一传账本 month_start_day(设计 D5),不再传 budget.start_day。
    """
    day = max(1, min(28, start_day or 1))
    if now.day >= day:
        start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
        # 下月同 day —— year/month 进位
        if now.month == 12:
            end = start.replace(year=now.year + 1, month=1)
        else:
            end = start.replace(month=now.month + 1)
    else:
        # 上月 day —— 借位
        if now.month == 1:
            start = now.replace(year=now.year - 1, month=12, day=day,
                                hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(month=now.month - 1, day=day,
                                hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    return start, end


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
    # user-global per-user 表已经唯一,_dedupe_by_sync_id 是 no-op。
    rows = _dedupe_by_sync_id(
        db.scalars(
            select(UserTagProjection)
            .where(UserTagProjection.user_id == current_user.id)
            .order_by(UserTagProjection.sync_id.asc())
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


