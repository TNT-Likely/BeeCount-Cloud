"""跨账本聚合读端点:/workspace/{accounts,categories,tags,transactions,
ledger-counts,analytics}。

跟 ledgers.py 的区别:这里的查询不锁定到单个账本,会扫 caller 所有可见账本的
projection 做聚合(tx 计数 / balance / category 排行等)。
去重 / 跨账本 dedup / owner 信息回填的逻辑也在这里。"""
from __future__ import annotations

import statistics as _stats
from typing import Literal, NamedTuple

from pydantic import BaseModel
from sqlalchemy import false as sa_false

from ._shared import *  # noqa: F401,F403 — imports + helpers + router
from ...models import ExchangeRateCache, UserExchangeRateProjection
from ...services.exchange_rate import fetcher as _exchange_rate_fetcher

# ---------------------------------------------------------------------------
# 净值历史 — 响应 schema
# ---------------------------------------------------------------------------

class NetWorthHistorySeriesItemOut(BaseModel):
    bucket: str
    net_worth: float
    assets: float
    liabilities: float


class NetWorthHistoryOut(BaseModel):
    series: list[NetWorthHistorySeriesItemOut]
    multi_currency: bool

@router.get("/workspace/transactions", response_model=WorkspaceTransactionPageOut)
def list_workspace_transactions(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tx_type: str | None = Query(default=None),
    account_name: str | None = Query(default=None),
    q: str | None = Query(default=None),
    tx_sync_id: str | None = Query(default=None, description="按 tx 自身 syncId 精确过滤(用于 admin/integrity 跳到具体交易)"),
    tag_sync_id: str | None = Query(default=None, description="按 tag syncId 精确过滤,不走模糊搜索"),
    category_sync_id: str | None = Query(default=None, description="按 category syncId 精确过滤"),
    account_sync_id: str | None = Query(default=None, description="按 account syncId 精确过滤(含 from/to)"),
    amount_min: float | None = Query(default=None, description="金额下限(含)。按 abs(amount) 比较以兼容 expense 负值"),
    amount_max: float | None = Query(default=None, description="金额上限(含)"),
    date_from: datetime | None = Query(default=None, description="happened_at >= date_from"),
    date_to: datetime | None = Query(default=None, description="happened_at < date_to(独占,前端传当天 23:59:59 即可包含整天)"),
    limit: int = Query(default=20, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceTransactionPageOut:
    is_admin = _is_admin(current_user)

    # 账本筛选 → 内部 id 列表(已排除软删账本,issue #31)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
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
    # tx 自身 sync_id 过滤(单条精确查找)
    if tx_sync_id:
        query = query.where(ReadTxProjection.sync_id == tx_sync_id)
    # Tag 精确过滤:用 tag_sync_ids_json LIKE 含引号形式 `"<sync_id>"`,确保是 JSON
    # 数组里那个 id(而不是 note/tags_csv 里的字符串误匹配)。前端标签弹窗走这个参数。
    if tag_sync_id:
        query = query.where(
            ReadTxProjection.tag_sync_ids_json.like(f'%"{tag_sync_id}"%')
        )
    if category_sync_id:
        query = query.where(ReadTxProjection.category_sync_id == category_sync_id)
    if account_sync_id:
        # 主帳戶(account_group)自己從不擁有交易(`_assert_account_not_group`
        # 擋住了),原本這裡只精確比對這一個 sync_id,導致打開群組的帳戶
        # 詳情永遠是空的(2026-08-04 使用者實測回報)。這裡展開成群組底下
        # 全部子帳戶一起查,跟 billing-summary 讀端點用同一個
        # `credit_card_billing.resolve_billing_children` 解析。
        account_sync_ids = {account_sync_id}
        lookup_account = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == current_user.id,
                UserAccountProjection.sync_id == account_sync_id,
            )
        )
        if lookup_account is not None and lookup_account.account_type == "account_group":
            children = credit_card_billing.resolve_billing_children(db, account=lookup_account)
            account_sync_ids.update(c.sync_id for c in children)
        query = query.where(or_(
            ReadTxProjection.account_sync_id.in_(account_sync_ids),
            ReadTxProjection.from_account_sync_id.in_(account_sync_ids),
            ReadTxProjection.to_account_sync_id.in_(account_sync_ids),
        ))
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
    # 金额范围 — 跟 mobile search_page 对齐,按 abs(amount) 过滤(expense 是
    # 正值存储,但用户视觉上看到的也是正数,直接比较 amount 即可。如果未来
    # 改成 signed 存储再调整)。
    if amount_min is not None:
        query = query.where(ReadTxProjection.amount >= amount_min)
    if amount_max is not None:
        query = query.where(ReadTxProjection.amount <= amount_max)
    # 日期范围 — happened_at 是 UTC 存储,前端传 ISO datetime 即可;
    # date_from 含,date_to 不含(独占,匹配 mobile "<endOfDay" 半开区间习惯)。
    if date_from is not None:
        query = query.where(ReadTxProjection.happened_at >= date_from)
    if date_to is not None:
        query = query.where(ReadTxProjection.happened_at < date_to)

    total = int(db.scalar(
        select(func.count()).select_from(query.subquery())
    ) or 0)

    query = query.order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    ).offset(offset).limit(limit)
    rows = db.scalars(query).all()

    # 退款反查(§2.12.3):同 ledgers.py::list_transactions 的批次聚合,scope
    # 到本次可见的多个账本内。
    page_sync_ids = [r.sync_id for r in rows]
    refunds_by_target: dict[str, list[ReadTxRefundSummaryOut]] = {}
    if page_sync_ids:
        refund_rows = db.execute(
            select(
                ReadTxProjection.refund_of_sync_id,
                ReadTxProjection.sync_id,
                ReadTxProjection.amount,
                ReadTxProjection.happened_at,
            ).where(
                ReadTxProjection.ledger_id.in_(ledger_internal_ids),
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
    # sync_id -> (counterparty_name, direction) 字典,同 refund 批次 join
    # 同一套模式,scope 到本次可见的多个账本内。
    debt_sync_ids = {r.debt_sync_id for r in rows if r.debt_sync_id}
    debt_info_by_id: dict[str, tuple[str | None, str | None]] = {}
    if debt_sync_ids:
        debt_info_rows = db.execute(
            select(
                ReadDebtProjection.sync_id,
                ReadDebtProjection.counterparty_name,
                ReadDebtProjection.direction,
            ).where(
                ReadDebtProjection.ledger_id.in_(ledger_internal_ids),
                ReadDebtProjection.sync_id.in_(debt_sync_ids),
            )
        ).all()
        for debt_sid, debt_counterparty_name, debt_direction in debt_info_rows:
            debt_info_by_id[debt_sid] = (debt_counterparty_name, debt_direction)

    # 專案(Phase 13,docs/PH13_PROJECT_SD.md):同款批次反查模式,scope 到
    # 本次可见的多个账本内。
    project_sync_ids = {r.project_sync_id for r in rows if r.project_sync_id}
    project_name_by_id: dict[str, str] = {}
    if project_sync_ids:
        project_rows = db.execute(
            select(
                ReadProjectProjection.sync_id,
                ReadProjectProjection.name,
            ).where(
                ReadProjectProjection.ledger_id.in_(ledger_internal_ids),
                ReadProjectProjection.sync_id.in_(project_sync_ids),
            )
        ).all()
        for project_sid, project_name in project_rows:
            project_name_by_id[project_sid] = project_name

    # §7 共享账本:per-tx 创建者/编辑者头像 + name 用。从 projection 收集
    # 所有出现过的 user_id,一次查 User + UserProfile,O(N) → O(distinct users)。
    # 必须放 rows 之后(原 commit 顺序错了导致 UnboundLocalError)。
    # 同时把账本 owner 也收进去 — legacy tx(created_by_user_id IS NULL)
    # fallback 走 owner_info[0],owner uid 必须在 user_info_map 里才能映射
    # 回 email/display_name,否则前端 created_by_email 全 null,信息回退到
    # tx 列。
    actor_user_ids: set[str] = set()
    for r in rows:
        cu = r.created_by_user_id
        if cu:
            actor_user_ids.add(cu)
        eu = r.last_edited_by_user_id
        if eu:
            actor_user_ids.add(eu)
    for owner_uid, _owner_email in owner_map.values():
        if owner_uid:
            actor_user_ids.add(owner_uid)
    user_info_map = _user_info_map(db, actor_user_ids)

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

        # §7 共享账本:per-tx 创建者 + 编辑者真实值(对齐 projection 的
        # created_by_user_id / last_edited_by_user_id 列)。owner_info 仅作为
        # 回退,projection 没填时用账本所有者。
        creator_uid = row.created_by_user_id or owner_info[0]
        editor_uid = row.last_edited_by_user_id or creator_uid
        creator_info = user_info_map.get(creator_uid or '', (None, None, None, 0))
        editor_info = user_info_map.get(editor_uid or '', creator_info)
        debt_info = debt_info_by_id.get(row.debt_sync_id) if row.debt_sync_id else None
        out_items.append(
            WorkspaceTransactionOut(
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
                last_change_id=change_id,
                ledger_id=led_ext_id,
                ledger_name=led_name,
                created_by_user_id=creator_uid,
                created_by_email=creator_info[0],
                created_by_display_name=creator_info[1],
                created_by_avatar_url=(
                    f"/api/v1/profile/avatar/{creator_uid}?v={creator_info[3]}"
                    if creator_uid and creator_info[2] else None
                ),
                created_by_avatar_version=creator_info[3] if creator_info[2] else None,
                last_edited_by_user_id=editor_uid,
                last_edited_by_email=editor_info[0],
                last_edited_by_display_name=editor_info[1],
                last_edited_by_avatar_url=(
                    f"/api/v1/profile/avatar/{editor_uid}?v={editor_info[3]}"
                    if editor_uid and editor_info[2] else None
                ),
                last_edited_by_avatar_version=editor_info[3] if editor_info[2] else None,
            )
        )
    return WorkspaceTransactionPageOut(
        items=out_items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# CSV 导出 — 跟 list_workspace_transactions 共用一组 filter,流式输出。
# 设计:.docs/web-csv-export-design.md
# ---------------------------------------------------------------------------


_CSV_HEADERS_BY_LANG: dict[str, list[str]] = {
    # 跟 mobile lib/pages/data/export_page.dart 的 12 列严格对齐(v30 加币种):
    # Type, Category, SubCategory, Amount, Currency, Account, FromAccount,
    # ToAccount, Note, Time, Tags, Attachments
    "zh-CN": ["类型", "分类", "二级分类", "金额", "币种", "账户", "转出账户",
              "转入账户", "备注", "时间", "标签", "附件"],
    "zh-TW": ["類型", "分類", "二級分類", "金額", "幣種", "帳戶", "轉出帳戶",
              "轉入帳戶", "備註", "時間", "標籤", "附件"],
    "en":    ["Type", "Category", "Subcategory", "Amount", "Currency",
              "Account", "From Account", "To Account", "Note", "Time",
              "Tags", "Attachments"],
}

_TX_TYPE_LABELS_BY_LANG: dict[str, dict[str, str]] = {
    "zh-CN": {"income": "收入", "expense": "支出", "transfer": "转账"},
    "zh-TW": {"income": "收入", "expense": "支出", "transfer": "轉帳"},
    "en":    {"income": "Income", "expense": "Expense", "transfer": "Transfer"},
}


def _normalize_lang(lang: str | None) -> str:
    """归一化 ?lang= 到 zh-CN / zh-TW / en,无效值落回 en(跟 web 默认一致)。"""
    if not lang:
        return "en"
    s = lang.strip().lower().replace("_", "-")
    if s.startswith("zh-tw") or s in {"zh-hant", "zh-hk", "zh-mo"}:
        return "zh-TW"
    if s.startswith("zh"):
        return "zh-CN"
    return "en"


@router.get("/workspace/transactions.csv")
def export_workspace_transactions_csv(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tx_type: str | None = Query(default=None),
    account_name: str | None = Query(default=None),
    q: str | None = Query(default=None),
    tx_sync_id: str | None = Query(default=None),
    tx_ids: list[str] | None = Query(
        default=None,
        description="按 sync_id 集合导出(批量选中场景);传入则忽略其它过滤参数",
    ),
    tag_sync_id: str | None = Query(default=None),
    category_sync_id: str | None = Query(default=None),
    account_sync_id: str | None = Query(default=None),
    amount_min: float | None = Query(default=None),
    amount_max: float | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    tz_offset_minutes: int = Query(
        default=0,
        description="客户端本地时区偏移,正数 = 东半球;Time 列按这个折算",
    ),
    lang: str | None = Query(
        default=None,
        description="表头 + Type 列语言。zh-CN / zh-TW / en;默认 en",
    ),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导出当前用户可见账本的 tx 明细为 CSV(UTF-8 BOM)。

    跟 mobile `lib/pages/data/export_page.dart` 严格对齐(11 列、本地化表头、
    parent/sub 分类拆列、单 Time 列、Type 本地化为 收入/支出/转账)。这样 web
    导出 → mobile 导入可以无损 round-trip。

    - 字段(11):type,category,subcategory,amount,account,from_account,
      to_account,note,time,tags,attachments
    - subcategory:level=2 时 category 写父类名、subcategory 写当前;level=1 时
      只写 category。父类名走 LEFT JOIN user_category_projection 拿 parent_name。
    - time:`  YYYY-MM-DD HH:mm:ss  `(前后各 2 空格,App 一致,Excel 列宽好看)
    - 流式 yield_per(500),50k+ 笔 server 内存稳定
    - 文件名:`beecount-<ledger>-<date_from>_<date_to>.csv`,中文走 RFC 5987
    """
    from urllib.parse import quote
    from sqlalchemy.orm import aliased
    from fastapi.responses import StreamingResponse

    is_admin = _is_admin(current_user)
    lang_key = _normalize_lang(lang)
    headers = _CSV_HEADERS_BY_LANG[lang_key]
    type_labels = _TX_TYPE_LABELS_BY_LANG[lang_key]

    # 账本筛选 — 跟 list_workspace_transactions 完全一致(含软删过滤,issue #31)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )

    if ledgers:
        ledger_internal_ids = [l.id for l in ledgers]
        primary_name = _sanitize_filename(_resolve_ledger_name(db, ledger=ledgers[0]))
    else:
        ledger_internal_ids = []
        primary_name = "ledger"
    # v30 多币种:currency_code NULL 的历史行按其账本本位币兜底(导出自包含)
    ledger_currency_by_id = {l.id: (l.currency or "CNY") for l in ledgers}

    # LEFT JOIN UserCategoryProjection 拿 level + parent_name,做 parent/sub 列拆分。
    # category 是 user-global,按 user_id 而非 ledger_id JOIN。
    Cat = aliased(UserCategoryProjection)

    query = (
        select(
            ReadTxProjection,
            Cat.level.label("cat_level"),
            Cat.parent_name.label("cat_parent_name"),
        )
        .select_from(ReadTxProjection)
        .outerjoin(
            Cat,
            and_(
                Cat.user_id == ReadTxProjection.user_id,
                Cat.sync_id == ReadTxProjection.category_sync_id,
            ),
        )
    )

    if ledger_internal_ids:
        query = query.where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
    else:
        query = query.where(false_literal())

    # tx_ids 模式:批量选中导出走 sync_id IN (...) 直接限定,忽略其它 filter
    # —— 用户已经显式选好了行,日期 / q 等参数再叠加只会让 CSV 比预期少几条
    # 这种反直觉行为。ledger 限定仍生效,跨 ledger 的 sync_id 不会越权。
    if tx_ids:
        cleaned_ids = [s for s in (s.strip() for s in tx_ids) if s]
        if cleaned_ids:
            query = query.where(ReadTxProjection.sync_id.in_(cleaned_ids))
        else:
            query = query.where(false_literal())
    else:
        if tx_type:
            query = query.where(ReadTxProjection.tx_type == tx_type)
        if account_name:
            pattern = f"%{account_name}%"
            query = query.where(or_(
                ReadTxProjection.account_name.ilike(pattern),
                ReadTxProjection.from_account_name.ilike(pattern),
                ReadTxProjection.to_account_name.ilike(pattern),
            ))
        if tx_sync_id:
            query = query.where(ReadTxProjection.sync_id == tx_sync_id)
        if tag_sync_id:
            query = query.where(
                ReadTxProjection.tag_sync_ids_json.like(f'%"{tag_sync_id}"%')
            )
        if category_sync_id:
            query = query.where(ReadTxProjection.category_sync_id == category_sync_id)
        if account_sync_id:
            query = query.where(or_(
                ReadTxProjection.account_sync_id == account_sync_id,
                ReadTxProjection.from_account_sync_id == account_sync_id,
                ReadTxProjection.to_account_sync_id == account_sync_id,
            ))
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
        if amount_min is not None:
            query = query.where(ReadTxProjection.amount >= amount_min)
        if amount_max is not None:
            query = query.where(ReadTxProjection.amount <= amount_max)
        if date_from is not None:
            query = query.where(ReadTxProjection.happened_at >= date_from)
        if date_to is not None:
            query = query.where(ReadTxProjection.happened_at < date_to)

    query = query.order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    ).execution_options(stream_results=True)

    tz_delta = timedelta(minutes=tz_offset_minutes)

    def generate():
        # BOM 让 Excel 双击中文不乱码
        yield "\ufeff"
        yield ",".join(_csv_field(h) for h in headers) + "\n"
        for row in db.execute(query).yield_per(500):
            tx = row[0]  # ReadTxProjection
            cat_level = row.cat_level
            cat_parent_name = row.cat_parent_name
            is_transfer = tx.tx_type == "transfer"

            local_dt = _to_utc(tx.happened_at) + tz_delta
            tags_list = _tags_list(tx.tags_csv)
            attachment_names = _attachment_names(tx.attachments_json)

            # 转账无分类;否则按 level 拆 parent/sub
            if is_transfer:
                category_col = ""
                sub_category_col = ""
            elif cat_level == 2 and cat_parent_name:
                category_col = cat_parent_name
                sub_category_col = tx.category_name or ""
            else:
                category_col = tx.category_name or ""
                sub_category_col = ""

            type_label = type_labels.get(tx.tx_type or "", tx.tx_type or "")

            # mobile 用前后各 2 空格,Excel 列宽好看 — 完全一致
            time_str = (
                f"  {local_dt.strftime('%Y-%m-%d %H:%M:%S')}  "
            )

            # v30 多币种:currency_code 为 NULL 的历史行按账本本位币兜底
            # (与统计读取端同语义,导出自包含可回导)
            currency_col = (
                tx.currency_code
                or ledger_currency_by_id.get(tx.ledger_id)
                or "CNY"
            ).upper()

            yield ",".join([
                _csv_field(type_label),
                _csv_field(category_col),
                _csv_field(sub_category_col),
                f"{tx.amount:.2f}" if tx.amount is not None else "",
                _csv_field(currency_col),
                _csv_field(tx.account_name) if not is_transfer else "",
                _csv_field(tx.from_account_name) if is_transfer else "",
                _csv_field(tx.to_account_name) if is_transfer else "",
                _csv_field(tx.note),
                _csv_field(time_str),
                _csv_field(",".join(tags_list)),
                _csv_field(",".join(attachment_names)),
            ]) + "\n"

    if date_from is None and date_to is None:
        # 没设日期 → 用导出当下本地时间戳。多次下载文件名才能区分,且非日期
        # filter(category / account / q)也不会被误标成 "all"。
        local_now = datetime.now(timezone.utc) + tz_delta
        period_segment = local_now.strftime("%Y%m%d-%H%M%S")
    else:
        period_from = date_from.isoformat()[:10] if date_from else "all"
        period_to = date_to.isoformat()[:10] if date_to else "all"
        period_segment = f"{period_from}_{period_to}"
    filename = f"beecount-{primary_name}-{period_segment}.csv"
    ascii_filename = filename.encode("ascii", "replace").decode("ascii")

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Cache-Control": "no-store",
        },
    )


def _attachment_names(raw: str | None) -> list[str]:
    """从 attachments_json 里挑 fileName(跟 mobile 导出用 fileName 一致)。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, dict):
            name = item.get("fileName") or item.get("file_name") or item.get("name")
            if name:
                out.append(str(name))
    return out


def false_literal():
    """SQL FALSE 字面量(用于"用户无任何账本可读"时的强制空结果)。"""
    from sqlalchemy import literal
    return literal(False)


@router.get("/workspace/accounts", response_model=list[WorkspaceAccountOut])
async def list_workspace_accounts(
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

    # --- 1. 从 snapshot 聚合账户（手机同步写入的数据，已排除软删账本 issue #31） ---
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers}
    change_id_by_ledger = {l.id: _get_latest_change_id(db, ledger_id=l.id) for l in ledgers}

    # account 是 **user-global** 实体(Flutter 侧 Accounts 表没 ledger_id),但
    # projection 历史上 per-ledger 重复存(snapshot 每 ledger 各一份)。所以 tx
    # 聚合也要 **跨 ledger** 按 account_sync_id 累加,不能按 (ledger, account)
    # 分桶 —— 否则后面的 dedup(`best_by_key` 按 last_change_id 留一份 ledger
    # 下的 account)会跟 tx 聚合的 ledger 对不上,tx_count 永远 miss。
    # 用户可见 ledger 范围靠 `ledger_id IN ledger_internal_ids` 限定。
    from sqlalchemy import case as sa_case

    # 帳戶餘額(balance)只該反映「已經發生」的交易 —— 使用者可以在記帳表單
    # 自由選未來日期(例如先記一筆下個月才會扣款的訂閱),這種交易在它真正
    # 發生前不該被算進目前餘額/信用卡欠款,否則帳戶列表的金額會比實際偏高
    # (2026-08 使用者反饋 #1:信用卡顯示的消費金額不該連未來的都算進去)。
    # 這裡刻意維持 `happened_at` 口徑(不是 `attribution_date_expr()`)—— 對齐
    # `deferred_posting.py` 文件說明,workspace 主要收支統計刻意不採用「入帳日」
    # 口徑,只是額外加一個「不算未來」的上界。
    now = datetime.now(timezone.utc)

    # Main account stats: income + expense,按 account_sync_id 聚合
    main_stats = db.execute(
        select(
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
            ReadTxProjection.happened_at <= now,
        ).group_by(ReadTxProjection.account_sync_id)
    ).all()

    # 餘額調整(§2.10 Phase 5):amount 本身带正负号的差量,直接累加进 balance,
    # 不像 income/expense 分开算再相减。
    adjustment_rows = db.execute(
        select(
            ReadTxProjection.account_sync_id,
            func.count().label("cnt"),
            func.coalesce(func.sum(ReadTxProjection.amount), 0.0).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "adjustment",
            ReadTxProjection.account_sync_id.is_not(None),
            ReadTxProjection.happened_at <= now,
        ).group_by(ReadTxProjection.account_sync_id)
    ).all()

    # Transfer adjustments: from_account = minus, to_account = plus
    transfer_from = db.execute(
        select(
            ReadTxProjection.from_account_sync_id,
            func.count().label("cnt"),
            # 轉帳手續費/折損(2026-08-29):轉出腳疊加轉出側手續費,沒有手續費
            # 的舊資料 fee_amount 為 NULL,COALESCE 退回 amount,行為不變。跟
            # App 端 LocalAccountRepository._transferOutEffect 是同一套公式。
            func.coalesce(
                func.sum(
                    ReadTxProjection.amount
                    + func.coalesce(ReadTxProjection.fee_amount, 0.0)
                ),
                0.0,
            ).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.from_account_sync_id.is_not(None),
            ReadTxProjection.happened_at <= now,
        ).group_by(ReadTxProjection.from_account_sync_id)
    ).all()
    transfer_to = db.execute(
        select(
            ReadTxProjection.to_account_sync_id,
            func.count().label("cnt"),
            # 跨幣別轉帳(2026-08):轉入端要用轉入帳戶自身幣別的金額,不是
            # 轉出端的 amount——同幣種轉帳 to_amount 是 NULL,COALESCE 退回
            # amount,行為不變。轉帳手續費/折損(2026-08-29):再疊加轉入側
            # 折損,沒有折損的舊資料 discount_amount 為 NULL,COALESCE 退回
            # 0,行為不變。跟 App 端 _transferInEffect 是同一套公式。
            func.coalesce(
                func.sum(
                    func.coalesce(ReadTxProjection.to_amount, ReadTxProjection.amount)
                    - func.coalesce(ReadTxProjection.discount_amount, 0.0)
                ),
                0.0,
            ).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.to_account_sync_id.is_not(None),
            ReadTxProjection.happened_at <= now,
        ).group_by(ReadTxProjection.to_account_sync_id)
    ).all()

    # 合并成 per-account 的 dict(跨 ledger,key 只是 sync_id)
    stats: dict[str, dict[str, float | int]] = {}
    for acc, cnt, inc, exp in main_stats:
        stats[acc] = {"count": int(cnt), "income": float(inc),
                      "expense": float(exp), "balance": float(inc) - float(exp)}
    for acc, cnt, amt in transfer_from:
        bucket = stats.setdefault(acc,
                                   {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0})
        bucket["count"] = int(bucket["count"]) + int(cnt)
        bucket["balance"] = float(bucket["balance"]) - float(amt)
    for acc, cnt, amt in transfer_to:
        bucket = stats.setdefault(acc,
                                   {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0})
        bucket["count"] = int(bucket["count"]) + int(cnt)
        bucket["balance"] = float(bucket["balance"]) + float(amt)
    for acc, cnt, amt in adjustment_rows:
        bucket = stats.setdefault(acc,
                                   {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0})
        bucket["count"] = int(bucket["count"]) + int(cnt)
        bucket["balance"] = float(bucket["balance"]) + float(amt)

    # user-global 重构:account 是 per-user 表,直接按 user_id 拉,不再 per-ledger
    # 重复存 + dedup。target_user 是 admin 模式下指定的 user_id,否则 caller。
    target_user_id = user_id if (is_admin and user_id) else current_user.id
    target_email = db.scalar(select(User.email).where(User.id == target_user_id))

    # 用户级 last_change_id:该用户最近的 user-scope SyncChange.change_id(account)。
    # 给前端做缓存失效 key 用,跨账本统一。
    account_last_change_id = int(db.scalar(
        select(func.coalesce(func.max(SyncChange.change_id), 0))
        .where(
            SyncChange.user_id == target_user_id,
            SyncChange.scope == "user",
            SyncChange.entity_type == "account",
        )
    ) or 0)

    account_query = select(UserAccountProjection).where(
        UserAccountProjection.user_id == target_user_id
    )
    if q:
        account_query = account_query.where(UserAccountProjection.name.ilike(f"%{q}%"))

    all_accounts: list[WorkspaceAccountOut] = []
    raw_by_sync_id: dict[str, UserAccountProjection] = {}
    for acct in db.scalars(account_query).all():
        name = (acct.name or "").strip()
        if not name:
            continue
        sync_id = acct.sync_id
        raw_by_sync_id[sync_id] = acct
        init_bal = float(acct.initial_balance or 0.0)
        bucket = stats.get(sync_id)
        income_total = float(bucket.get("income", 0.0)) if bucket else 0.0
        expense_total = float(bucket.get("expense", 0.0)) if bucket else 0.0
        tx_count = int(bucket.get("count", 0)) if bucket else 0
        movement = float(bucket.get("balance", 0.0)) if bucket else 0.0
        all_accounts.append(
            WorkspaceAccountOut(
                id=sync_id,
                name=name,
                account_type=acct.account_type,
                currency=acct.currency,
                initial_balance=init_bal,
                last_change_id=account_last_change_id,
                ledger_id="",            # user-global 不挂账本
                ledger_name="",
                created_by_user_id=target_user_id,
                created_by_email=target_email,
                note=acct.note,
                credit_limit=acct.credit_limit,
                billing_day=acct.billing_day,
                payment_due_day=acct.payment_due_day,
                bank_name=acct.bank_name,
                card_last_four=acct.card_last_four,
                parent_account_id=acct.parent_account_id,
                hidden=acct.hidden,
                auto_pay_enabled=acct.auto_pay_enabled,
                auto_pay_from_account_id=acct.auto_pay_from_account_id,
                avatar_cloud_file_id=acct.avatar_cloud_file_id,
                avatar_cloud_sha256=acct.avatar_cloud_sha256,
                swipesmart_card_id=acct.swipesmart_card_id,
                include_in_total=acct.include_in_total,
                sort_order=acct.sort_order,
                investment_settings=_investment_settings_or_none(acct.investment_settings_json),
                tx_count=tx_count,
                income_total=income_total,
                expense_total=expense_total,
                balance=init_bal + movement,
            )
        )

    # 主帳戶(§2.9,2026-08-02 群組模型):account_group 是純管理容器,底下
    # 子帳戶(可能不只信用卡,以後銀行帳戶群組也走這條路)的統計要加總回填到
    # 群組自己身上,不然看起來像壞掉的 0(2026-08-02 web UI 手測發現)。
    # 2026-08-03 補強:帳單分期改成直接掛在 group 自己身上(§2.3 第四輪,
    # 對齊使用者反饋「分期金額應該附屬主帳戶」)之後,group 自己也可能有
    # 真實交易了——這裡改成在群組自己已經按 account_sync_id 算出來的統計
    # 上「疊加」子帳戶加總,而不是整段覆蓋掉,否則 group 自己持有的分期
    # 期數交易會被子帳戶加總結果蓋掉,金額對不上。
    children_by_parent: dict[str, list[WorkspaceAccountOut]] = {}
    for acc in all_accounts:
        if acc.parent_account_id:
            children_by_parent.setdefault(acc.parent_account_id, []).append(acc)

    # 2026-08-12 使用者回報:子帳戶跨幣別時(例如 TWD 群組底下掛了 JPY/USD
    # 子帳戶),上面「加總回填到群組自己身上」不能對 balance/income_total/
    # expense_total 這些金額欄位不分幣別直接 sum() —— 19400 TWD + 10800 JPY
    # 不能直接相加。這裡先找出「有跨幣別子帳戶」的群組各自需要的 target base
    # (群組自己的 currency),批次備好匯率,rollup 迴圈才能按幣別分別換算。
    _needed_bases: set[str] = set()
    for _acc in all_accounts:
        if _acc.account_type != "account_group":
            continue
        _group_cur = (_acc.currency or "CNY").upper()
        for _kid in children_by_parent.get(_acc.id, []):
            if (_kid.currency or "CNY").upper() != _group_cur:
                _needed_bases.add(_group_cur)
                break

    # base(群組幣別)→ {quote(大寫): rate},即 1 quote = rate base。跟下方
    # workspace_net_worth_history 的 _net() 同一套組法:自動快取(1 base = x
    # quote,取倒數)先墊底,使用者手動 override(方向已經是 1 quote = rate base)
    # 覆蓋掉自動值。抓不到匯率(代理關閉/上游全掛/從未快取過)的 base 就不會有
    # 對應 entry——呼叫方對這個 base 底下缺匯率的幣別必須整筆剔除,絕不裸加
    # 1.0(對齊 _net() 既有鐵律)。這裡直接複用惰性快取(`fetcher.get_rates`),
    # 不強求前端事先靠別的頁面把匯率快取暖好。
    _rates_to_base: dict[str, dict[str, float]] = {}
    _fx_settings = get_settings()
    for _base in _needed_bases:
        _rates_to_base[_base] = {_base: 1.0}
        if _fx_settings.exchange_rate_proxy_enabled:
            try:
                _cache_row, _ = await _exchange_rate_fetcher.get_rates(db, _base)
            except Exception:  # noqa: BLE001 — 上游/代理任何錯誤都當這個 base 沒匯率,不讓帳戶列表整支 500
                _cache_row = None
            if _cache_row is not None and isinstance(_cache_row.payload_json, dict):
                for _q, _x in _cache_row.payload_json.items():
                    try:
                        _xf = float(_x)
                    except (TypeError, ValueError):
                        continue
                    if _xf > 0:
                        _rates_to_base[_base][_q.upper()] = 1.0 / _xf
        for _ov in db.execute(
            select(
                UserExchangeRateProjection.quote_currency,
                UserExchangeRateProjection.rate,
            ).where(
                UserExchangeRateProjection.user_id == target_user_id,
                UserExchangeRateProjection.base_currency == _base,
            )
        ).all():
            try:
                _r = float(_ov.rate)
            except (TypeError, ValueError):
                continue
            if _r > 0:
                _rates_to_base[_base][_ov.quote_currency.upper()] = _r

    for acc in all_accounts:
        if acc.account_type == "account_group":
            kids = children_by_parent.get(acc.id, [])
            acc.tx_count = (acc.tx_count or 0) + sum(k.tx_count or 0 for k in kids)
            group_cur = (acc.currency or "CNY").upper()
            base_rates = _rates_to_base.get(group_cur)
            income_add = 0.0
            expense_add = 0.0
            balance_add = 0.0
            incomplete = False
            for k in kids:
                kid_cur = (k.currency or "CNY").upper()
                if kid_cur == group_cur:
                    rate = 1.0
                elif base_rates is not None and kid_cur in base_rates:
                    rate = base_rates[kid_cur]
                else:
                    # 缺匯率:這筆子帳戶的金額整筆剔除,不裸加 1.0(對齊 _net() 鐵律)。
                    incomplete = True
                    continue
                income_add += (k.income_total or 0.0) * rate
                expense_add += (k.expense_total or 0.0) * rate
                # 納入總餘額(Phase 18)開關為 False 的子帳戶,不能把 balance 疊加回
                # 群組身上——前端「資產頁淨資產卡」是靠 include_in_total 過濾
                # rows 加總,子帳戶本身那一列會被濾掉,但群組列本身通常仍是
                # include_in_total=True,若這裡照樣把子帳戶餘額灌進群組 balance,
                # 使用者關掉子帳戶開關會變成完全沒作用(2026-08-14 生產環境回報)。
                # income_total/expense_total 維持不分 include_in_total 都疊加,
                # 因為那兩個欄位只是帳戶自身收支統計顯示用,不是總餘額計算。
                if k.include_in_total is not False:
                    balance_add += (k.balance or 0.0) * rate
            acc.income_total = (acc.income_total or 0.0) + income_add
            acc.expense_total = (acc.expense_total or 0.0) + expense_add
            acc.balance = (acc.balance or 0.0) + balance_add
            if incomplete:
                acc.balance_fx_incomplete = True

    # 「可繳款」提醒(§2.9 補強,2026-08-02):只對 billing-root(account_group,
    # 或沒有掛靠任何群組的獨立信用卡)且已設定 billing_day/payment_due_day 的
    # 帳戶算,數量通常是個位數,列表頁多算這幾次可接受。ledger_id 找法跟
    # `credit_card_reminders.py` 一样 —— 用任一笔子帳戶交易所在的 ledger
    # (account 是 user-global 实体,没有自己的 ledger_id)。
    now = datetime.now(timezone.utc)
    for acc in all_accounts:
        raw = raw_by_sync_id.get(acc.id)
        if raw is None or raw.billing_day is None or raw.payment_due_day is None:
            continue
        if not credit_card_billing.is_billing_root(raw):
            continue
        children = credit_card_billing.resolve_billing_children(db, account=raw)
        # 2026-08-03:group 自己現在也可能直接持有分期期數交易(§2.3 第四輪),
        # 這裡找 ledger 時併入 group 自己的 sync_id,不然一個還沒有任何子帳戶
        # 交易、只有 group 自己持有分期交易的群組會找不到 ledger 而被跳過。
        child_ids = credit_card_billing.billing_member_ids(raw, children)
        if not child_ids:
            continue
        billing_ledger_id = db.scalar(
            select(ReadTxProjection.ledger_id).where(
                ReadTxProjection.user_id == target_user_id,
                ReadTxProjection.account_sync_id.in_(child_ids),
            ).limit(1)
        )
        if billing_ledger_id is None:
            continue
        billing = credit_card_billing.compute_group_billing(
            db, ledger_id=billing_ledger_id, group=raw, children=children, now=now,
        )
        # 結帳日當天銀行才剛結帳,實際帳單要隔天才會出來,「可繳款」徽章要等
        # 隔天才顯示(2026-09-11 使用者反饋:結帳日 11 號當天就跳出來,應該
        # 12 號才對),不能結帳日當天就顯示,跟 credit_card_reminders.py 的
        # `statement_closed` 时机同一个判断。
        if billing["remaining_due"] > 0.01 and now.date() > billing["cycle_end"]:
            due = billing["due_date"]
            acc.billing_due_date = datetime(due.year, due.month, due.day, tzinfo=timezone.utc)
            acc.billing_remaining_due = round(billing["remaining_due"], 2)

    # 帳戶清單拖曳排序(2026-09-05):按 sort_order 排,None(舊資料/舊版 App)
    # fallback 排到最後,同 sort_order/都缺值時用名稱當 tiebreaker,再 paginate。
    all_accounts.sort(
        key=lambda a: (
            a.sort_order if a.sort_order is not None else 10**9,
            (a.name or "").lower(),
        )
    )
    return all_accounts[offset : offset + limit]


def _workspace_debt_currency_totals(
    db: Session, *, ledgers: list[Ledger],
) -> dict[str, dict[str, float]]:
    """跨帳本彙總未結清、未排除的欠款/應收,按帳本幣種分桶(debt 沒有自己的
    幣種欄位,沿用所屬帳本幣種,跟 accounts 一致)。供 `/workspace/debts` 卡片
    端點跟 `/workspace/net-worth-history` 趨勢端點共用同一份口徑,避免兩邊
    各自實作走岔。回傳 `{currency: {"receivable_total": x, "payable_total": y}}`,
    remaining_amount 口徑同 `/ledgers/{id}/debts`(principal - repaid,非負,
    已結清 <=0.01 的不計入),excluded_from_total=True 的不計入。"""
    if not ledgers:
        return {}
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_currency = {l.id: (l.currency or "CNY").upper() for l in ledgers}

    rows = db.execute(
        select(
            ReadDebtProjection.sync_id,
            ReadDebtProjection.ledger_id,
            ReadDebtProjection.direction,
            ReadDebtProjection.principal_amount,
            ReadDebtProjection.excluded_from_total,
        ).where(
            ReadDebtProjection.ledger_id.in_(ledger_internal_ids),
            ReadDebtProjection.closed_at.is_(None),
        )
    ).all()
    # 排除 excluded_from_total(跟 accounts.include_in_total 一樣用 Python 端
    # 判 `is not True`,兼容遷移前遺留的 NULL,不用 SQL == False)。
    rows = [r for r in rows if r.excluded_from_total is not True]
    if not rows:
        return {}

    debt_ids = [row.sync_id for row in rows]
    repaid_rows = db.execute(
        select(
            ReadTxProjection.debt_sync_id,
            func.coalesce(func.sum(func.abs(ReadTxProjection.amount)), 0.0),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.debt_sync_id.in_(debt_ids),
        ).group_by(ReadTxProjection.debt_sync_id)
    ).all()
    repaid_by_debt = {debt_sid: float(amt or 0.0) for debt_sid, amt in repaid_rows}

    # remaining_amount 口徑同 /ledgers/{id}/debts:principal - repaid,已結清
    # (<=0.01)的不計入,避免已還清部分重複算進總額。
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        remaining = max(
            float(row.principal_amount or 0.0) - repaid_by_debt.get(row.sync_id, 0.0), 0.0
        )
        if remaining <= 0.01:
            continue
        currency = ledger_currency.get(row.ledger_id, "CNY")
        bucket = totals.setdefault(currency, {"receivable_total": 0.0, "payable_total": 0.0})
        if row.direction == "receivable":
            bucket["receivable_total"] += remaining
        else:
            bucket["payable_total"] += remaining
    return totals


@router.get("/workspace/debts", response_model=list[WorkspaceDebtCurrencyTotalOut])
async def list_workspace_debt_totals(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceDebtCurrencyTotalOut]:
    """淨資產卡片用:跨帳本彙總未結清、未排除的欠款/應收,按帳本幣種分桶,
    讓前端能跟 /workspace/accounts 一樣折算進「資產/負債」總額(§淨資產卡
    此前完全沒查過 debts 表,导致 app 端把應收算進資產、這裡沒算,兩邊對不上
    帳——見 mobile LocalRepository.getNetWorthBreakdown 對應口徑)。"""
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    totals = _workspace_debt_currency_totals(db, ledgers=ledgers)
    return [
        WorkspaceDebtCurrencyTotalOut(
            currency=currency,
            receivable_total=vals["receivable_total"],
            payable_total=vals["payable_total"],
        )
        for currency, vals in totals.items()
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

    # --- 1. 从 snapshot 聚合分类（手机同步写入的数据，已排除软删账本 issue #31） ---
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers}
    change_id_by_ledger = {l.id: _get_latest_change_id(db, ledger_id=l.id) for l in ledgers}

    # user-global 重构:category 是 per-user 表。target_user 跟 accounts 一致。
    target_user_id = user_id if (is_admin and user_id) else current_user.id
    target_email = db.scalar(select(User.email).where(User.id == target_user_id))

    # 用户级 last_change_id:该用户最近的 user-scope SyncChange(category)。
    cat_last_change_id = int(db.scalar(
        select(func.coalesce(func.max(SyncChange.change_id), 0))
        .where(
            SyncChange.user_id == target_user_id,
            SyncChange.scope == "user",
            SyncChange.entity_type == "category",
        )
    ) or 0)

    cat_query = select(UserCategoryProjection).where(
        UserCategoryProjection.user_id == target_user_id
    )
    if q:
        cat_query = cat_query.where(UserCategoryProjection.name.ilike(f"%{q}%"))

    # tx_count 聚合:按 category_sync_id 数 ReadTxProjection 行。tx 仍 per-ledger,
    # 限定在 caller 可见 ledger 范围内。
    from collections import defaultdict
    tx_count_by_sync_id: dict[str, int] = defaultdict(int)
    if ledger_internal_ids:
        tx_count_rows = db.execute(
            select(
                ReadTxProjection.category_sync_id,
                func.count(),
            )
            .where(
                ReadTxProjection.ledger_id.in_(ledger_internal_ids),
                ReadTxProjection.category_sync_id.is_not(None),
            )
            .group_by(ReadTxProjection.category_sync_id)
        ).all()
        for row in tx_count_rows:
            sid = row[0]
            if sid:
                tx_count_by_sync_id[sid] += int(row[1] or 0)
        # 拆帳(§2.4):拆帳交易的父行 category_sync_id 是 NULL,不会进上面那个
        # group by;分到某分类的笔数要另外从 split 明细表数(同一笔交易拆到
        # 同一分类两次极少见,这里按 split 行数计,不做去重)。
        split_count_rows = db.execute(
            select(
                ReadTxSplitProjection.category_sync_id,
                func.count(),
            )
            .where(
                ReadTxSplitProjection.ledger_id.in_(ledger_internal_ids),
                ReadTxSplitProjection.category_sync_id.is_not(None),
            )
            .group_by(ReadTxSplitProjection.category_sync_id)
        ).all()
        for row in split_count_rows:
            sid = row[0]
            if sid:
                tx_count_by_sync_id[sid] += int(row[1] or 0)

    all_categories: list[WorkspaceCategoryOut] = []
    for cat in db.scalars(cat_query).all():
        name = (cat.name or "").strip()
        if not name:
            continue
        kind = cat.kind or "expense"
        sync_id = cat.sync_id
        all_categories.append(
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
                color=cat.color,
                last_change_id=cat_last_change_id,
                ledger_id="",
                ledger_name="",
                created_by_user_id=target_user_id,
                created_by_email=target_email,
                tx_count=tx_count_by_sync_id.get(sync_id, 0) if sync_id else 0,
            )
        )

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

    # --- 1. 从 snapshot 聚合标签（手机同步写入的数据，已排除软删账本 issue #31） ---
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )

    all_tags: list[WorkspaceTagOut] = []
    # user-global 重构:tag 是 per-user。即便用户无 ledger,标签仍可存在(协议层
    # 允许);但 tx 聚合需要 ledger 才有意义,所以 stats 阶段空集即可。
    ledger_internal_ids = [l.id for l in ledgers]

    target_user_id = user_id if (is_admin and user_id) else current_user.id
    target_email = db.scalar(select(User.email).where(User.id == target_user_id))

    tag_last_change_id = int(db.scalar(
        select(func.coalesce(func.max(SyncChange.change_id), 0))
        .where(
            SyncChange.user_id == target_user_id,
            SyncChange.scope == "user",
            SyncChange.entity_type == "tag",
        )
    ) or 0)

    tag_query = select(UserTagProjection).where(
        UserTagProjection.user_id == target_user_id
    )
    if q:
        tag_query = tag_query.where(UserTagProjection.name.ilike(f"%{q}%"))

    for tag in db.scalars(tag_query).all():
        name = (tag.name or "").strip()
        if not name:
            continue
        all_tags.append(
            WorkspaceTagOut(
                id=tag.sync_id,
                name=name,
                color=tag.color,
                last_change_id=tag_last_change_id,
                ledger_id="",
                ledger_name="",
                created_by_user_id=target_user_id,
                created_by_email=target_email,
            )
        )

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
    # 账本维度口径:折本位币(native ?? amount)+ 排除「不计收支」标记笔,
    # 与 analytics / _projection_totals / App getTagStats 一致(审查发现:
    # 此前此处两个口径都缺,多币种/标记场景下标签合计与分析页对不上)。
    tx_rows = db.execute(
        select(
            ReadTxProjection.tx_type,
            func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount),
            ReadTxProjection.tag_sync_ids_json,
            ReadTxProjection.tags_csv,
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.exclude_from_stats == sa_false(),
        )
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
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
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
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    natural_month: bool = Query(default=False),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceAnalyticsOut:
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    # 单账本视图用该账本的自定义起始日;多账本聚合维持自然月(各账本周期可能
    # 不同无法对齐)。natural_month=true 强制自然月(日历网格等按公历的消费方,D6)。
    month_start_day = (
        1
        if natural_month
        else ((ledgers[0].month_start_day or 1) if len(ledgers) == 1 else 1)
    )
    start_at, end_at, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,
        month_start_day=month_start_day,
    )
    ledger_internal_ids = [l.id for l in ledgers]

    transaction_count = 0
    income_total = 0.0
    expense_total = 0.0
    series_map: dict[str, dict[str, float]] = {}
    category_map: dict[str, dict[str, float]] = {}
    # bucket → category → expense 总额。仅 scope=year anomaly 归因用,其它 scope
    # 也写入(开销可忽略),只在 endpoint 末尾按 scope 决定是否用。
    category_by_bucket: dict[str, dict[str, float]] = {}
    distinct_days_set: set[str] = set()
    first_tx_at: datetime | None = None
    last_tx_at: datetime | None = None

    if ledger_internal_ids:
        tx_query = select(
            ReadTxProjection.ledger_id,
            ReadTxProjection.tx_type,
            # 账本维度折本位币口径(0018):amount 是原币,下面按 native_amount
            # 是否存在算折算比例(拆帳的每个 split 金额也要按同一比例缩放)。
            ReadTxProjection.amount,
            ReadTxProjection.native_amount,
            ReadTxProjection.happened_at,
            ReadTxProjection.category_name,
            # 退款(§2.6/§2.12.3):非空 = 这笔交易是对另一笔交易的退款(income
            # 退 expense,或 expense 退 income),netting 逻辑见下方循环。
            ReadTxProjection.refund_of_sync_id,
            # 拆帳(§2.4):has_splits=True 时按 splits_json 展开成多个分类
            # "腿"分别累加,而不是整笔归到(此时已是 NULL 的)category_name。
            ReadTxProjection.has_splits,
            ReadTxProjection.splits_json,
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            # exclude_from_stats=True 的交易不计入收支统计(D1);该端点所有
            # 数字(income/expense 汇总、series、分类排行、anomaly)都源自这一
            # 查询,故在此一处过滤即覆盖全部收支口径。余额/净值口径在
            # workspace_net_worth_history 端点,不受此过滤影响。
            ReadTxProjection.exclude_from_stats == sa_false(),
        )
        if start_at is not None:
            tx_query = tx_query.where(ReadTxProjection.happened_at >= start_at)
        if end_at is not None:
            tx_query = tx_query.where(ReadTxProjection.happened_at < end_at)

        # 用本地时区折算的日期算"记账天数",跟 _bucket_key 同步;否则东半球用户在
        # 本地 0-8 点记的笔会被算到前一天的 distinct_days,跟日历视图不一致。
        from datetime import timedelta as _td

        tx_rows = db.execute(tx_query).all()
        refund_targets = _load_refund_targets(
            db, ledger_ids=ledger_internal_ids,
            refund_ids={r[6] for r in tx_rows if r[6]},
        )
        for (lg_id, tx_type_val, raw_amount, native_amount_val, happened_at_raw, cat_name,
             refund_of_id, has_splits, splits_json) in tx_rows:
            if happened_at_raw is None:
                continue
            happened_at = _to_utc(happened_at_raw)
            transaction_count += 1
            local_for_day = happened_at + _td(minutes=tz_offset_minutes)
            distinct_days_set.add(local_for_day.strftime("%Y-%m-%d"))
            if first_tx_at is None or happened_at < first_tx_at:
                first_tx_at = happened_at
            if last_tx_at is None or happened_at > last_tx_at:
                last_tx_at = happened_at
            bucket = _bucket_key(scope, happened_at, tz_offset_minutes, month_start_day)
            slot = series_map.setdefault(bucket, {"expense": 0.0, "income": 0.0})

            # 拆帳展開、退款 netting(扣回原交易分類、算在退款自己的 bucket)
            # 見 [_stat_legs]。退款 leg 已是反方向的負值,直接累加即可。
            for flow, leg_cat, _sid, leg_amt in _stat_legs(
                tx_type=tx_type_val, raw_amount=raw_amount, native_amount=native_amount_val,
                category_name=cat_name, category_sync_id=None, refund_of_sync_id=refund_of_id,
                has_splits=has_splits, splits_json=splits_json,
                refund_target=(refund_targets.get((lg_id, refund_of_id))
                               if refund_of_id else None),
            ):
                category_slot = category_map.setdefault(
                    leg_cat, {"income": 0.0, "expense": 0.0, "count": 0.0})
                if flow is None:
                    continue
                category_slot["count"] += 1.0
                if flow == "income":
                    income_total += leg_amt
                    slot["income"] += leg_amt
                    category_slot["income"] += leg_amt
                else:
                    expense_total += leg_amt
                    slot["expense"] += leg_amt
                    category_slot["expense"] += leg_amt
                    # 同步累加 per-bucket category → anomaly 归因输入
                    bucket_cat = category_by_bucket.setdefault(bucket, {})
                    bucket_cat[leg_cat] = bucket_cat.get(leg_cat, 0.0) + leg_amt

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

    # 异常月份归因 — 仅 scope=year 算(month/all 没意义,month 只 1 个 bucket,
    # all 跨年 baseline 抖动太大)。详见
    # .docs/dashboard-anomaly-budget/plan.md §2.1。
    anomaly_months: list[WorkspaceAnalyticsAnomalyMonthOut] = []
    if scope == "year":
        anomaly_months = _compute_anomaly_months(series, category_by_bucket)

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
        anomaly_months=anomaly_months,
        range=WorkspaceAnalyticsRangeOut(
            scope=scope,
            metric=metric,
            period=normalized_period,
            start_at=start_at,
            end_at=end_at - timedelta(seconds=1) if end_at is not None else None,
        ),
    )


# ---------------------------------------------------------------------------
# 异常月份归因(scope=year)
# ---------------------------------------------------------------------------

# 异常判定阈值。同时满足两条才算异常:
#   1. expense > baseline × 1.2 — 高于基线 20%+
#   2. expense - baseline > ¥200 — 绝对差避免低消费月的"高 N%"假阳性
# 详见 .docs/dashboard-anomaly-budget/plan.md §2.1。
_ANOMALY_DEVIATION_MULT = 1.2
_ANOMALY_DEVIATION_ABS = 200.0
# 最少 3 个已发生月份才算 baseline(中位数,1-2 个月样本太小不稳)
_ANOMALY_MIN_MONTHS = 3
# 每个异常月份最多归因到 top N 个分类(避免列表过长)
_ANOMALY_TOP_ATTRIBUTIONS = 2


def _compute_anomaly_months(
    series: list[WorkspaceAnalyticsSeriesItemOut],
    category_by_bucket: dict[str, dict[str, float]],
) -> list[WorkspaceAnalyticsAnomalyMonthOut]:
    """从年度 series + per-bucket category 数据算异常月份 + 归因。

    算法:
      1. baseline = median(已发生月份的 expense),已发生月份 < 3 时返回空
      2. 异常判定:expense > baseline × 1.2 AND expense - baseline > ¥200
      3. 归因:对该月每个 category,算 (该月 category 总额) - (其他月份该
         category 的中位数),取 diff 最大的 top 2 作主因
      4. category 在其他月份从来没出现过(median_others=0)→ 算"本月独有",
         multiplier 返回 None
    """
    # 已发生月份(expense > 0)— 没记账的月不算 baseline
    occurred = [s for s in series if s.expense > 0]
    if len(occurred) < _ANOMALY_MIN_MONTHS:
        return []

    baseline = _stats.median(s.expense for s in occurred)

    out: list[WorkspaceAnalyticsAnomalyMonthOut] = []
    other_buckets_cache: dict[str, list[str]] = {}

    def _others_for(bucket: str) -> list[str]:
        cached = other_buckets_cache.get(bucket)
        if cached is not None:
            return cached
        result = [o.bucket for o in occurred if o.bucket != bucket]
        other_buckets_cache[bucket] = result
        return result

    for s in occurred:
        if s.expense <= baseline * _ANOMALY_DEVIATION_MULT:
            continue
        if s.expense - baseline <= _ANOMALY_DEVIATION_ABS:
            continue

        # 归因:每个 category 算 diff
        attributions_raw: list[
            tuple[float, WorkspaceAnalyticsAnomalyAttributionOut]
        ] = []
        this_month_cats = category_by_bucket.get(s.bucket, {})
        other_bucket_keys = _others_for(s.bucket)
        for cat_name, cat_amount in this_month_cats.items():
            others = [
                category_by_bucket.get(b, {}).get(cat_name, 0.0)
                for b in other_bucket_keys
            ]
            median_others = _stats.median(others) if others else 0.0
            diff = cat_amount - median_others
            if diff <= 0:
                # 该 category 不算异常因素(本月没比平时多)
                continue
            multiplier = (
                cat_amount / median_others if median_others > 0 else None
            )
            attributions_raw.append((
                diff,
                WorkspaceAnalyticsAnomalyAttributionOut(
                    category_name=cat_name,
                    amount=cat_amount,
                    median_others=median_others,
                    multiplier=multiplier,
                ),
            ))
        attributions_raw.sort(key=lambda x: -x[0])
        top_attributions = [a for _, a in attributions_raw[:_ANOMALY_TOP_ATTRIBUTIONS]]

        deviation_pct = (
            (s.expense - baseline) / baseline if baseline > 0 else 0.0
        )
        out.append(WorkspaceAnalyticsAnomalyMonthOut(
            bucket=s.bucket,
            expense=s.expense,
            baseline=baseline,
            deviation_pct=deviation_pct,
            top_attributions=top_attributions,
        ))

    # 按超出 baseline 的绝对值降序(最异常的排前面)
    out.sort(key=lambda a: -(a.expense - a.baseline))
    return out


# ---------------------------------------------------------------------------
# 比較報表(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5)—— 複用 `_analytics_range`
# 的區間計算 + `workspace_analytics` 核心迴圈同款的 refund netting/拆帳展開
# 規則(精簡版:只算 income/expense 總額 + 分類排行,不需要 series/anomaly/
# distinct_days,所以沒有直接呼叫 workspace_analytics 端點函式本身,而是把
# 共用邏輯抽成這個獨立 helper)。
# ---------------------------------------------------------------------------


class _RefundTarget(NamedTuple):
    """退款單指向的原交易(統計歸屬用),見 [_load_refund_targets]。"""
    category_name: str | None
    category_sync_id: str | None
    has_splits: bool | None
    splits_json: str | None
    exclude_from_stats: bool
    project_sync_id: str | None


def _load_refund_targets(
    db: Session, *, ledger_ids: list[str], refund_ids: set[str],
) -> dict[tuple[str, str], _RefundTarget]:
    """批次查退款單指向的原交易(不限日期——退款常常跟原交易不在同一期),
    key = `(ledger_id, sync_id)`。"""
    out: dict[tuple[str, str], _RefundTarget] = {}
    ids = sorted(i for i in refund_ids if i)
    if not ids or not ledger_ids:
        return out
    for i in range(0, len(ids), 500):
        for row in db.execute(
            select(
                ReadTxProjection.ledger_id,
                ReadTxProjection.sync_id,
                ReadTxProjection.category_name,
                ReadTxProjection.category_sync_id,
                ReadTxProjection.has_splits,
                ReadTxProjection.splits_json,
                ReadTxProjection.exclude_from_stats,
                ReadTxProjection.project_sync_id,
            ).where(
                ReadTxProjection.ledger_id.in_(ledger_ids),
                ReadTxProjection.sync_id.in_(ids[i:i + 500]),
            )
        ).all():
            out[(row[0], row[1])] = _RefundTarget(
                category_name=row[2], category_sync_id=row[3], has_splits=row[4],
                splits_json=row[5], exclude_from_stats=bool(row[6]), project_sync_id=row[7],
            )
    return out


def _split_entries(splits_json: str | None) -> list[tuple[str, str | None, float]]:
    """拆帳 JSON → `(category_name, category_sync_id, 原幣金額)` 列表。"""
    if not splits_json:
        return []
    try:
        raw_splits = json.loads(splits_json)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(raw_splits, list):
        return []
    out: list[tuple[str, str | None, float]] = []
    for entry in raw_splits:
        if not isinstance(entry, dict):
            continue
        leg_cat = (entry.get("categoryName") or "").strip() or "Uncategorized"
        leg_sid = entry.get("categoryId") or entry.get("categorySyncId")
        out.append((leg_cat, leg_sid if isinstance(leg_sid, str) else None,
                    float(entry.get("amount") or 0.0)))
    return out


def _stat_legs(
    *,
    tx_type: str | None,
    raw_amount: float | None,
    native_amount: float | None,
    category_name: str | None,
    category_sync_id: str | None,
    refund_of_sync_id: str | None,
    has_splits: bool | None,
    splits_json: str | None,
    refund_target: _RefundTarget | None = None,
) -> list[tuple[str | None, str, str | None, float]]:
    """把一筆交易展開成統計用的 legs:`(flow, category_name, category_sync_id,
    signed_amount)`。

    口徑:本位幣(native_amount ?? amount)、拆帳按明細展開並按折算比例縮放、
    退款 netting —— income 退款記成 `flow="expense"` 的負值、expense 退款
    (含自動產生的回饋金沖銷)記成 `flow="income"` 的負值。非收支類型(轉帳
    等)的 leg `flow=None`、金額 0(仍回傳,讓呼叫端能保留「分類有出現過」的
    語意)。

    退款的分類歸屬(2026-09-25,跟 App `utils/refund_netting.dart` 同口徑):
    有 [refund_target](原交易)就扣回原交易的分類——原交易拆帳就按明細比例
    分攤;原交易本身不計入統計時退款也不計入(回傳空列表);查不到原交易才
    用退款單自己的分類。退款仍算在退款單自己的日期。

    `workspace_analytics`、比較報表(`/workspace/comparison`)與比較矩陣
    (`/workspace/comparison-matrix`)共用這一份,數字才對得上。
    """
    raw_amt = float(raw_amount or 0.0)
    coalesced_amt = float(native_amount) if native_amount is not None else raw_amt
    is_refund = refund_of_sync_id is not None

    legs: list[tuple[str, str | None, float]] = []
    if is_refund and refund_target is not None:
        if refund_target.exclude_from_stats:
            return []
        target_splits = _split_entries(refund_target.splits_json) if refund_target.has_splits else []
        base = sum(a for _, _, a in target_splits)
        if target_splits and base:
            legs = [(c, sid, coalesced_amt * a / base) for c, sid, a in target_splits]
        else:
            legs = [(
                "Uncategorized" if refund_target.has_splits
                else ((refund_target.category_name or "").strip() or "Uncategorized"),
                None if refund_target.has_splits else refund_target.category_sync_id,
                coalesced_amt,
            )]
    elif has_splits:
        scale = (coalesced_amt / raw_amt) if raw_amt else 1.0
        legs = [(c, sid, a * scale) for c, sid, a in _split_entries(splits_json)]
    if not legs:
        legs = [((category_name or "").strip() or "Uncategorized", category_sync_id, coalesced_amt)]

    out: list[tuple[str | None, str, str | None, float]] = []
    for leg_cat, leg_sid, leg_amt in legs:
        if tx_type == "income" and is_refund:
            out.append(("expense", leg_cat, leg_sid, -leg_amt))
        elif tx_type == "expense" and is_refund:
            out.append(("income", leg_cat, leg_sid, -leg_amt))
        elif tx_type in ("income", "expense"):
            out.append((tx_type, leg_cat, leg_sid, leg_amt))
        else:
            out.append((None, leg_cat, leg_sid, 0.0))
    return out


def _comparison_period_totals(
    db: Session, *, ledger_internal_ids: list[str], start_at: datetime | None, end_at: datetime | None,
) -> tuple[float, float, dict[str, dict[str, float]]]:
    """回傳 (income_total, expense_total, category_map)。category_map 的
    value 是 `{"income": x, "expense": y}`(跟 `workspace_analytics` 的
    `category_map` 同款結構,只是不需要 count)。口徑見 [_stat_legs]。"""
    income_total = 0.0
    expense_total = 0.0
    category_map: dict[str, dict[str, float]] = {}
    if not ledger_internal_ids:
        return income_total, expense_total, category_map

    tx_query = select(
        ReadTxProjection.ledger_id,
        ReadTxProjection.tx_type,
        ReadTxProjection.amount,
        ReadTxProjection.native_amount,
        ReadTxProjection.category_name,
        ReadTxProjection.refund_of_sync_id,
        ReadTxProjection.has_splits,
        ReadTxProjection.splits_json,
    ).where(
        ReadTxProjection.ledger_id.in_(ledger_internal_ids),
        ReadTxProjection.exclude_from_stats == sa_false(),
    )
    if start_at is not None:
        tx_query = tx_query.where(ReadTxProjection.happened_at >= start_at)
    if end_at is not None:
        tx_query = tx_query.where(ReadTxProjection.happened_at < end_at)

    rows = db.execute(tx_query).all()
    targets = _load_refund_targets(
        db, ledger_ids=ledger_internal_ids, refund_ids={r[5] for r in rows if r[5]},
    )
    for (lg_id, tx_type_val, raw_amount, native_amount_val, cat_name,
         refund_of_id, has_splits, splits_json) in rows:
        for flow, leg_cat, _sid, amt in _stat_legs(
            tx_type=tx_type_val, raw_amount=raw_amount, native_amount=native_amount_val,
            category_name=cat_name, category_sync_id=None, refund_of_sync_id=refund_of_id,
            has_splits=has_splits, splits_json=splits_json,
            refund_target=targets.get((lg_id, refund_of_id)) if refund_of_id else None,
        ):
            category_slot = category_map.setdefault(leg_cat, {"income": 0.0, "expense": 0.0})
            if flow == "income":
                income_total += amt
                category_slot["income"] += amt
            elif flow == "expense":
                expense_total += amt
                category_slot["expense"] += amt
    return income_total, expense_total, category_map


def _comparison_metric(current: float, previous: float) -> ComparisonReportMetricOut:
    diff = round(current - previous, 2)
    diff_pct = round((current - previous) / abs(previous) * 100, 2) if previous else None
    return ComparisonReportMetricOut(
        current=round(current, 2), previous=round(previous, 2), diff=diff, diff_pct=diff_pct,
    )


@router.get("/workspace/comparison", response_model=ComparisonReportOut)
def comparison_report(
    scope: Literal["month", "year"] = Query(default="month"),
    period: str | None = Query(default=None),
    offset: int = Query(default=1, ge=1, le=120),
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    natural_month: bool = Query(default=False),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComparisonReportOut:
    """比較報表(§2.10 Phase 5):「當期」跟「比較期」兩個區間各自的
    income/expense/balance + 分類排行對比。`period` 語意跟 `/workspace/
    analytics` 相同(不傳 = 目前所在的那個 scope 週期);`offset` 決定比較期
    往前推幾個週期 —— `scope=month` 時 `offset=1` 是上個月(環比),
    `offset=12` 是去年同月(同比);`scope=year` 時 `offset=1` 是去年。
    複用 `_analytics_range` 算區間邊界,跟 analytics 端點的月份起始日/自然月
    語意完全一致。"""
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    month_start_day = (
        1
        if natural_month
        else ((ledgers[0].month_start_day or 1) if len(ledgers) == 1 else 1)
    )
    current_start, current_end, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,
        month_start_day=month_start_day,
    )
    if current_start is None or current_end is None:
        raise HTTPException(status_code=400, detail="comparison report requires a concrete period")

    # 比較期邊界:對「當期週期標籤」(normalized_period,本地日曆的
    # "YYYY-MM"/"YYYY")做整數年月位移,再重新透過 `_analytics_range` 算出
    # UTC 邊界 —— 不能直接對 current_start/current_end(已轉 UTC 的 instant)
    # 做 dt.replace(month=...),那是本地日期在轉 UTC 後可能已經跨到前一天
    # (正時區偏移時),此時 dt.day 對「位移後的目標月」可能無效(例如 31 號
    # 位移到只有 30 天的月份)直接炸 ValueError。
    def _shift_period_label(period: str, offset: int) -> str:
        if scope == "year":
            return str(int(period) - offset)
        year_part, month_part = period.split("-", 1)
        total_months = int(year_part) * 12 + (int(month_part) - 1) - offset
        year, month0 = divmod(total_months, 12)
        return f"{year:04d}-{month0 + 1:02d}"

    previous_period_label = _shift_period_label(normalized_period, offset)
    previous_start, previous_end, _ = _analytics_range(
        scope=scope, period=previous_period_label, tz_offset_minutes=tz_offset_minutes,
        month_start_day=month_start_day,
    )

    ledger_internal_ids = [l.id for l in ledgers]
    cur_income, cur_expense, cur_cat = _comparison_period_totals(
        db, ledger_internal_ids=ledger_internal_ids, start_at=current_start, end_at=current_end,
    )
    prev_income, prev_expense, prev_cat = _comparison_period_totals(
        db, ledger_internal_ids=ledger_internal_ids, start_at=previous_start, end_at=previous_end,
    )

    breakdown: list[ComparisonCategoryBreakdownItemOut] = []
    for cat_name in set(cur_cat) | set(prev_cat):
        cur_slot = cur_cat.get(cat_name, {"income": 0.0, "expense": 0.0})
        prev_slot = prev_cat.get(cat_name, {"income": 0.0, "expense": 0.0})
        # 分類排行以 expense 為主(比較報表最常見的用法是看支出分類漲跌);
        # 純收入分類(expense 兩期都是 0)一律歸類成 kind=income,net 用收入。
        if cur_slot["expense"] or prev_slot["expense"]:
            kind = "expense"
            cur_amt, prev_amt = cur_slot["expense"], prev_slot["expense"]
        else:
            kind = "income"
            cur_amt, prev_amt = cur_slot["income"], prev_slot["income"]
        breakdown.append(ComparisonCategoryBreakdownItemOut(
            category_name=cat_name,
            category_kind=kind,
            current=round(cur_amt, 2),
            previous=round(prev_amt, 2),
            diff=round(cur_amt - prev_amt, 2),
        ))
    breakdown.sort(key=lambda item: -abs(item.diff))

    return ComparisonReportOut(
        scope=scope,
        offset=offset,
        current_period_start=current_start,
        current_period_end=current_end,
        previous_period_start=previous_start,
        previous_period_end=previous_end,
        income=_comparison_metric(cur_income, prev_income),
        expense=_comparison_metric(cur_expense, prev_expense),
        balance=_comparison_metric(cur_income - cur_expense, prev_income - prev_expense),
        category_breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# 比較矩陣(對齊 doc.moze.app/analysis/comparison-report):列 = 月份、欄 =
# 比較項目(記錄類型 / 支出類別 / 收入類別 / 子類別 / 專案 / 帳戶分組),
# 右側每月合計與月增率、底部各欄合計與平均。口徑同 `/workspace/comparison`
# (共用 `_stat_legs`:本位幣、拆帳展開、退款 netting、exclude_from_stats)。
# ---------------------------------------------------------------------------

ComparisonMatrixDimension = Literal[
    "record_type",
    "expense_category",
    "income_category",
    "expense_subcategory",
    "income_subcategory",
    "project",
    "account_group",
]

_MATRIX_NONE_KEY = "__none__"
_MATRIX_MAX_MONTHS = 120


class ComparisonMatrixColumnOut(BaseModel):
    key: str
    label: str
    # 子類別維度時是一級分類名稱,其餘為 None
    parent_label: str | None = None


class ComparisonMatrixRowOut(BaseModel):
    month: str
    start: datetime
    end: datetime
    values: dict[str, float]
    total: float
    # 相對上一列(上一個月)的增減百分比;上一列為 0 或第一列時為 None
    mom_pct: float | None = None


class ComparisonMatrixOut(BaseModel):
    dimension: str
    kind: str
    columns: list[ComparisonMatrixColumnOut]
    rows: list[ComparisonMatrixRowOut]
    column_totals: dict[str, float]
    column_averages: dict[str, float]
    grand_total: float


class ComparisonMatrixCellTxOut(BaseModel):
    sync_id: str
    ledger_id: str
    happened_at: datetime
    tx_type: str
    # 這筆交易落在該格的金額(拆帳時只算對應明細;退款為負值)
    amount: float
    category_name: str | None = None
    account_name: str | None = None
    note: str | None = None
    merchant: str | None = None
    is_refund: bool = False


class ComparisonMatrixCellOut(BaseModel):
    month: str
    column_key: str
    total: float
    items: list[ComparisonMatrixCellTxOut]


def _matrix_month_labels(start: str, end: str) -> list[str]:
    def parse(label: str) -> int:
        try:
            y, m = label.split("-", 1)
            yi, mi = int(y), int(m)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Invalid month label") from exc
        if not 1 <= mi <= 12:
            raise HTTPException(status_code=400, detail="Invalid month label")
        return yi * 12 + mi - 1

    a, b = parse(start), parse(end)
    if b < a:
        a, b = b, a
    if b - a + 1 > _MATRIX_MAX_MONTHS:
        raise HTTPException(status_code=400, detail=f"At most {_MATRIX_MAX_MONTHS} months")
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(a, b + 1)]


def _matrix_legs(
    db: Session,
    *,
    ledgers: list,
    months: list[str],
    dimension: str,
    kind: str,
    tz_offset_minutes: int,
    month_start_day: int,
):
    """產生矩陣用的 legs:`(month_label, column_key, column_label, parent_label,
    amount, tx_row, leg_category_name)`。矩陣端點與格子下鑽端點共用,確保
    兩邊分組規則一致。"""
    start_at, _, _ = _analytics_range(
        scope="month", period=months[0], tz_offset_minutes=tz_offset_minutes,
        month_start_day=month_start_day,
    )
    _, end_at, _ = _analytics_range(
        scope="month", period=months[-1], tz_offset_minutes=tz_offset_minutes,
        month_start_day=month_start_day,
    )
    ledger_ids = [lg.id for lg in ledgers]
    owner_ids = {lg.user_id for lg in ledgers}
    ext_by_internal = {lg.id: lg.external_id for lg in ledgers}

    rows = db.execute(
        select(
            ReadTxProjection.sync_id,
            ReadTxProjection.ledger_id,
            ReadTxProjection.user_id,
            ReadTxProjection.tx_type,
            ReadTxProjection.amount,
            ReadTxProjection.native_amount,
            ReadTxProjection.happened_at,
            ReadTxProjection.category_name,
            ReadTxProjection.category_sync_id,
            ReadTxProjection.refund_of_sync_id,
            ReadTxProjection.has_splits,
            ReadTxProjection.splits_json,
            ReadTxProjection.project_sync_id,
            ReadTxProjection.account_sync_id,
            ReadTxProjection.account_name,
            ReadTxProjection.note,
            ReadTxProjection.merchant,
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_ids),
            ReadTxProjection.exclude_from_stats == sa_false(),
            ReadTxProjection.happened_at >= start_at,
            ReadTxProjection.happened_at < end_at,
        )
    ).all()

    # ---- 分類階層(user-global,依帳本擁有者) ----
    cats = db.scalars(
        select(UserCategoryProjection).where(UserCategoryProjection.user_id.in_(owner_ids))
    ).all() if dimension.endswith("category") or dimension.endswith("subcategory") else []
    cat_by_sid = {(c.user_id, c.sync_id): c for c in cats}
    cat_by_name: dict[tuple[str, str, str], UserCategoryProjection] = {}
    for c in cats:
        if c.name:
            cat_by_name.setdefault((c.user_id, c.kind or "", c.name), c)

    def top_and_child(user_id: str, flow: str, name: str, sid: str | None) -> tuple[str, str | None]:
        c = (cat_by_sid.get((user_id, sid)) if sid else None) or cat_by_name.get((user_id, flow, name))
        if c is None:
            return name, None
        if (c.level or 1) >= 2 and (c.parent_sync_id or c.parent_name):
            parent = cat_by_sid.get((user_id, c.parent_sync_id)) if c.parent_sync_id else None
            pname = (parent.name if parent is not None else None) or c.parent_name or name
            return pname, c.name or name
        return c.name or name, None

    # ---- 專案 / 帳戶分組 ----
    project_names: dict[tuple[str, str], str] = {}
    if dimension == "project":
        for p in db.scalars(
            select(ReadProjectProjection).where(ReadProjectProjection.ledger_id.in_(ledger_ids))
        ).all():
            project_names[(p.ledger_id, p.sync_id)] = p.name or ""
    accounts: dict[tuple[str, str], UserAccountProjection] = {}
    if dimension == "account_group":
        for a in db.scalars(
            select(UserAccountProjection).where(UserAccountProjection.user_id.in_(owner_ids))
        ).all():
            accounts[(a.user_id, a.sync_id)] = a

    targets = _load_refund_targets(
        db, ledger_ids=ledger_ids, refund_ids={r.refund_of_sync_id for r in rows if r.refund_of_sync_id},
    )
    for row in rows:
        (sync_id, ledger_id, user_id, tx_type, amount, native, happened_at, cat_name,
         cat_sid, refund_of, has_splits, splits_json, project_sid, account_sid,
         _account_name, _note, _merchant) = row
        target = targets.get((ledger_id, refund_of)) if refund_of else None
        if target is not None and not project_sid:
            # 退款單沒掛專案就沿用原交易的(同 App 統計報表)
            project_sid = target.project_sync_id
        month = _bucket_key("year", happened_at, tz_offset_minutes, month_start_day)
        for flow, leg_cat, leg_sid, amt in _stat_legs(
            tx_type=tx_type, raw_amount=amount, native_amount=native,
            category_name=cat_name, category_sync_id=cat_sid, refund_of_sync_id=refund_of,
            has_splits=has_splits, splits_json=splits_json, refund_target=target,
        ):
            if flow is None:
                continue
            if dimension == "record_type":
                yield month, flow, flow, None, amt, row, leg_cat, ext_by_internal[ledger_id]
                continue
            if dimension in ("expense_category", "income_category",
                             "expense_subcategory", "income_subcategory"):
                want = "expense" if dimension.startswith("expense") else "income"
                if flow != want:
                    continue
                top, child = top_and_child(user_id, want, leg_cat, leg_sid)
                if dimension.endswith("subcategory") and child is not None:
                    yield (month, f"{top}›{child}", child, top, amt, row, leg_cat,
                           ext_by_internal[ledger_id])
                else:
                    yield month, top, top, None, amt, row, leg_cat, ext_by_internal[ledger_id]
                continue
            if flow != kind:
                continue
            if dimension == "project":
                if project_sid:
                    label = project_names.get((ledger_id, project_sid), "")
                    yield month, project_sid, label, None, amt, row, leg_cat, ext_by_internal[ledger_id]
                else:
                    yield month, _MATRIX_NONE_KEY, "", None, amt, row, leg_cat, ext_by_internal[ledger_id]
                continue
            if dimension == "account_group":
                acc = accounts.get((user_id, account_sid)) if account_sid else None
                group_sid: str | None = None
                if acc is not None:
                    if acc.parent_account_id:
                        group_sid = acc.parent_account_id
                    elif acc.account_type == "account_group":
                        group_sid = acc.sync_id
                if group_sid is None:
                    yield month, _MATRIX_NONE_KEY, "", None, amt, row, leg_cat, ext_by_internal[ledger_id]
                else:
                    g = accounts.get((user_id, group_sid))
                    yield (month, group_sid, (g.name if g is not None else "") or "", None, amt, row,
                           leg_cat, ext_by_internal[ledger_id])


def _matrix_context(
    db: Session, *, current_user: User, ledger_id: str | None, user_id: str | None,
    natural_month: bool,
):
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    month_start_day = (
        1
        if natural_month
        else ((ledgers[0].month_start_day or 1) if len(ledgers) == 1 else 1)
    )
    return ledgers, month_start_day


@router.get("/workspace/comparison-matrix", response_model=ComparisonMatrixOut)
def comparison_matrix(
    dimension: ComparisonMatrixDimension = Query(default="expense_category"),
    kind: Literal["expense", "income"] = Query(
        default="expense", description="專案/帳戶分組維度要看支出還是收入;類別維度由 dimension 決定"),
    start: str = Query(..., description="起始週期標籤 YYYY-MM(含)"),
    end: str = Query(..., description="結束週期標籤 YYYY-MM(含)"),
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    natural_month: bool = Query(default=False),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComparisonMatrixOut:
    """比較報表矩陣(Web 大螢幕用)。`record_type` 維度的欄位固定是
    expense / income / balance,每月 `total` = 結餘;其餘維度 `total` = 該月
    各欄加總。欄位依期間合計金額降序,「(無)」(`__none__`)固定排最後。"""
    months = _matrix_month_labels(start, end)
    ledgers, month_start_day = _matrix_context(
        db, current_user=current_user, ledger_id=ledger_id, user_id=user_id,
        natural_month=natural_month,
    )
    effective_kind = (
        "income" if dimension.startswith("income") else
        "expense" if dimension.startswith("expense") else kind
    )

    cells: dict[str, dict[str, float]] = {m: {} for m in months}
    col_meta: dict[str, tuple[str, str | None]] = {}
    if ledgers:
        for month, key, label, parent, amt, _row, _cat, _ext in _matrix_legs(
            db, ledgers=ledgers, months=months, dimension=dimension, kind=kind,
            tz_offset_minutes=tz_offset_minutes, month_start_day=month_start_day,
        ):
            if month not in cells:
                continue
            cells[month][key] = cells[month].get(key, 0.0) + amt
            if key not in col_meta or (not col_meta[key][0] and label):
                col_meta[key] = (label, parent)

    if dimension == "record_type":
        col_meta = {"expense": ("expense", None), "income": ("income", None), "balance": ("balance", None)}
        for m in months:
            c = cells[m]
            c["balance"] = c.get("income", 0.0) - c.get("expense", 0.0)

    column_totals = {k: 0.0 for k in col_meta}
    for m in months:
        for k, v in cells[m].items():
            column_totals[k] = column_totals.get(k, 0.0) + v

    def sort_key(k: str) -> tuple[int, float]:
        return (1 if k == _MATRIX_NONE_KEY else 0, -abs(column_totals.get(k, 0.0)))

    ordered = (
        ["expense", "income", "balance"] if dimension == "record_type"
        else sorted(col_meta, key=sort_key)
    )
    columns = [
        ComparisonMatrixColumnOut(key=k, label=col_meta[k][0], parent_label=col_meta[k][1])
        for k in ordered
    ]

    out_rows: list[ComparisonMatrixRowOut] = []
    prev_total: float | None = None
    for m in months:
        r_start, r_end, _ = _analytics_range(
            scope="month", period=m, tz_offset_minutes=tz_offset_minutes,
            month_start_day=month_start_day,
        )
        values = {k: round(cells[m].get(k, 0.0), 2) for k in ordered}
        total = (
            values.get("balance", 0.0) if dimension == "record_type"
            else round(sum(cells[m].values()), 2)
        )
        mom = (
            round((total - prev_total) / abs(prev_total) * 100, 2)
            if prev_total not in (None, 0) else None
        )
        out_rows.append(ComparisonMatrixRowOut(
            month=m, start=r_start, end=r_end, values=values, total=total, mom_pct=mom,
        ))
        prev_total = total

    n = len(months) or 1
    grand_total = (
        round(column_totals.get("balance", 0.0), 2) if dimension == "record_type"
        else round(sum(column_totals.values()), 2)
    )
    return ComparisonMatrixOut(
        dimension=dimension,
        kind=effective_kind,
        columns=columns,
        rows=out_rows,
        column_totals={k: round(column_totals.get(k, 0.0), 2) for k in ordered},
        column_averages={k: round(column_totals.get(k, 0.0) / n, 2) for k in ordered},
        grand_total=grand_total,
    )


@router.get("/workspace/comparison-matrix/cell", response_model=ComparisonMatrixCellOut)
def comparison_matrix_cell(
    dimension: ComparisonMatrixDimension = Query(default="expense_category"),
    kind: Literal["expense", "income"] = Query(default="expense"),
    month: str = Query(..., description="週期標籤 YYYY-MM"),
    column_key: str = Query(..., description="矩陣欄位 key(同 comparison-matrix 回傳的 columns[].key)"),
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    natural_month: bool = Query(default=False),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComparisonMatrixCellOut:
    """比較矩陣單一格子的交易明細(點格子下鑽)。分組規則跟矩陣端點共用
    `_matrix_legs`;`record_type` 維度的 `balance` 欄回傳該月全部收支交易。"""
    months = _matrix_month_labels(month, month)
    ledgers, month_start_day = _matrix_context(
        db, current_user=current_user, ledger_id=ledger_id, user_id=user_id,
        natural_month=natural_month,
    )
    by_tx: dict[str, ComparisonMatrixCellTxOut] = {}
    total = 0.0
    if ledgers:
        for m, key, _label, _parent, amt, row, leg_cat, ext in _matrix_legs(
            db, ledgers=ledgers, months=months, dimension=dimension, kind=kind,
            tz_offset_minutes=tz_offset_minutes, month_start_day=month_start_day,
        ):
            if m != months[0]:
                continue
            if dimension == "record_type" and column_key == "balance":
                signed = amt if key == "income" else -amt
            elif key == column_key:
                signed = amt
            else:
                continue
            total += signed
            sync_id = row.sync_id
            item = by_tx.get(sync_id)
            if item is None:
                by_tx[sync_id] = ComparisonMatrixCellTxOut(
                    sync_id=sync_id,
                    ledger_id=ext,
                    happened_at=row.happened_at,
                    tx_type=row.tx_type,
                    amount=round(signed, 2),
                    category_name=leg_cat,
                    account_name=row.account_name,
                    note=row.note,
                    merchant=row.merchant,
                    is_refund=row.refund_of_sync_id is not None,
                )
            else:
                item.amount = round(item.amount + signed, 2)
    items = sorted(by_tx.values(), key=lambda i: _to_utc(i.happened_at), reverse=True)
    return ComparisonMatrixCellOut(
        month=months[0], column_key=column_key, total=round(total, 2), items=items,
    )


# ---------------------------------------------------------------------------
# 净值历史端点
# ---------------------------------------------------------------------------

@router.get("/workspace/net-worth-history", response_model=NetWorthHistoryOut)
def workspace_net_worth_history(
    ledger_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NetWorthHistoryOut:
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin,
        ledger_id=ledger_id, user_id=user_id,
    )
    ledger_internal_ids = [l.id for l in ledgers]
    if not ledger_internal_ids:
        return NetWorthHistoryOut(series=[], multi_currency=False)

    # 欠款/應收(§淨資產卡同一份口徑,見 `_workspace_debt_currency_totals`
    # docstring):目前沒有逐月的欠款餘額歷史(debt 不像 account 有完整交易流水
    # 可以逐月 replay),這裡簡化成「目前未結清餘額」當常數疊加到每個月分桶
    # (見下方 `_net()`),而不是真正的歷史欠款餘額——跟純用 accounts 算出來的
    # 趨勢線相比,早期月份的資產/負債會偏移,但至少「最新一個月」會跟淨資產卡
    # 對上帳,好過完全不算。
    debt_totals = _workspace_debt_currency_totals(db, ledgers=ledgers)

    accts = db.execute(
        select(
            UserAccountProjection.sync_id,
            UserAccountProjection.account_type,
            UserAccountProjection.currency,
            UserAccountProjection.initial_balance,
            UserAccountProjection.include_in_total,
        ).where(UserAccountProjection.user_id == current_user.id)
    ).all()
    # 納入總餘額(Phase 18):跟資產頁淨資產卡同口徑,include_in_total=False 的
    # 帳戶整條(初始餘額 + 後續交易增減)都不進淨值序列,而不是只濾掉初始餘額。
    accts = [a for a in accts if a.include_in_total is not False]
    init_by_acc = {a.sync_id: float(a.initial_balance or 0.0) for a in accts}
    is_liab = {a.sync_id: (a.account_type in ("credit_card", "loan")) for a in accts}
    acc_currency = {a.sync_id: (a.currency or "CNY").upper() for a in accts}
    currencies = {(a.currency or "CNY").upper() for a in accts if a.sync_id in init_by_acc}
    currencies |= set(debt_totals.keys())
    multi_currency = len(currencies) > 1

    # 主币种(base):用户设的 primary_currency;没设则单币种用唯一币种、多币种留空
    # (留空时下方 rates_to_base 为空 → 全部账户剔除 → series 为空,由前端引导设主币种)。
    base = (
        db.execute(
            select(UserProfile.primary_currency).where(
                UserProfile.user_id == current_user.id
            )
        ).scalar()
        or ""
    ).upper()
    if not base and len(currencies) == 1:
        # 没设主币种且单币种:回退到该唯一币种(折算率 1)。多币种未设主币种则 base
        # 为空 → rates_to_base 为空 → series 各点折算后为 0,前端据 needsBase 出引导卡。
        base = next(iter(currencies))

    # 各币种 → base 汇率,净值序列折算到主币种(与净资产卡同口径):base 自身 1.0;
    # 自动缓存 payload 是「1 base = x quote」取倒数;手动 override「1 quote = rate base」
    # 覆盖自动。缺汇率的币种在 _net 内整条剔除,绝不按 1.0 裸加。
    rates_to_base: dict[str, float] = {}
    if base:
        rates_to_base[base] = 1.0
        cache_payload = db.execute(
            select(ExchangeRateCache.payload_json).where(
                ExchangeRateCache.base_currency == base
            )
        ).scalar()
        if isinstance(cache_payload, dict):
            for q, x in cache_payload.items():
                try:
                    xf = float(x)
                except (TypeError, ValueError):
                    continue
                if xf > 0:
                    rates_to_base[q.upper()] = 1.0 / xf
        for ov in db.execute(
            select(
                UserExchangeRateProjection.quote_currency,
                UserExchangeRateProjection.rate,
            ).where(
                UserExchangeRateProjection.user_id == current_user.id,
                UserExchangeRateProjection.base_currency == base,
            )
        ).all():
            try:
                r = float(ov.rate)
            except (TypeError, ValueError):
                continue
            if r > 0:
                rates_to_base[ov.quote_currency.upper()] = r

    # 欠款/應收折到 base 後的常數增量(見上方 debt_totals 註解):缺匯率的幣種
    # 整條剔除,絕不按 1.0 裸加,跟 accounts 同口徑。payable(我欠對方)算負債,
    # 用負數疊加,對齐 credit_card/loan 帳戶餘額本身就是負數的慣例。
    debt_assets_delta = 0.0
    debt_liab_delta = 0.0
    for cur, vals in debt_totals.items():
        rate = rates_to_base.get(cur)
        if rate is None:
            continue
        debt_assets_delta += vals["receivable_total"] * rate
        debt_liab_delta -= vals["payable_total"] * rate

    txs = db.execute(
        select(
            ReadTxProjection.tx_type,
            ReadTxProjection.amount,
            # 跨幣別轉帳(2026-08):轉入端要用轉入帳戶自身幣別的金額,不是
            # 轉出端的 amount——同幣種轉帳 to_amount 是 NULL,下面 _apply
            # COALESCE 回 amount,行為不變。
            ReadTxProjection.to_amount,
            # 轉帳手續費/折損(2026-08-29):疊加在餘額計算上的獨立 delta,
            # 沒有手續費/折損的舊資料皆為 NULL,下面 _apply COALESCE 回 0,
            # 行為不變。
            ReadTxProjection.fee_amount,
            ReadTxProjection.discount_amount,
            ReadTxProjection.happened_at,
            ReadTxProjection.account_sync_id,
            ReadTxProjection.from_account_sync_id,
            ReadTxProjection.to_account_sync_id,
        )
        .where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
        .order_by(ReadTxProjection.happened_at.asc())
    ).all()

    bal = dict(init_by_acc)

    def _apply(tx_type, amt, to_amt, fee_amt, discount_amt, acc, from_acc, to_acc):
        if tx_type == "income" and acc in bal:
            bal[acc] += amt
        elif tx_type == "expense" and acc in bal:
            bal[acc] -= amt
        elif tx_type == "adjustment" and acc in bal:
            bal[acc] += amt
        elif tx_type == "transfer":
            fa, ta = from_acc or acc, to_acc
            if fa in bal:
                bal[fa] -= amt + fee_amt
            if ta in bal:
                bal[ta] += to_amt - discount_amt

    def _net():
        # 折算到主币种:各账户余额 × 该币种汇率;缺汇率(或无 base)的账户整条剔除,
        # 与净资产卡同口径,绝不按 1.0 裸加。
        assets = 0.0
        liab = 0.0
        for k, v in bal.items():
            rate = rates_to_base.get(acc_currency.get(k, base))
            if rate is None:
                continue
            vb = v * rate
            if is_liab.get(k, False):
                liab += vb
            else:
                assets += vb
        # 欠款/應收(目前未結清餘額,非逐月歷史回放,見上方 debt_totals 註解)。
        assets += debt_assets_delta
        liab += debt_liab_delta
        return assets, liab

    series: list[NetWorthHistorySeriesItemOut] = []
    last_bucket: str | None = None
    for tx in txs:
        if tx.happened_at is None:
            continue
        ha = _to_utc(tx.happened_at)
        bucket = (ha + timedelta(minutes=tz_offset_minutes)).strftime("%Y-%m")
        if last_bucket is not None and bucket != last_bucket:
            a, l = _net()
            series.append(NetWorthHistorySeriesItemOut(
                bucket=last_bucket, net_worth=a + l, assets=a, liabilities=l,
            ))
        _apply(
            tx.tx_type, float(tx.amount or 0.0),
            float(tx.to_amount) if tx.to_amount is not None else float(tx.amount or 0.0),
            float(tx.fee_amount or 0.0),
            float(tx.discount_amount or 0.0),
            tx.account_sync_id, tx.from_account_sync_id, tx.to_account_sync_id,
        )
        last_bucket = bucket
    if last_bucket is not None:
        a, l = _net()
        series.append(NetWorthHistorySeriesItemOut(
            bucket=last_bucket, net_worth=a + l, assets=a, liabilities=l,
        ))
    if not series and (init_by_acc or debt_totals):
        a, l = _net()
        series.append(NetWorthHistorySeriesItemOut(
            bucket=datetime.now(timezone.utc).strftime("%Y-%m"),
            net_worth=a + l, assets=a, liabilities=l,
        ))

    # 补齐稀疏 series 为连续月序列:缺月沿用上月末值(净值存量,无交易即持平)。
    if series:
        sparse = {s.bucket: s for s in series}

        def _month_range(start_ym: str, end_ym: str):
            sy, sm = (int(x) for x in start_ym.split("-"))
            ey, em = (int(x) for x in end_ym.split("-"))
            y, m = sy, sm
            while (y, m) <= (ey, em):
                yield f"{y:04d}-{m:02d}"
                m += 1
                if m > 12:
                    m, y = 1, y + 1

        first_ym = series[0].bucket
        now_ym = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)).strftime("%Y-%m")
        end_ym = max(series[-1].bucket, now_ym)
        filled: list[NetWorthHistorySeriesItemOut] = []
        prev: NetWorthHistorySeriesItemOut | None = None
        for ym in _month_range(first_ym, end_ym):
            if ym in sparse:
                prev = sparse[ym]
                filled.append(prev)
            elif prev is not None:
                filled.append(NetWorthHistorySeriesItemOut(
                    bucket=ym, net_worth=prev.net_worth,
                    assets=prev.assets, liabilities=prev.liabilities))
        series = filled

    return NetWorthHistoryOut(series=series, multi_currency=multi_currency)
