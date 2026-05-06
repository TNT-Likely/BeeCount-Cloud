"""跨账本聚合读端点:/workspace/{accounts,categories,tags,transactions,
ledger-counts,analytics}。

跟 ledgers.py 的区别:这里的查询不锁定到单个账本,会扫 caller 所有可见账本的
projection 做聚合(tx 计数 / balance / category 排行等)。
去重 / 跨账本 dedup / owner 信息回填的逻辑也在这里。"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — imports + helpers + router

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
        query = query.where(or_(
            ReadTxProjection.account_sync_id == account_sync_id,
            ReadTxProjection.from_account_sync_id == account_sync_id,
            ReadTxProjection.to_account_sync_id == account_sync_id,
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


# ---------------------------------------------------------------------------
# CSV 导出 — 跟 list_workspace_transactions 共用一组 filter,流式输出。
# 设计:.docs/web-csv-export-design.md
# ---------------------------------------------------------------------------


_CSV_HEADERS_BY_LANG: dict[str, list[str]] = {
    # 跟 mobile lib/pages/data/export_page.dart 的 11 列严格对齐:
    # Type, Category, SubCategory, Amount, Account, FromAccount,
    # ToAccount, Note, Time, Tags, Attachments
    "zh-CN": ["类型", "分类", "二级分类", "金额", "账户", "转出账户",
              "转入账户", "备注", "时间", "标签", "附件"],
    "zh-TW": ["類型", "分類", "二級分類", "金額", "帳戶", "轉出帳戶",
              "轉入帳戶", "備註", "時間", "標籤", "附件"],
    "en":    ["Type", "Category", "Subcategory", "Amount", "Account",
              "From Account", "To Account", "Note", "Time", "Tags",
              "Attachments"],
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
      只写 category。父类名走 LEFT JOIN read_category_projection 拿 parent_name。
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

    # 账本筛选 — 跟 list_workspace_transactions 完全一致
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

    if ledgers:
        ledger_internal_ids = [l.id for l in ledgers]
        primary_name = _sanitize_filename(_resolve_ledger_name(db, ledger=ledgers[0]))
    else:
        ledger_internal_ids = []
        primary_name = "ledger"

    # LEFT JOIN ReadCategoryProjection 拿 level + parent_name,做 parent/sub 列拆分
    Cat = aliased(ReadCategoryProjection)

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
                Cat.ledger_id == ReadTxProjection.ledger_id,
                Cat.sync_id == ReadTxProjection.category_sync_id,
            ),
        )
    )

    if ledger_internal_ids:
        query = query.where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
    else:
        query = query.where(false_literal())

    # 复用 list endpoint 的所有 filter 条件
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

            yield ",".join([
                _csv_field(type_label),
                _csv_field(category_col),
                _csv_field(sub_category_col),
                f"{tx.amount:.2f}" if tx.amount is not None else "",
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

    # account 是 **user-global** 实体(Flutter 侧 Accounts 表没 ledger_id),但
    # projection 历史上 per-ledger 重复存(snapshot 每 ledger 各一份)。所以 tx
    # 聚合也要 **跨 ledger** 按 account_sync_id 累加,不能按 (ledger, account)
    # 分桶 —— 否则后面的 dedup(`best_by_key` 按 last_change_id 留一份 ledger
    # 下的 account)会跟 tx 聚合的 ledger 对不上,tx_count 永远 miss。
    # 用户可见 ledger 范围靠 `ledger_id IN ledger_internal_ids` 限定。
    from sqlalchemy import case as sa_case

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
        ).group_by(ReadTxProjection.account_sync_id)
    ).all()

    # Transfer adjustments: from_account = minus, to_account = plus
    transfer_from = db.execute(
        select(
            ReadTxProjection.from_account_sync_id,
            func.count().label("cnt"),
            func.coalesce(func.sum(ReadTxProjection.amount), 0.0).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.from_account_sync_id.is_not(None),
        ).group_by(ReadTxProjection.from_account_sync_id)
    ).all()
    transfer_to = db.execute(
        select(
            ReadTxProjection.to_account_sync_id,
            func.count().label("cnt"),
            func.coalesce(func.sum(ReadTxProjection.amount), 0.0).label("amt"),
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.to_account_sync_id.is_not(None),
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
        # stats dict 的 key 是 sync_id(跨 ledger 聚合,见上面 group_by 改动)
        bucket = stats.get(sync_id)
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
                    note=acct.note,
                    credit_limit=acct.credit_limit,
                    billing_day=acct.billing_day,
                    payment_due_day=acct.payment_due_day,
                    bank_name=acct.bank_name,
                    card_last_four=acct.card_last_four,
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

    # tx_count 聚合:按 (ledger_id, category_sync_id) 数 ReadTxProjection 行。
    # category_sync_id 在 tx projection 上可空(转账类交易没分类),NULL 用单独
    # 桶不计入。这里用 sync_id 全局聚合(不区分 ledger)是因为前端 dedup 时
    # 同 syncId 跨账本会合并展示,统计一并合并跟 dedup 一致。
    from collections import defaultdict
    tx_count_by_sync_id: dict[str, int] = defaultdict(int)
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
                    tx_count=tx_count_by_sync_id.get(sync_id, 0) if sync_id else 0,
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
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceAnalyticsOut:
    is_admin = _is_admin(current_user)
    start_at, end_at, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,
    )

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

        # 用本地时区折算的日期算"记账天数",跟 _bucket_key 同步;否则东半球用户在
        # 本地 0-8 点记的笔会被算到前一天的 distinct_days,跟日历视图不一致。
        from datetime import timedelta as _td

        for tx_type_val, amount, happened_at_raw, cat_name in db.execute(tx_query).all():
            if happened_at_raw is None:
                continue
            happened_at = _to_utc(happened_at_raw)
            amt = float(amount or 0.0)
            transaction_count += 1
            local_for_day = happened_at + _td(minutes=tz_offset_minutes)
            distinct_days_set.add(local_for_day.strftime("%Y-%m-%d"))
            if first_tx_at is None or happened_at < first_tx_at:
                first_tx_at = happened_at
            if last_tx_at is None or happened_at > last_tx_at:
                last_tx_at = happened_at
            bucket = _bucket_key(scope, happened_at, tz_offset_minutes)
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
