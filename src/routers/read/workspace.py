"""跨账本聚合读端点:/workspace/{accounts,categories,tags,transactions,
ledger-counts,analytics}。

跟 ledgers.py 的区别:这里的查询不锁定到单个账本,会扫 caller 所有可见账本的
projection 做聚合(tx 计数 / balance / category 排行等)。
去重 / 跨账本 dedup / owner 信息回填的逻辑也在这里。"""
from __future__ import annotations

import json
import statistics as _stats

from pydantic import BaseModel
from sqlalchemy import false as sa_false
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...entity_extensions import (
    loan_derived,
    time_deposit_derived,
    time_deposit_payout_plans_derived,
)
from ...models import (
    ExchangeRateCache,
    ReadLoanProjection,
    ReadReimbursementProjection,
    ReadTimeDepositProjection,
    ReadTxProjection,
    UserAccountProjection,
    UserExchangeRateProjection,
)
from ...schemas import (
    ReadLoanOut,
    ReadReimbursementOut,
    ReadTimeDepositOut,
    TimeDepositPayoutPlanOut,
)
from ._shared import *  # noqa: F401,F403 — imports + helpers + router

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

def _parse_transfer_fee(
    fee_json: str | None,
) -> tuple[float, str | None, str | None]:
    """解析转账手续费/优惠 JSON → (amount, mode, fee_account_id)。

    amount<=0 / 字段缺失 / 非法 JSON → (0.0, None, None)。
    mode ∈ {deduct_from_source, deduct_from_target, separate_account, discount};
    缺省视作 deduct_from_source(.docs / models.py 注释口径)。
    """
    fee = 0.0
    mode = None
    fee_account_id = None
    if fee_json:
        try:
            d = json.loads(fee_json)
        except (json.JSONDecodeError, TypeError):
            d = None
        if isinstance(d, dict):
            fee = float(d.get("amount") or 0.0)
            mode = d.get("mode")
            fee_account_id = d.get("feeAccountId")
    return fee, mode, fee_account_id


def _transfer_account_deltas(
    amount: float,
    from_account_sync_id: str | None,
    to_account_sync_id: str | None,
    transfer_fee_json: str | None,
) -> dict[str, float]:
    """转账对涉及账户的完整余额增量 {account_sync_id: delta}。

    手续费三模式 + 优惠模式(.docs / models.py):
    - deduct_from_source: 转出方额外扣 fee(到账=amount)
    - deduct_from_target: 到账方少到 fee(到账=amount-fee)
    - separate_account: 手续费从 feeAccountId 另扣
    - discount: 转出方少扣 fee(实扣=amount-fee,到账=amount)——银行/平台优惠
    amount<=0 / 字段缺失 → 退化为普通转账(amount / +amount)。
    """
    deltas: dict[str, float] = {}
    if from_account_sync_id:
        deltas[from_account_sync_id] = -amount
    if to_account_sync_id:
        deltas[to_account_sync_id] = deltas.get(to_account_sync_id, 0.0) + amount

    fee, mode, fee_account_id = _parse_transfer_fee(transfer_fee_json)
    if fee <= 0:
        return deltas
    if mode == "deduct_from_target":
        if to_account_sync_id:
            deltas[to_account_sync_id] = deltas.get(to_account_sync_id, 0.0) - fee
    elif mode == "separate_account":
        if fee_account_id:
            deltas[fee_account_id] = deltas.get(fee_account_id, 0.0) - fee
    elif mode == "discount":
        # 优惠:转出方少扣(实扣 amount-fee),到账方仍全收 amount
        if from_account_sync_id:
            deltas[from_account_sync_id] = deltas.get(from_account_sync_id, 0.0) + fee
    else:  # deduct_from_source (default)
        if from_account_sync_id:
            deltas[from_account_sync_id] = deltas.get(from_account_sync_id, 0.0) - fee
    return deltas


def _transfer_fee_only_deltas(
    transfer_fee_json: str | None,
    from_account_sync_id: str | None,
    to_account_sync_id: str | None,
) -> dict[str, float]:
    """仅手续费/优惠的余额增量(不含基础转账金额),供聚合余额在 SQL 基础额之上叠加。

    返回 {被扣方 account_sync_id: -fee};discount 模式被"少扣"方是转出方,
    返回 {转出方: +fee};无 amount / 被扣方缺失 → {}。
    """
    fee, mode, fee_account_id = _parse_transfer_fee(transfer_fee_json)
    if fee <= 0:
        return {}
    if mode == "deduct_from_target":
        return {to_account_sync_id: -fee} if to_account_sync_id else {}
    if mode == "separate_account":
        return {fee_account_id: -fee} if fee_account_id else {}
    if mode == "discount":
        return {from_account_sync_id: fee} if from_account_sync_id else {}
    # deduct_from_source (default)
    return {from_account_sync_id: -fee} if from_account_sync_id else {}


def _to_float_or_zero(v: object) -> float:
    """安全转 float,非法/None → 0.0。"""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _build_name_to_id(db: Session, target_user_id: str) -> dict[str, str]:
    """(账户名小写) → account_sync_id,供合并付款 legs 仅带 accountName 时反查。"""
    rows = db.execute(
        select(UserAccountProjection.sync_id, UserAccountProjection.name).where(
            UserAccountProjection.user_id == target_user_id
        )
    ).all()
    return {
        (name or "").strip().lower(): sid
        for sid, name in rows
        if sid and name
    }


def _resolve_leg_account_id(
    leg: dict, name_to_id: dict[str, str] | None
) -> str | None:
    """合并付款单腿的账户 syncId:优先 accountId,缺则按 accountName 反查。

    前端 wire 自带 accountId;CSV/导入路径仅带 accountName,需 name_to_id 反查。
    """
    aid = leg.get("accountId") or leg.get("account_id")
    if aid:
        return aid
    name = leg.get("accountName") or leg.get("account_name") or leg.get("name")
    if name and name_to_id:
        return name_to_id.get(name.strip().lower())
    return None


def _expense_leg_deltas(
    payment_legs_json: str | None,
    name_to_id: dict[str, str] | None,
) -> dict[str, float]:
    """支出合并付款:每条腿 account 扣 -leg.amount。返回 {account_sync_id: -amount}。

    无 legs / 非法 JSON / 解析不到账户 → {}。同一账户出现多腿时累加(罕见但稳健)。
    """
    if not payment_legs_json:
        return {}
    try:
        legs = json.loads(payment_legs_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(legs, list) or not legs:
        return {}
    out: dict[str, float] = {}
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        aid = _resolve_leg_account_id(leg, name_to_id)
        if not aid:
            continue
        amt = _to_float_or_zero(leg.get("amount"))
        if amt <= 0:
            continue
        out[aid] = out.get(aid, 0.0) - amt
    return out


def _tx_effect_on_account(
    tx: ReadTxProjection,
    account_sync_id: str,
    name_to_id: dict[str, str] | None = None,
) -> float:
    """该交易对指定账户的余额影响(amount 口径,与 WorkspaceAccount.balance 一致)。

    转账含手续费时按 mode 折算(转出额外扣 / 到账内扣 / 单独账户扣);
    支出含合并付款(payment_legs)时按各腿账户分摊扣款,顶层 account_sync_id
    若非某腿账户则不额外扣(避免重复扣全额)。
    """
    amount = float(tx.amount or 0.0)
    if tx.tx_type == "income" and tx.account_sync_id == account_sync_id:
        return amount
    if tx.tx_type == "expense":
        leg_deltas = _expense_leg_deltas(tx.payment_legs_json, name_to_id)
        if leg_deltas:
            return leg_deltas.get(account_sync_id, 0.0)
        if tx.account_sync_id == account_sync_id:
            return -amount
        return 0.0
    if tx.tx_type == "transfer":
        return _transfer_account_deltas(
            amount, tx.from_account_sync_id, tx.to_account_sync_id, tx.transfer_fee_json
        ).get(account_sync_id, 0.0)
    return 0.0


def _balance_by_account_for(
    row: ReadTxProjection,
    running_by_target: dict[str, dict[str, float]],
    name_to_id: dict[str, str] | None,
) -> dict[str, float] | None:
    """合并付款(0021)各腿账户在该笔交易完成后的余额,形如 {account_sync_id: balance}。

    仅取本笔 payment_legs 里出现的腿账户;某腿账户没被追踪(如对账单模式、或
    账户过多放弃计算)则跳过。没有任何腿账户余额可给时返回 None,前端回退到
    单个 balance_after。
    """
    if not row.payment_legs_json:
        return None
    legs = _expense_leg_deltas(row.payment_legs_json, name_to_id)
    if not legs:
        return None
    out: dict[str, float] = {}
    for aid in legs:
        bal = running_by_target.get(aid, {}).get(row.sync_id)
        if bal is not None:
            out[aid] = bal
    return out or None


def _compute_balance_after_map(
    db: Session,
    ledger_internal_ids: list[int],
    rows: list[ReadTxProjection],
    target_user_id: str,
    statement_account_sync_id: str | None,
    name_to_id: dict[str, str] | None = None,
    max_targets: int = 50,
) -> dict[str, dict[str, float]]:
    """计算每笔交易完成后各账户余额,返回 {account_sync_id: {tx_sync_id: balance_after}}。

    - statement_account_sync_id 给定时(账户筛选模式),所有行都追踪该账户,
      转账双向都计入。
    - 否则每行追踪自己的主账户(支出/收入=账户;转账=转出账户)。
    - 非 statement 模式下,合并付款(0021)的每条腿账户也作为独立 target 追踪,
      从而能分别给出每个腿账户的余额(交易流水中合并付款要分别显示各账户余额)。
    余额口径与 WorkspaceAccount.balance 一致(initial + income - expense +
    transfer_to - transfer_from,amount 口径),单账本/单账户视图精确。
    超过 max_targets 个分散账户(无筛选大杂烩视图)时放弃计算,返回空 dict。
    """
    if not rows:
        return {}

    row_target: dict[str, str | None] = {}
    # 合并付款的腿账户(非 statement 模式才纳入 target,避免改变对账单模式成本)
    leg_accounts: set[str] = set()
    for row in rows:
        if statement_account_sync_id:
            row_target[row.sync_id] = statement_account_sync_id
        elif row.tx_type in ("income", "expense"):
            row_target[row.sync_id] = row.account_sync_id
            if row.tx_type == "expense" and row.payment_legs_json:
                for leg_aid in _expense_leg_deltas(
                    row.payment_legs_json, name_to_id
                ):
                    leg_accounts.add(leg_aid)
        elif row.tx_type == "transfer":
            row_target[row.sync_id] = row.from_account_sync_id
        else:
            row_target[row.sync_id] = None

    distinct_targets = {t for t in row_target.values() if t}
    if not statement_account_sync_id:
        distinct_targets |= leg_accounts
    if not distinct_targets or (
        not statement_account_sync_id and len(distinct_targets) > max_targets
    ):
        return {}

    init_rows = db.scalars(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == target_user_id,
            UserAccountProjection.sync_id.in_(distinct_targets),
        )
    ).all()
    init_map = {a.sync_id: float(a.initial_balance or 0.0) for a in init_rows}

    # 两类「第三账户」的 running balance 无法靠 account/from/to 列直接查到,
    # 必须显式聚合进对应 target 的流水:
    # 1) separate_account 手续费从 feeAccountId 另扣;
    # 2) 合并付款(0021) legs 从腿账户扣 —— 这里也是非 statement 模式下
    #    让每个腿账户能正确算出「该笔完成后余额」的关键(否则腿账户查不到
    #    自己名下的支出,balance_by_account 只会剩主账户一个)。
    # statement 模式下 distinct_targets 只有被筛选账户,下面两个循环只会对
    # 命中的第三账户生效,与原来行为一致。
    extra_by_target: dict[str, list[ReadTxProjection]] = {}
    fee_txs = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.transfer_fee_json.is_not(None),
        )
    ).all()
    for tx in fee_txs:
        _, mode, fee_account_id = _parse_transfer_fee(tx.transfer_fee_json)
        if mode == "separate_account" and fee_account_id in distinct_targets:
            extra_by_target.setdefault(fee_account_id, []).append(tx)
    # 合并付款:每条腿账户若是 target,则把该支出归入其「对账单」。
    leg_txs = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "expense",
            ReadTxProjection.payment_legs_json.is_not(None),
        )
    ).all()
    for tx in leg_txs:
        for leg_aid in _expense_leg_deltas(tx.payment_legs_json, name_to_id):
            if leg_aid in distinct_targets:
                extra_by_target.setdefault(leg_aid, []).append(tx)

    running_by_target: dict[str, dict[str, float]] = {}
    for target in distinct_targets:
        txs = db.scalars(
            select(ReadTxProjection)
            .where(
                ReadTxProjection.ledger_id.in_(ledger_internal_ids),
                or_(
                    ReadTxProjection.account_sync_id == target,
                    ReadTxProjection.from_account_sync_id == target,
                    ReadTxProjection.to_account_sync_id == target,
                ),
            )
            .order_by(
                ReadTxProjection.happened_at.asc(),
                ReadTxProjection.tx_index.asc(),
            )
        ).all()
        # 合并 separate_account 手续费转账(按时间序)
        _min_dt = datetime.min
        extra = sorted(
            extra_by_target.get(target, []),
            key=lambda t: (t.happened_at or _min_dt, t.tx_index),
        )
        combined = sorted(
            list(txs) + extra,
            key=lambda t: (t.happened_at or _min_dt, t.tx_index),
        )
        # combined 去重(理论上 txs 与 extra 不重叠,稳妥起见)
        seen: set[str] = set()
        ordered: list[ReadTxProjection] = []
        for t in combined:
            if t.sync_id not in seen:
                seen.add(t.sync_id)
                ordered.append(t)
        bal = init_map.get(target, 0.0)
        per_tx: dict[str, float] = {}
        for tx in ordered:
            bal += _tx_effect_on_account(tx, target, name_to_id)
            per_tx[tx.sync_id] = bal
        running_by_target[target] = per_tx
    return running_by_target


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
    with_balance: bool = Query(default=False, description="为 true 时计算每笔交易完成后主账户余额(balance_after),开销随涉及账户数增加。"),
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
    # 余额计算前置:目标用户(账户归属)与"账户流水"模式的目标账户 syncId。
    target_user_id = user_id if (is_admin and user_id) else current_user.id
    name_to_id = _build_name_to_id(db, target_user_id)
    id_to_name = {sid: nm for nm, sid in name_to_id.items()}
    statement_account_sync_id: str | None = account_sync_id
    statement_account_name: str | None = None
    if account_sync_id:
        statement_account_name = id_to_name.get(account_sync_id)
    if not statement_account_sync_id and account_name:
        # 账户名精确(trim + 小写)匹配单个账户 → 视为"该账户流水"模式,转账双向计入;
        # 否则(模糊/多匹配)退化为逐行主账户,避免错配。
        name_norm = account_name.strip().lower()
        matched = db.scalars(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == target_user_id,
                UserAccountProjection.name.ilike(f"%{account_name.strip()}%"),
            )
        ).all()
        exact = [a for a in matched if (a.name or "").strip().lower() == name_norm]
        if len(exact) == 1:
            statement_account_sync_id = exact[0].sync_id
            statement_account_name = exact[0].name
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
    if account_name and not statement_account_sync_id:
        # statement 模式(精确匹配到单个账户)下改由下方 account_sync_id 过滤接管,
        # 否则会误排除"主账户非该账户、但合并付款 legs 含该账户"的支出。
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
        # 精确账户过滤。statement 模式下需同时纳入:该账户作为"合并付款 legs 腿"
        # 的支出(主账户是另一账户,普通 account_sync_id 过滤会漏)。前端 legs 带
        # accountId、导入 legs 仅带 accountName,两种都覆盖。
        acct_filter = [
            ReadTxProjection.account_sync_id == account_sync_id,
            ReadTxProjection.from_account_sync_id == account_sync_id,
            ReadTxProjection.to_account_sync_id == account_sync_id,
        ]
        if statement_account_sync_id:
            acct_filter.append(
                ReadTxProjection.payment_legs_json.like(f'%"{account_sync_id}"%')
            )
            if statement_account_name:
                acct_filter.append(
                    ReadTxProjection.payment_legs_json.like(
                        f'%accountName":"{statement_account_name}"%'
                    )
                )
        query = query.where(or_(*acct_filter))
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

    # 0022 退款进度:按本页原单批量聚合两个口径(都不含 cancelled)——
    # refunded_by_tx 按原单合计(退款维度),refunded_by_tx_account 按
    # (原单, 去向卡)拆分(卡维度:流水删除线只认退回本卡的部分,跨卡退款
    # 钱到了别的卡,本卡支出仍成立,与账户余额口径一致)。
    refunded_by_tx, refunded_by_tx_account = _refund_totals_for_txs(
        db,
        ledger_ids=ledger_internal_ids,
        tx_ids=[r.sync_id for r in rows],
        received_only=True,
    )

    # 资产流水余额:每笔交易完成后各账户余额。仅 with_balance 时计算,开销随
    # 涉及账户数增加;无筛选的大杂烩视图里分散账户过多时函数内部会放弃计算。
    # running_by_target: {account_sync_id: {tx_sync_id: balance_after}}。
    running_by_target: dict[str, dict[str, float]] = {}
    if with_balance and ledger_internal_ids:
        running_by_target = _compute_balance_after_map(
            db, ledger_internal_ids, rows, target_user_id,
            statement_account_sync_id, name_to_id,
        )

    def _primary_target(row: ReadTxProjection) -> str | None:
        if statement_account_sync_id:
            return statement_account_sync_id
        if row.tx_type in ("income", "expense"):
            return row.account_sync_id
        if row.tx_type == "transfer":
            return row.from_account_sync_id
        return None

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
    # 归物(0029):本页交易关联到的物品名(一次查完,避免逐行查)。
    item_names = item_names_by_sync_id(db, rows, ledger_ids=set(ledger_meta))
    # 定期存款(0031):本页交易关联到的定存展示名(同样一次查完)。
    time_deposit_names = time_deposit_names_by_sync_id(db, rows, ledger_ids=set(ledger_meta))
    # 退款到账(0032):本页退款关联交易 → 原账单摘要(一次查完,避免逐行查)。
    refund_source_labels = refund_source_labels_by_tx_sync_id(
        db, rows, ledger_ids=set(ledger_meta)
    )

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
                exclude_from_stats=bool(row.exclude_from_stats),
                exclude_from_budget=bool(row.exclude_from_budget),
                # 0022 退款进度:有效退款(不含 cancelled)累计。前端在
                # refunded_total >= amount(容差 0.011)时给金额画删除线。
                refunded_total=refunded_by_tx.get(row.sync_id, 0.0),
                # 0022 退款进度(卡维度):其中实际退回本卡的部分。跨卡退款
                # 不计入 —— 钱到了别的卡,本卡支出仍成立,删除线以此为准。
                refunded_to_account=_refunded_to_account(
                    row,
                    totals=refunded_by_tx,
                    by_account=refunded_by_tx_account,
                ),
                currency_code=row.currency_code,
                native_amount=row.native_amount,
                **_tx_extension_fields(
                    row,
                    item_name=item_names.get(row.item_sync_id or ""),
                    time_deposit_name=time_deposit_names.get(row.time_deposit_sync_id or ""),
                    refund_source_label=refund_source_labels.get(row.sync_id or ""),
                ),
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
                balance_after=running_by_target.get(_primary_target(row), {}).get(row.sync_id),
                # 合并付款(0021):把每条腿账户的余额单独给出,交易流水中分别展示。
                # 非合并付款 / 对账单模式 / 余额不可算时均为 null(前端回退到 balance_after)。
                balance_by_account=(
                    _balance_by_account_for(row, running_by_target, name_to_id)
                    if with_balance and row.payment_legs_json
                    else None
                ),
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
    # v0021 在末尾追加 3 列扩展字段:优惠 / 手续费 / 多账户(web-only, mobile 导入忽略未知列)
    "zh-CN": ["类型", "分类", "二级分类", "金额", "币种", "账户", "转出账户",
              "转入账户", "备注", "时间", "标签", "附件", "优惠", "手续费", "多账户"],
    "zh-TW": ["類型", "分類", "二級分類", "金額", "幣種", "帳戶", "轉出帳戶",
              "轉入帳戶", "備註", "時間", "標籤", "附件", "優惠", "手續費", "多賬戶"],
    "en":    ["Type", "Category", "Subcategory", "Amount", "Currency",
              "Account", "From Account", "To Account", "Note", "Time",
              "Tags", "Attachments", "Discount", "Fee", "Split Accounts"],
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

    from fastapi.responses import StreamingResponse
    from sqlalchemy.orm import aliased

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

            # v0021 扩展字段:优惠 / 手续费 / 多账户(无则空列,不影响基础 12 列)
            discount_str = _fmt_discount(tx.discount_json)
            fee_str = _fmt_transfer_fee(tx.transfer_fee_json)
            legs_str = _fmt_payment_legs(tx.payment_legs_json)

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
                _csv_field(discount_str),
                _csv_field(fee_str),
                _csv_field(legs_str),
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


# ──────────── v0021 扩展字段 CSV 格式化 ────────────
# 导出成"可读 + 可回导"的紧凑文本:优惠 / 手续费输出金额数字,多账户输出
# "账户名:金额; 账户名:金额"。导入端(transformer)兼容 JSON 与这种简单文本。


def _fmt_discount(raw: str | None) -> str:
    """优惠列:导出 discount_json 的 discountAmount(优惠金额,正数);无则空。"""
    if not raw:
        return ""
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(d, dict):
        return ""
    amt = d.get("discountAmount")
    if amt is None:
        amt = d.get("value")
    if amt is None:
        return ""
    try:
        return f"{float(amt):.2f}"
    except (TypeError, ValueError):
        return ""


def _fmt_transfer_fee(raw: str | None) -> str:
    """手续费列:导出 transfer_fee_json 的 amount(手续费金额,正数);无则空。"""
    if not raw:
        return ""
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(d, dict):
        return ""
    amt = d.get("amount")
    if amt is None:
        return ""
    try:
        return f"{float(amt):.2f}"
    except (TypeError, ValueError):
        return ""


def _fmt_payment_legs(raw: str | None) -> str:
    """多账户列:导出 payment_legs_json 为 '账户名:金额; 账户名:金额';无则空。"""
    if not raw:
        return ""
    try:
        legs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(legs, list) or not legs:
        return ""
    parts: list[str] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        name = leg.get("accountName") or leg.get("account_name") or leg.get("name")
        amt = leg.get("amount")
        if not name or amt is None:
            continue
        try:
            parts.append(f"{name}:{float(amt):.2f}")
        except (TypeError, ValueError):
            continue
    return "; ".join(parts)


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

    # Main account stats: income + expense,按 account_sync_id 聚合。
    # 合并付款(payment_legs)的支出不再主账户全额计,改由下方 legs 分摊逐腿计入,
    # 故此处排除 legs 支出(income / 普通支出 legs 恒为 NULL,排除无副作用)。
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
            ReadTxProjection.payment_legs_json.is_(None),
        ).group_by(ReadTxProjection.account_sync_id)
    ).all()

    # Transfer balance effects: from_account = minus, to_account = plus
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

    # 转账手续费/优惠对余额的增量:SQL 已计入基础转账额,这里叠加 fee 部分
    # (deduct_from_source→转出方再 -fee;deduct_from_target→到账方再 -fee;
    #  separate_account→手续费账户 -fee;discount→转出方 +fee 少扣)。
    #  无 fee 的转账不产生行。
    fee_rows = db.execute(
        select(
            ReadTxProjection.from_account_sync_id,
            ReadTxProjection.to_account_sync_id,
            ReadTxProjection.transfer_fee_json,
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "transfer",
            ReadTxProjection.transfer_fee_json.is_not(None),
        )
    ).all()
    for fa, ta, fee_json in fee_rows:
        for acc, delta in _transfer_fee_only_deltas(fee_json, fa, ta).items():
            if not acc:
                continue
            bucket = stats.setdefault(
                acc, {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0}
            )
            bucket["balance"] = float(bucket["balance"]) + delta

    # 合并付款 legs:每条腿账户扣 -leg.amount(余额 + 支出额都按腿分摊),
    # 主账户不再全额计(已在 main_stats 排除)。legs 可能仅带 accountName(导入),
    # 故用 name_to_id 反查 syncId。
    target_user_id = user_id if (is_admin and user_id) else current_user.id
    _agg_name_to_id = _build_name_to_id(db, target_user_id)
    leg_rows = db.execute(
        select(
            ReadTxProjection.payment_legs_json,
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.tx_type == "expense",
            ReadTxProjection.payment_legs_json.is_not(None),
        )
    ).all()
    for (legs_json,) in leg_rows:
        for acc, delta in _expense_leg_deltas(legs_json, _agg_name_to_id).items():
            if not acc:
                continue
            bucket = stats.setdefault(
                acc, {"count": 0, "income": 0.0, "expense": 0.0, "balance": 0.0}
            )
            bucket["balance"] = float(bucket["balance"]) + delta
            bucket["expense"] = float(bucket["expense"]) + (-delta)
            bucket["count"] = int(bucket["count"]) + 1

    # 「支出统计不含已退款」—— 卡维度口径(与流水列表删除线、交易详情同口径):
    # 只扣**退回本卡**的部分。跨卡退款(统一退到别的卡 / split 退到其他卡)
    # 钱没回这张卡,本卡支出仍然成立。账户维度读原币 amount,退款额与原单
    # 同币种,直接减、不折本位币。合并付款按腿账户各扣各的;无账户原单
    # (脏数据)不退任何卡。balance 不动 —— 余额是流水口径,退款不是交易。
    refund_rows = _refunded_expense_tx_rows(
        db, ledger_ids=ledger_internal_ids, received_only=True
    )
    if refund_rows:
        _refund_totals, refund_by_account = _refund_totals_for_txs(
            db,
            ledger_ids=ledger_internal_ids,
            tx_ids=[r.sync_id for r in refund_rows],
            received_only=True,
        )
        for row in refund_rows:
            if row.payment_legs_json:
                # 合并付款:该单支出按腿计入各卡,退款也按腿退回各卡。
                for acc, _delta in _expense_leg_deltas(
                    row.payment_legs_json, _agg_name_to_id
                ).items():
                    back = refund_by_account.get((row.sync_id, acc), 0.0)
                    if not acc or back <= 0:
                        continue
                    bucket = stats.get(acc)
                    if bucket is not None:
                        bucket["expense"] = max(
                            0.0, float(bucket["expense"]) - back)
            elif row.account_sync_id:
                back = refund_by_account.get(
                    (row.sync_id, str(row.account_sync_id)), 0.0)
                if back > 0:
                    bucket = stats.get(str(row.account_sync_id))
                    if bucket is not None:
                        bucket["expense"] = max(
                            0.0, float(bucket["expense"]) - back)

    # user-global 重构:account 是 per-user 表,直接按 user_id 拉,不再 per-ledger
    # 重复存 + dedup。target_user 是 admin 模式下指定的 user_id,否则 caller。
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
    for acct in db.scalars(account_query).all():
        name = (acct.name or "").strip()
        if not name:
            continue
        sync_id = acct.sync_id
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
                card_number=acct.card_number,
                icon=acct.icon,
                # 别名 + 出账日计入当期(0027),同 ledgers 读端的兜底。
                alias=acct.alias,
                billing_day_in_current=bool(acct.billing_day_in_current),
                hidden=acct.hidden,
                tx_count=tx_count,
                income_total=income_total,
                expense_total=expense_total,
                balance=init_bal + movement,
            )
        )

    # Sort by name, then paginate
    all_accounts.sort(key=lambda a: (a.name or "").lower())
    return all_accounts[offset : offset + limit]


@router.get(
    "/workspace/time-deposits",
    response_model=list[ReadTimeDepositOut],
)
def list_workspace_time_deposits(
    ledger_id: str | None = Query(default=None, description="按账本过滤;不传 = 全部可见账本"),
    status: str | None = Query(default=None, description="holding / matured / withdrawn"),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadTimeDepositOut]:
    """跨账本定期存款只读列表(0021,资产页净资产 / 资产构成并入定期存款用)。

    资产页账户是 **工作区全局** 口径(`/read/workspace/accounts` 不传 ledger_id
    即跨账本),定期存款必须同口径跨账本查询,否则存入定期后账户余额被扣、定期又
    没算进资产,总资产凭空少了本金。派生字段(预期利息 / 剩余天数 / 是否到期 /
    已收利息)按今天现算,与按账本端点 `list_time_deposits` 同口径。
    """
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin, ledger_id=ledger_id
    )
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {
        l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers
    }

    query = select(ReadTimeDepositProjection).where(
        ReadTimeDepositProjection.ledger_id.in_(ledger_internal_ids)
    )
    if status:
        query = query.where(ReadTimeDepositProjection.status == status)
    # 起息日倒序 + 新建在前;同日起息按 sync_id 稳定排序。
    rows = db.scalars(
        query.order_by(
            ReadTimeDepositProjection.start_date.desc(),
            ReadTimeDepositProjection.sync_id.asc(),
        )
    ).all()

    # 已收利息(0030):关联交易中 tx_type='income'(利息)的金额合计与笔数,按
    # (ledger_id, time_deposit_sync_id) 现算 —— 跨账本需带上 ledger_id 维度。
    linked_rows = db.execute(
        select(
            ReadTxProjection.ledger_id,
            ReadTxProjection.time_deposit_sync_id,
            func.sum(
                func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
            ),
            func.count(),
        )
        .where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            ReadTxProjection.time_deposit_sync_id.is_not(None),
            ReadTxProjection.tx_type == "income",
        )
        .group_by(
            ReadTxProjection.ledger_id, ReadTxProjection.time_deposit_sync_id
        )
    ).all()
    interest_received: dict[tuple[str, str], float] = {}
    interest_tx_count: dict[tuple[str, str], int] = {}
    for lid, sid, total, n in linked_rows:
        interest_received[(lid, sid)] = float(total or 0.0)
        interest_tx_count[(lid, sid)] = int(n or 0)

    results: list[ReadTimeDepositOut] = []
    for row in rows:
        derived = time_deposit_derived(
            principal=float(row.principal or 0.0),
            annual_rate=float(row.annual_rate or 0.0),
            start_date=row.start_date,
            maturity_date=row.maturity_date,
            status=row.status or "holding",
            actual_interest=row.actual_interest,
            interest_mode=row.interest_mode or "simple",
        )
        # 到息计划(0028,多笔):坏 JSON → 当无计划,不让脏数据炸列表。
        payout_plans_raw: list[dict[str, Any]] | None = None
        if row.payout_plans_json:
            try:
                parsed = json.loads(row.payout_plans_json)
                if isinstance(parsed, list):
                    payout_plans_raw = parsed
            except (json.JSONDecodeError, TypeError):
                payout_plans_raw = None
        plans = time_deposit_payout_plans_derived(payout_plans_raw)
        ext_id, ledger_name = ledger_meta[row.ledger_id]
        results.append(
            ReadTimeDepositOut(
                id=row.sync_id,
                principal=float(row.principal or 0.0),
                currency=row.currency or "CNY",
                annual_rate=float(row.annual_rate or 0.0),
                term_months=int(row.term_months or 12),
                start_date=_to_utc(row.start_date),
                maturity_date=_to_utc(row.maturity_date),
                status=row.status or "holding",
                account_id=row.account_sync_id,
                account_name=row.account_name,
                auto_renew=bool(row.auto_renew),
                interest_mode=row.interest_mode or "simple",
                note=row.note,
                withdrawn_at=_to_utc(row.withdrawn_at) if row.withdrawn_at else None,
                actual_interest=row.actual_interest,
                expected_interest=derived["expected_interest"],
                days_remaining=derived["days_remaining"],
                is_matured=derived["is_matured"],
                payout_plans=[
                    TimeDepositPayoutPlanOut(**plan) for plan in plans["plans"]
                ],
                payout_plans_total=plans["total"],
                next_payout_date=plans["next_payout_date"],
                interest_received=interest_received.get(
                    (row.ledger_id, row.sync_id), 0.0
                ),
                interest_tx_count=interest_tx_count.get(
                    (row.ledger_id, row.sync_id), 0
                ),
                last_change_id=_get_latest_change_id(db, ledger_id=row.ledger_id),
                ledger_id=ext_id,
                ledger_name=ledger_name,
            )
        )
    return results


@router.get(
    "/workspace/loans",
    response_model=list[ReadLoanOut],
)
def list_workspace_loans(
    ledger_id: str | None = Query(default=None, description="按账本过滤;不传 = 全部可见账本"),
    status: str | None = Query(default=None, description="active / repaid / cancelled"),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadLoanOut]:
    """跨账本借款只读列表(0023,资产页净资产 / 资产构成并入借款用)。

    资产页账户是 **工作区全局** 口径(`/read/workspace/accounts` 不传 ledger_id
    即跨账本),借款必须同口径跨账本查询,否则借出(应收资产)/ 借入(应付负债)
    的头寸无法并入净资产。派生字段(repay_progress / days_to_due /
    is_overdue)按今天现算,与按账本端点 `list_loans` 同口径。
    """
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin, ledger_id=ledger_id
    )
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]
    ledger_meta = {
        l.id: (l.external_id, _resolve_ledger_name(db, ledger=l)) for l in ledgers
    }

    query = select(ReadLoanProjection).where(
        ReadLoanProjection.ledger_id.in_(ledger_internal_ids)
    )
    if status:
        query = query.where(ReadLoanProjection.status == status)
    rows = db.scalars(
        query.order_by(
            ReadLoanProjection.started_at.desc(),
            ReadLoanProjection.sync_id.asc(),
        )
    ).all()

    results: list[ReadLoanOut] = []
    for row in rows:
        derived = loan_derived(
            principal=float(row.principal or 0.0),
            repaid_amount=float(row.repaid_amount or 0.0),
            due_at=row.due_at,
            status=row.status or "active",
        )
        ext_id, ledger_name = ledger_meta[row.ledger_id]
        results.append(
            ReadLoanOut(
                id=row.sync_id,
                direction=row.direction or "borrowed",
                counterparty=row.counterparty or "",
                principal=float(row.principal or 0.0),
                currency=row.currency or "CNY",
                interest_rate=float(row.interest_rate or 0.0),
                started_at=_to_utc(row.started_at),
                due_at=_to_utc(row.due_at) if row.due_at else None,
                repaid_amount=float(row.repaid_amount or 0.0),
                status=row.status or "active",
                account_id=row.account_sync_id,
                account_name=row.account_name,
                note=row.note,
                repay_progress=derived["repay_progress"],
                days_to_due=derived["days_to_due"],
                is_overdue=derived["is_overdue"],
                last_change_id=_get_latest_change_id(db, ledger_id=row.ledger_id),
                ledger_id=ext_id,
                ledger_name=ledger_name,
            )
        )
    return results


@router.get(
    "/workspace/reimbursements",
    response_model=list[ReadReimbursementOut],
)
def list_workspace_reimbursements(
    ledger_id: str | None = Query(default=None, description="按账本过滤;不传 = 全部可见账本"),
    status: str | None = Query(default=None, description="pending / reimbursed / cancelled"),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReadReimbursementOut]:
    """跨账本报销只读列表(0023,资产页净资产 / 资产构成并入待报销用)。

    资产页账户是 **工作区全局** 口径(`/read/workspace/accounts` 不传 ledger_id
    即跨账本),报销(待报销 = 应收资产头寸)必须同口径跨账本查询,才能并入净资产。
    与按账本端点 `list_reimbursements` 同口径。
    """
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin, ledger_id=ledger_id
    )
    if not ledgers:
        return []
    ledger_internal_ids = [l.id for l in ledgers]

    query = select(ReadReimbursementProjection).where(
        ReadReimbursementProjection.ledger_id.in_(ledger_internal_ids)
    )
    if status:
        query = query.where(ReadReimbursementProjection.status == status)
    rows = db.scalars(
        query.order_by(
            ReadReimbursementProjection.happened_at.desc(),
            ReadReimbursementProjection.sync_id.asc(),
        )
    ).all()

    results: list[ReadReimbursementOut] = []
    for row in rows:
        results.append(
            ReadReimbursementOut(
                id=row.sync_id,
                title=row.title or "",
                payer=row.payer,
                reimburser=row.reimburser,
                amount=float(row.amount or 0.0),
                currency=row.currency or "CNY",
                category_id=row.category_sync_id,
                category_name=row.category_name,
                happened_at=_to_utc(row.happened_at),
                status=row.status or "pending",
                account_id=row.account_sync_id,
                account_name=row.account_name,
                related_tx_id=row.related_tx_id,
                note=row.note,
                last_change_id=_get_latest_change_id(db, ledger_id=row.ledger_id),
            )
        )
    return results


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
                parent_sync_id=cat.parent_sync_id,
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

    def _matched_tag_ids(tag_ids_json: Any, tags_csv: Any) -> set[str]:
        matched: set[str] = set()
        if tag_ids_json:
            try:
                raw_tag_ids = json.loads(tag_ids_json)
                if isinstance(raw_tag_ids, list):
                    for tid in raw_tag_ids:
                        tid_s = str(tid).strip()
                        if tid_s and tid_s in tag_id_to_stats:
                            matched.add(tid_s)
            except json.JSONDecodeError:
                pass
        if not matched and tags_csv:
            for part in str(tags_csv).split(","):
                key = part.strip().lower()
                if key and key in tag_name_to_id:
                    matched.add(tag_name_to_id[key])
        return matched

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
        matched_ids = _matched_tag_ids(tag_ids_json, tags_csv)
        amt = float(amount or 0.0)
        for tid in matched_ids:
            slot = tag_id_to_stats[tid]
            slot["count"] += 1.0
            if tx_type_val == "expense":
                slot["expense"] += amt
            elif tx_type_val == "income":
                slot["income"] += amt

    # 「支出统计不含已退款」—— 标签是全局维度(不看卡),扣退款维度
    # refunded_total(折本位币),与 analytics / 账本卡片同口径。只捞有退款的
    # 原单行;tx_count 不变(笔数记真实交易)。
    refund_rows = _refunded_expense_tx_rows(
        db, ledger_ids=ledger_internal_ids, received_only=True
    )
    if refund_rows:
        refund_totals, _refund_by_account = _refund_totals_for_txs(
            db,
            ledger_ids=ledger_internal_ids,
            tx_ids=[r.sync_id for r in refund_rows],
            received_only=True,
        )
        for row in refund_rows:
            if row.exclude_from_stats:
                continue
            refunded = refund_totals.get(row.sync_id, 0.0)
            if refunded <= 0:
                continue
            refunded_native = _refund_native(
                refunded,
                amount=float(row.amount or 0.0),
                native_amount=row.native_amount,
            )
            if refunded_native <= 0:
                continue
            for tid in _matched_tag_ids(row.tag_sync_ids_json, row.tags_csv):
                slot = tag_id_to_stats[tid]
                slot["expense"] = max(0.0, slot["expense"] - refunded_native)
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
            ReadTxProjection.sync_id,
            ReadTxProjection.tx_type,
            # 账本维度折本位币口径(0018):native_amount ?? amount。
            func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount),
            ReadTxProjection.happened_at,
            ReadTxProjection.category_name,
        ).where(
            ReadTxProjection.ledger_id.in_(ledger_internal_ids),
            # exclude_from_stats=True 的交易不计入收支统计(D1);该端点所有
            # 数字(income/expense 汇总、series、分类排行、anomaly)都源自这一
            # 查询,故在此一处过滤即覆盖全部收支口径。余额/净值口径在
            # workspace_net_worth_history 端点,不受此过滤影响。
            # 支出还要再扣「已退款」——见下方 refund_rows 段(用户需求:
            # 支出统计不含已退款的支出金额)。
            ReadTxProjection.exclude_from_stats == sa_false(),
        )
        if start_at is not None:
            tx_query = tx_query.where(ReadTxProjection.happened_at >= start_at)
        if end_at is not None:
            tx_query = tx_query.where(ReadTxProjection.happened_at < end_at)

        # 用本地时区折算的日期算"记账天数",跟 _bucket_key 同步;否则东半球用户在
        # 本地 0-8 点记的笔会被算到前一天的 distinct_days,跟日历视图不一致。
        from datetime import timedelta as _td

        for _sync_id, tx_type_val, amount, happened_at_raw, cat_name in db.execute(tx_query).all():
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
            bucket = _bucket_key(scope, happened_at, tz_offset_minutes, month_start_day)
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
                # 同步累加 per-bucket category → anomaly 归因输入
                bucket_cat = category_by_bucket.setdefault(bucket, {})
                bucket_cat[category] = bucket_cat.get(category, 0.0) + amt

        # 「支出统计不含已退款」(全局/账本维度 → 退款维度 refunded_total):
        # 对**周期内**的支出原单扣有效退款(折本位币),汇总 / series / 分类
        # 排行 / anomaly 归因四处口径一致。transaction_count / distinct_days
        # 不变 —— 笔数与记账天数记录的是真实发生过的交易(D1 同款语义)。
        # 只捞有退款的原单行,无退款的账本零额外查询。
        refund_rows = _refunded_expense_tx_rows(
            db, ledger_ids=ledger_internal_ids, received_only=True
        )
        if refund_rows:
            refund_totals, _refund_by_account = _refund_totals_for_txs(
                db,
                ledger_ids=ledger_internal_ids,
                tx_ids=[r.sync_id for r in refund_rows],
                received_only=True,
            )
            for refund_row in refund_rows:
                if refund_row.exclude_from_stats:
                    continue
                refund_happened_at = _to_utc(refund_row.happened_at) if refund_row.happened_at else None
                if refund_happened_at is None:
                    continue
                if start_at is not None and refund_happened_at < start_at:
                    continue
                if end_at is not None and refund_happened_at >= end_at:
                    continue
                refunded = refund_totals.get(refund_row.sync_id, 0.0)
                if refunded <= 0:
                    continue
                refunded_native = _refund_native(
                    refunded,
                    amount=float(refund_row.amount or 0.0),
                    native_amount=refund_row.native_amount,
                )
                if refunded_native <= 0:
                    continue
                bucket = _bucket_key(
                    scope, refund_happened_at, tz_offset_minutes, month_start_day)
                category = (refund_row.category_name or "").strip() or "Uncategorized"
                # 各 slot 必然已由上面的主循环创建(该原单被计入过),直接扣。
                series_map[bucket]["expense"] = max(
                    0.0, series_map[bucket]["expense"] - refunded_native)
                category_map[category]["expense"] = max(
                    0.0, category_map[category]["expense"] - refunded_native)
                category_by_bucket[bucket][category] = max(
                    0.0, category_by_bucket[bucket][category] - refunded_native)
                expense_total = max(0.0, expense_total - refunded_native)

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

    accts = db.execute(
        select(
            UserAccountProjection.sync_id,
            UserAccountProjection.account_type,
            UserAccountProjection.currency,
            UserAccountProjection.initial_balance,
            UserAccountProjection.name,
        ).where(UserAccountProjection.user_id == current_user.id)
    ).all()
    init_by_acc = {a.sync_id: float(a.initial_balance or 0.0) for a in accts}
    nw_name_to_id = {
        (a.name or "").strip().lower(): a.sync_id
        for a in accts if a.sync_id and a.name
    }
    is_credit = {a.sync_id: a.account_type == "credit_card" for a in accts}
    is_liab = {a.sync_id: (a.account_type in ("credit_card", "loan")) for a in accts}
    acc_currency = {a.sync_id: (a.currency or "CNY").upper() for a in accts}
    currencies = {(a.currency or "CNY").upper() for a in accts if a.sync_id in init_by_acc}
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

    txs = db.execute(
        select(
            ReadTxProjection.tx_type,
            ReadTxProjection.amount,
            ReadTxProjection.happened_at,
            ReadTxProjection.account_sync_id,
            ReadTxProjection.from_account_sync_id,
            ReadTxProjection.to_account_sync_id,
            ReadTxProjection.transfer_fee_json,
            ReadTxProjection.payment_legs_json,
        )
        .where(ReadTxProjection.ledger_id.in_(ledger_internal_ids))
        .order_by(ReadTxProjection.happened_at.asc())
    ).all()

    bal = dict(init_by_acc)

    def _apply(tx_type, amt, acc, from_acc, to_acc, fee_json=None, legs_json=None):
        if tx_type == "income" and acc in bal:
            bal[acc] += amt
        elif tx_type == "expense":
            # 合并付款:按各腿账户分摊扣;否则主账户扣全额。
            leg_deltas = _expense_leg_deltas(legs_json, nw_name_to_id)
            if leg_deltas:
                for a, delta in leg_deltas.items():
                    if a in bal:
                        bal[a] += delta
            elif acc in bal:
                bal[acc] -= amt
        elif tx_type == "transfer":
            # 含手续费:按 mode 折算转出/到账/手续费账户的余额增量。
            for a, delta in _transfer_account_deltas(
                amt, from_acc or acc, to_acc, fee_json
            ).items():
                if a in bal:
                    bal[a] += delta

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
            if is_liab.get(k, False) and not (is_credit.get(k, False) and vb > 0):
                liab += vb
            else:
                assets += vb
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
            tx.account_sync_id, tx.from_account_sync_id, tx.to_account_sync_id,
            tx.transfer_fee_json, tx.payment_legs_json,
        )
        last_bucket = bucket
    if last_bucket is not None:
        a, l = _net()
        series.append(NetWorthHistorySeriesItemOut(
            bucket=last_bucket, net_worth=a + l, assets=a, liabilities=l,
        ))
    if not series and init_by_acc:
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
