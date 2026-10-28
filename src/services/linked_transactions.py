"""借款 / 报销 自动关联交易(0024)。

背景:借款(借入 / 借出 / 归还 / 收回)、报销(到账)、定期存款(存入 / 支取)
原先只是独立的投影实体,**不会**产生交易流水(ReadTxProjection),也不**不会**
改动任何账户余额。用户期望这些资金真实变动要在「账户流水 + 余额」里体现
(issue:借入 / 归还应有流水且对应账户金额变动;定期存款存入应从原账户扣款)。

做法:每当一条 loan / reimbursement 被写入(且选了账户),按确定性 syncId 生成 /
更新 / 删除对应的关联交易:

- 借款 borrowed(借入):本金 → income(钱进账户);归还 → expense(钱出账户)。
- 借款 lent(借出):本金 → expense(钱出账户);收回 → income(钱进账户)。
- 报销 reimbursed(到账):本金 → income(钱进账户);pending / cancelled 无流水。
- 定期存款:本金存入 → transfer(from = 原账户,钱搬进定期);支取 → transfer
  (to = 原账户,本息搬回)。存定期是资产搬家、不是花掉也不是赚到,所以走转账;
  定期本身不是账户,故转账另一侧留空。

关联交易打 ``excludeFromStats=True``(且不进预算),这样:
- 账户余额 / 流水(run_balance / workspace transactions)包含它们 → 余额真实
  变动、流水可见;
- 收支统计(income_total / expense_total)排除它们 → 不把借款本金伪装成收入。

确定性 syncId(``<entityId>::ln-principal`` / ``::ln-repay`` / ``::rb-tx``)保证
upsert 幂等:重复 reconcile 只会更新同一行,不会增生重复流水。删除实体或清空
账户 / 取消状态时,清理掉对应关联交易(含 SyncChange,保证 mobile 同步删除)。

本模块函数必须在调用方已持 ledger 写锁且同一 DB 事务内调用(在
``_commit_entity_write._core`` 里、``db.commit()`` 之前调用),保证原子性。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..models import ReadTxProjection, SyncChange
from .. import projection

logger = logging.getLogger(__name__)

# 关联交易 syncId 后缀(拼在实体 syncId 后面,保证全局唯一且可反推)。
_LN_PRINCIPAL_SUFFIX = "::ln-principal"
_LN_REPAY_SUFFIX = "::ln-repay"
_RB_TX_SUFFIX = "::rb-tx"
_TD_DEPOSIT_SUFFIX = "::td-deposit"
_TD_WITHDRAW_SUFFIX = "::td-withdraw"
# 退款(0025):统一退一笔 tx,分别退按腿编号 rf-leg-<i>。
_RF_TX_SUFFIX = "::rf-tx"
_RF_LEG_PREFIX = "::rf-leg-"


def refund_entity_id_from_tx_sync_id(tx_sync_id: str) -> str | None:
    """关联交易 syncId → 退款实体 syncId;非退款关联交易返回 None。

    反向解析写端拼出来的 ``<refund_id>::rf-tx`` / ``<refund_id>::rf-leg-N``,
    供读端点反查「这笔退款到账退的是哪张原单」。跟后缀常量同处定义,
    避免别处重复硬编码字符串。
    """
    if not tx_sync_id:
        return None
    if tx_sync_id.endswith(_RF_TX_SUFFIX):
        return tx_sync_id[: -len(_RF_TX_SUFFIX)]
    idx = tx_sync_id.rfind(_RF_LEG_PREFIX)
    if idx > 0:
        return tx_sync_id[:idx]
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    source_change_id: int,
    tx_sync_id: str,
    tx_type: str,
    amount: float,
    account_id: str,
    account_name: str | None,
    happened_at: str,
    currency: str,
    note: str,
    category_id: str | None = None,
    category_name: str | None = None,
    from_account_id: str | None = None,
    from_account_name: str | None = None,
    to_account_id: str | None = None,
    to_account_name: str | None = None,
) -> None:
    """写入一条关联交易(income / expense / transfer)。幂等:同 syncId 走 upsert。

    三组账户字段(account / from / to)**每次都显式写入**(不适用的写 None):
    projection 只在 key 存在时覆盖,缺键会保留上一版的值 —— 实体从「支出」
    改成「转账」时必须把旧的 accountId 清掉,否则账户余额会被重复计算。
    """
    item: dict[str, Any] = {
        "syncId": tx_sync_id,
        "type": tx_type,
        "amount": float(amount),
        "accountId": account_id,
        "accountName": account_name,
        "fromAccountId": from_account_id,
        "fromAccountName": from_account_name,
        "toAccountId": to_account_id,
        "toAccountName": to_account_name,
        "happenedAt": happened_at,
        "note": note,
        "currencyCode": currency,
        # 标记笔:进余额 / 流水,但不进收支统计与预算(借款本金不是真收入)。
        "excludeFromStats": True,
        "excludeFromBudget": True,
        "txIndex": 0,
    }
    if category_id:
        item["categoryId"] = category_id
        item["categoryName"] = category_name

    now = _utcnow()
    change_row = SyncChange(
        user_id=owner_user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx_sync_id,
        action="upsert",
        payload_json=item,
        updated_at=now,
        updated_by_device_id=device_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_tx(
        db,
        ledger_id=ledger_id,
        user_id=owner_user_id,
        source_change_id=change_row.change_id,
        payload=item,
    )


def _delete_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    tx_sync_id: str,
) -> None:
    """删除一条关联交易(若存在)。同时发 SyncChange(delete)给 mobile。"""
    existing = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == tx_sync_id,
        )
    )
    if existing is None:
        return
    now = _utcnow()
    change_row = SyncChange(
        user_id=owner_user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx_sync_id,
        action="delete",
        payload_json={},
        updated_at=now,
        updated_by_device_id=device_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(change_row)
    db.flush()
    projection.delete_tx(db, ledger_id=ledger_id, sync_id=tx_sync_id)


def reconcile_loan_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    source_change_id: int,
    action: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    """借款实体 → 关联交易(本金 + 已还)的增删改。"""
    principal_tx_id = f"{entity_id}{_LN_PRINCIPAL_SUFFIX}"
    repay_tx_id = f"{entity_id}{_LN_REPAY_SUFFIX}"

    if action == "delete":
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=principal_tx_id,
        )
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=repay_tx_id,
        )
        return

    direction = str(payload.get("direction") or "borrowed").strip().lower()
    borrowed = direction != "lent"  # 缺省 borrowed
    principal = float(payload.get("principal") or 0.0)
    repaid = float(payload.get("repaidAmount") or 0.0)
    account_id = payload.get("accountId")
    account_name = payload.get("accountName")
    counterparty = str(payload.get("counterparty") or "").strip()
    currency = str(payload.get("currency") or "CNY").strip().upper() or "CNY"
    happened_at = payload.get("startedAt") or payload.get("happenedAt")
    status = str(payload.get("status") or "active").strip().lower()
    note = payload.get("note") or ""

    # 没选账户,或已取消 → 清理所有关联交易(钱没进任何账户)。
    if not account_id or status == "cancelled":
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=principal_tx_id,
        )
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=repay_tx_id,
        )
        return

    # 本金流水:借入=收入,借出=支出。
    principal_type = "income" if borrowed else "expense"
    principal_label = "借入" if borrowed else "借出"
    principal_note = f"{principal_label}-{counterparty}" + (f" ({note})" if note else "")
    _upsert_linked_tx(
        db, ledger_id=ledger_id, owner_user_id=owner_user_id,
        actor_user_id=actor_user_id, device_id=device_id,
        source_change_id=source_change_id, tx_sync_id=principal_tx_id,
        tx_type=principal_type, amount=principal, account_id=account_id,
        account_name=account_name, happened_at=happened_at,
        currency=currency, note=principal_note,
    )

    # 已还流水:借入的归还是支出,借出的收回是收入。repaid<=0 视为还没还,清理。
    if repaid > 0:
        repay_type = "expense" if borrowed else "income"
        repay_label = "归还" if borrowed else "收回"
        repay_note = f"{repay_label}-{counterparty}" + (f" ({note})" if note else "")
        _upsert_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, tx_sync_id=repay_tx_id,
            tx_type=repay_type, amount=repaid, account_id=account_id,
            account_name=account_name, happened_at=happened_at,
            currency=currency, note=repay_note,
        )
    else:
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=repay_tx_id,
        )


def reconcile_reimbursement_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    source_change_id: int,
    action: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    """报销实体 → 关联交易(到账)的增删改。

    语义:我先垫付、后报销。垫付通常是用户自己记的一笔支出交易
    (relatedTxId 弱引用),**不**在这里重复生成;这里只在 status=reimbursed
    (钱回到账户)时生成一条收入流水,让账户余额 / 流水体现这笔到账。
    """
    tx_id = f"{entity_id}{_RB_TX_SUFFIX}"

    if action == "delete":
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=tx_id,
        )
        return

    amount = float(payload.get("amount") or 0.0)
    account_id = payload.get("accountId")
    account_name = payload.get("accountName")
    status = str(payload.get("status") or "pending").strip().lower()
    currency = str(payload.get("currency") or "CNY").strip().upper() or "CNY"
    happened_at = payload.get("happenedAt")
    title = str(payload.get("title") or "").strip()
    note = payload.get("note") or ""
    category_id = payload.get("categoryId")
    category_name = payload.get("categoryName")

    # 没选账户,或未到账(reimbursed)→ 清理关联交易(钱还没进账户)。
    if not account_id or status != "reimbursed":
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=tx_id,
        )
        return

    rb_note = f"报销到账-{title}" + (f" ({note})" if note else "")
    _upsert_linked_tx(
        db, ledger_id=ledger_id, owner_user_id=owner_user_id,
        actor_user_id=actor_user_id, device_id=device_id,
        source_change_id=source_change_id, tx_sync_id=tx_id,
        tx_type="income", amount=amount, account_id=account_id,
        account_name=account_name, happened_at=happened_at,
        currency=currency, note=rb_note,
        category_id=category_id, category_name=category_name,
    )


def reconcile_time_deposit_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    source_change_id: int,
    action: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    """定期存款实体 → 关联交易(存入 / 支取)的增删改。

    语义(对齐用户诉求「存入定期应从原账户扣除存款金额」):
    - 只要存款存在(status ∈ holding/matured/withdrawn),就在 ``startDate`` 生成
      一笔 **转账**:``from`` = 原账户(accountId),把本金从原账户扣出 →
      账户余额真实减少。
    - 仅在 ``status=withdrawn``(支取)时,在 ``withdrawnAt`` 再生成一笔 **转账**:
      ``to`` = 原账户,把本金 + 实得利息转回 → 账户余额真实增加。
      两个状态都保留,完整反映「钱出去 → 钱回来(带息)」的真实资金流。

    为什么是转账而不是支出 / 收入:存定期只是把钱从「账户」搬到「定期存款」
    这项资产,钱没有真的花掉、也不是赚来的,所以流水里必须显示 **转账**,
    不能污染收支。

    定期存款**不是账户**,所以转账另一侧(from 或 to)留空,读出来显示
    「现金 → -」/「- → 现金」,语义由 note(存入定期 / 定期支取)表达。
    注意:绝不能给空侧填一个账户名 —— projection 会按名反查成真实账户
    sync_id,凭空改余额。

    两笔都打 ``excludeFromStats``(不进收支统计 / 预算)。
    转账的余额增量由 ``_transfer_account_deltas`` 按 from/to 计算,因此
    accountId(普通账户列)必须留空,否则部分代码路径会把它当转出方重复扣一次。
    """
    deposit_tx_id = f"{entity_id}{_TD_DEPOSIT_SUFFIX}"
    withdraw_tx_id = f"{entity_id}{_TD_WITHDRAW_SUFFIX}"

    if action == "delete":
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=deposit_tx_id,
        )
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=withdraw_tx_id,
        )
        return

    account_id = payload.get("accountId")
    account_name = payload.get("accountName")
    principal = float(payload.get("principal") or 0.0)
    status = str(payload.get("status") or "holding").strip().lower()
    currency = str(payload.get("currency") or "CNY").strip().upper() or "CNY"
    start_date = payload.get("startDate")
    withdrawn_at = payload.get("withdrawnAt")
    actual_interest = float(payload.get("actualInterest") or 0.0)
    note = payload.get("note") or ""

    # 没选原账户 / 本金 <= 0 → 清理所有关联交易(钱没离开任何账户)。
    if not account_id or principal <= 0:
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=deposit_tx_id,
        )
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=withdraw_tx_id,
        )
        return

    # 存入流水:本金从原账户转进定期(转账,from = 原账户),发生在 start_date。
    # 只要存款存在(holding/matured/withdrawn)即保留这笔真实资金流出。
    deposit_label = account_name or "原账户"
    deposit_note = f"存入定期-{deposit_label}" + (f" ({note})" if note else "")
    _upsert_linked_tx(
        db, ledger_id=ledger_id, owner_user_id=owner_user_id,
        actor_user_id=actor_user_id, device_id=device_id,
        source_change_id=source_change_id, tx_sync_id=deposit_tx_id,
        tx_type="transfer", amount=principal,
        account_id=None, account_name=None,
        from_account_id=account_id, from_account_name=account_name,
        to_account_id=None, to_account_name=None,
        happened_at=start_date,
        currency=currency, note=deposit_note,
    )

    # 支取流水:本息从定期转回原账户(转账,to = 原账户),发生在 withdrawn_at;
    # 仅 status=withdrawn 时生成。
    if status == "withdrawn":
        back_amount = principal + actual_interest
        withdraw_note = f"定期支取-{deposit_label}" + (f" ({note})" if note else "")
        _upsert_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, tx_sync_id=withdraw_tx_id,
            tx_type="transfer", amount=back_amount,
            account_id=None, account_name=None,
            from_account_id=None, from_account_name=None,
            to_account_id=account_id, to_account_name=account_name,
            happened_at=withdrawn_at,
            currency=currency, note=withdraw_note,
        )
    else:
        _delete_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            tx_sync_id=withdraw_tx_id,
        )


def reconcile_refund_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    source_change_id: int,
    action: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    """退款实体 -> 关联交易(到账收入)的增删改(0025)。

    语义:退款 status=received(已到账)时,钱回到账户 -> 生成 income 流水,
    让账户余额 / 流水真实体现这笔退款;pending / cancelled 不改动余额。

    - unified:全额退到顶层 accountId(单一卡)。
    - split:按 refundLegs 分别退到各腿账户(对应原始「两账户分摊」的各张卡)。

    关联交易打 excludeFromStats / excludeFromBudget,不污染收支统计(退款不是收入)。
    删除实体 / 取消 / 未到账 / 没选账户 -> 清理全部关联交易;split 腿数变化 ->
    清理多余孤儿腿。
    """
    if action == "delete":
        _reconcile_refund_linked_tx_cleanup(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            entity_id=entity_id, keep_ids=set(),
        )
        return

    status = str(payload.get("status") or "pending").strip().lower()
    amount = float(payload.get("amount") or 0.0)
    refund_mode = str(payload.get("refundMode") or "unified").strip().lower()
    currency = str(payload.get("currency") or "CNY").strip().upper() or "CNY"
    happened_at = payload.get("happenedAt")
    reason = str(payload.get("reason") or "").strip()
    note = payload.get("note") or ""

    # 仅「已到账」且金额为正才产生入账流水;否则清理全部关联交易。
    if status != "received" or amount <= 0:
        _reconcile_refund_linked_tx_cleanup(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            entity_id=entity_id, keep_ids=set(),
        )
        return

    # 计算「账户 -> 金额」目标列表:[(tx_sync_id, account_id, account_name, amt)]。
    targets: list[tuple[str, str, str | None, float]] = []
    if refund_mode == "split":
        raw_legs = payload.get("refundLegs") or []
        for i, leg in enumerate(raw_legs):
            if not isinstance(leg, dict):
                continue
            aid = leg.get("accountId")
            amt = float(leg.get("amount") or 0.0)
            if not aid or amt <= 0:
                continue
            targets.append(
                (f"{entity_id}{_RF_LEG_PREFIX}{i}", aid,
                 leg.get("accountName"), amt)
            )
    else:
        top_aid = payload.get("accountId")
        if not top_aid:
            # 统一退但没选目标账户 -> 不入账(清理全部)。
            _reconcile_refund_linked_tx_cleanup(
                db, ledger_id=ledger_id, owner_user_id=owner_user_id,
                actor_user_id=actor_user_id, device_id=device_id,
                entity_id=entity_id, keep_ids=set(),
            )
            return
        targets.append(
            (f"{entity_id}{_RF_TX_SUFFIX}", top_aid,
             payload.get("accountName"), amount)
        )

    if not targets:
        _reconcile_refund_linked_tx_cleanup(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            entity_id=entity_id, keep_ids=set(),
        )
        return

    # 先清理本退款实体下、不在 active 集合里的旧关联交易(腿数变化 / 模式切换)。
    active_ids = {t[0] for t in targets}
    _reconcile_refund_linked_tx_cleanup(
        db, ledger_id=ledger_id, owner_user_id=owner_user_id,
        actor_user_id=actor_user_id, device_id=device_id,
        entity_id=entity_id, keep_ids=active_ids,
    )

    for tx_sync_id, aid, aname, amt in targets:
        rf_note = "退款到账" + (f"-{reason}" if reason else "") + (f" ({note})" if note else "")
        _upsert_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, tx_sync_id=tx_sync_id,
            tx_type="income", amount=amt, account_id=aid,
            account_name=aname, happened_at=happened_at,
            currency=currency, note=rf_note,
        )


def _reconcile_refund_linked_tx_cleanup(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    entity_id: str,
    keep_ids: set[str],
) -> None:
    """删除本退款实体下、sync_id 以 '<entity_id>::rf-' 开头且不在 keep_ids 里的
    关联交易(撤销 / 状态变更 / split 腿数变化时的孤儿清理)。keep_ids 为空集合
    即删除全部(实体删除 / 取消 / 未到账 / 没选账户时调用)。"""
    prefix = f"{entity_id}::rf-"
    existing = db.scalars(
        select(ReadTxProjection.sync_id).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id.like(f"{prefix}%"),
        )
    ).all()
    for sid in existing:
        if sid not in keep_ids:
            _delete_linked_tx(
                db, ledger_id=ledger_id, owner_user_id=owner_user_id,
                actor_user_id=actor_user_id, device_id=device_id,
                tx_sync_id=sid,
            )


def reconcile_entity_linked_tx(
    db: Any,
    *,
    ledger_id: str,
    owner_user_id: str,
    actor_user_id: str,
    device_id: str,
    source_change_id: int,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    """统一入口:按 entity_type 派发到上面的借款 / 报销 / 定期存款 / 退款 reconciliation。"""
    if entity_type == "loan":
        reconcile_loan_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, action=action,
            entity_id=entity_id, payload=payload,
        )
    elif entity_type == "reimbursement":
        reconcile_reimbursement_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, action=action,
            entity_id=entity_id, payload=payload,
        )
    elif entity_type == "time_deposit":
        reconcile_time_deposit_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, action=action,
            entity_id=entity_id, payload=payload,
        )
    elif entity_type == "refund":
        reconcile_refund_linked_tx(
            db, ledger_id=ledger_id, owner_user_id=owner_user_id,
            actor_user_id=actor_user_id, device_id=device_id,
            source_change_id=source_change_id, action=action,
            entity_id=entity_id, payload=payload,
        )
    # 其它实体类型:无关联交易,no-op。
