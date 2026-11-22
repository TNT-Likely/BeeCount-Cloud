"""交易流水「该笔之后账户余额」(running balance) 的读时计算。

设计取舍
--------
余额**不落库**：如果存进 read_tx_projection，插入/编辑/删除任何一笔历史交易
都要重算该账户后面所有行，写入链路(fast path 只 emit 一条 SyncChange)会被迫
变成批量更新，且容易出现投影与余额不一致。因此改为读接口按账户时间线
O(n) 现算，结果只挂在响应对象上。

口径与 `read/workspace.py::list_workspace_accounts` 完全一致：
    balance = initial_balance + income - expense - transfer(from) + transfer(to)
转账手续费 / 优惠(0022)与支出优惠(0023)按现有产品口径都是**明细字段**，
不额外生成收支流水，所以不参与余额计算。

返回值：`{tx_sync_id: (primary_balance_after, counter_balance_after)}`
  - primary：expense/income 的 account；transfer 的 from_account
  - counter：仅 transfer 有值，为 to_account 在该笔之后的余额
账户 id 缺失(历史脏数据 / 账户已删)时对应值为 None，前端不展示。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ReadTxProjection, UserAccountProjection
from . import payment_splits as _payment_splits


def _timestamp(value: datetime | None) -> float:
    """SQLite 里 happened_at 可能是 naive datetime，统一按 UTC 解释后取时间戳。"""
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def compute_running_balances(
    db: Session,
    *,
    ledger_ids: list[str],
) -> dict[str, tuple[float | None, float | None]]:
    """算出指定可见账本范围内，每一笔交易发生后其账户的余额。

    只 select 计算所需列；一次全量扫描 + 内存排序，行数在个人账本量级
    (几千条以内)开销可忽略。跨账本时账户按 sync_id 归并(账户是 user-global
    实体)，与账户页聚合逻辑一致。
    """
    if not ledger_ids:
        return {}

    tx_rows = db.execute(
        select(
            ReadTxProjection.sync_id,
            ReadTxProjection.tx_type,
            ReadTxProjection.amount,
            ReadTxProjection.happened_at,
            ReadTxProjection.tx_index,
            ReadTxProjection.account_sync_id,
            ReadTxProjection.from_account_sync_id,
            ReadTxProjection.to_account_sync_id,
            ReadTxProjection.payment_splits_json,
        ).where(ReadTxProjection.ledger_id.in_(ledger_ids))
    ).all()

    # 账户基线余额:账户是 user-global 实体(共享账本/管理员视图下可能不属于
    # caller),所以按本批交易涉及的 sync_id 精确查,不按 user_id 过滤。
    # 查不到(账户已删 / 老数据)时按 0 起算,保证时间序列不被打断。
    acc_ids: set[str] = set()
    for r in tx_rows:
        for acc_id in (
            r.account_sync_id,
            r.from_account_sync_id,
            r.to_account_sync_id,
        ):
            if acc_id:
                acc_ids.add(acc_id)
        # 组合付款(0028):各 split 账户独立参与余额时间线
        for s in _payment_splits.decode_row(getattr(r, "payment_splits_json", None)):
            aid = s.get("account_id")
            if aid:
                acc_ids.add(aid)
    balances: dict[str, float] = {}
    if acc_ids:
        for acc_id, initial in db.execute(
            select(
                UserAccountProjection.sync_id,
                UserAccountProjection.initial_balance,
            ).where(UserAccountProjection.sync_id.in_(acc_ids))
        ).all():
            balances.setdefault(acc_id, float(initial or 0.0))

    def balance_of(acc_id: str | None) -> float:
        if not acc_id:
            return 0.0
        return balances.setdefault(acc_id, 0.0)

    def add(acc_id: str | None, delta: float) -> float | None:
        if not acc_id:
            return None
        balances[acc_id] = balance_of(acc_id) + delta
        return balances[acc_id]

    ordered = sorted(
        tx_rows,
        key=lambda r: (
            _timestamp(r.happened_at),
            int(r.tx_index or 0),
            r.sync_id or "",
        ),
    )

    result: dict[str, tuple[float | None, float | None]] = {}
    seen: set[str] = set()
    for r in ordered:
        # 投影主键是 (ledger_id, sync_id)，极端情况下同一 sync_id 在多个
        # 账本有残留行时只算一次，避免余额被重复累加。
        if not r.sync_id or r.sync_id in seen:
            continue
        seen.add(r.sync_id)

        amount = float(r.amount or 0.0)
        tx_type = r.tx_type or ""
        if tx_type == "transfer":
            from_id = r.from_account_sync_id
            to_id = r.to_account_sync_id
            primary = add(from_id, -amount)
            counter = add(to_id, amount)
        elif tx_type == "income":
            primary = add(r.account_sync_id, amount)
            counter = None
        elif tx_type == "expense":
            ps = _payment_splits.decode_row(getattr(r, "payment_splits_json", None))
            if ps:
                # 组合付款:按拆分分别扣减各账户;主账户余额对该笔无单一归属
                for s in ps:
                    add(s.get("account_id"), -float(s.get("amount") or 0))
                primary = None
            else:
                primary = add(r.account_sync_id, -amount)
            counter = None
        elif tx_type == "refund":
            # 退款(0025):退回接收账户(account_sync_id)余额,冲减原交易分类支出
            # 在 analytics 读时处理;此处只负责接收账户余额回正。
            primary = add(r.account_sync_id, amount)
            counter = None
        else:
            primary, counter = None, None

        # 展示只需要 2 位小数；累积过程保留原精度，避免逐步 round 放大误差。
        result[r.sync_id] = (
            round(primary, 2) if primary is not None else None,
            round(counter, 2) if counter is not None else None,
        )
    return result
