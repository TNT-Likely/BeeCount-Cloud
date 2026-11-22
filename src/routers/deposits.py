"""Web-first REST endpoints for fixed-term Deposits (定期存款).

Server-direct `deposits` / `deposit_interest_payments` tables, sibling of
contacts & loans — intentionally OUTSIDE the mobile sync layer.

资金流(复用借款的流水引擎):
  - 存入:transfer 流「活期账户 → 虚拟定期存款账户」。转账不进收支统计,
    总资产不变(活期 -X / 定期存款虚拟账户 +X,虚拟账户总额由 /summary 实时
    派生,跟应收款/应付款同思路)。
  - 支取:本金 transfer 转回活期 + 利息 income 流(计入统计)。
  - 周期结息:单独 income 流 + deposit_interest_payments 记录。
  - 转存:利息 income + 本金转回 + 新存单重新划转(账面每一步都有流水背书)。

Interest: 到期利息 = 本金 × 年利率 × (存期月数 / 12);interest_override
手动覆盖。到期日 = started_at + term_months(日历月,秒级)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..linked_tx import linked_tx_map as _linked_tx_map
from ..deposit_calc import (
    compute_maturity,
    days_until,
    derive_status,
    expected_interest,
)
from ..deps import get_current_user
from ..models import Deposit, DepositInterestPayment, Ledger, User
from ..projection import delete_tx
from ..schemas_linked import LinkedTxOut
from ..schemas_dep import (
    DepositCreate,
    DepositInterestPaymentOut,
    DepositInterestSettleRequest,
    DepositOut,
    DepositReminderItem,
    DepositRemindersOut,
    DepositRenewRequest,
    DepositSummaryOut,
    DepositUpdate,
    DepositWithdrawRequest,
)
from .contacts_loans import (
    _attachment_refs_from_ids,
    _create_loan_flow_tx,
    _delete_loan_flow_tx,
    _require_ledger,
    _update_loan_flow_tx,
    _utc,
)

deposits_router = APIRouter()

# 虚拟「定期存款」账户:transfer 流的对手方。不在真实账户列表里,余额由
# /deposits/summary 从存单记录实时派生(跟应收款/应付款虚拟账户同一模式)。
VIRTUAL_DEPOSIT_ACCOUNT_ID = "virtual:deposit"
VIRTUAL_DEPOSIT_ACCOUNT_NAME = "定期存款"

# 提醒窗口:到期前 7 天(含当天)算"临期";已到期未处理算"逾期"。
NEAR_MATURITY_DAYS = 7


def _json_loads_safe(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (ValueError, TypeError):
        return []
    return []


def _json_dumps(value: Optional[list[str]]) -> Optional[str]:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def _settled_interest_total(db: Session, deposit_id: str) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(DepositInterestPayment.amount), 0.0)).where(
            DepositInterestPayment.deposit_id == deposit_id
        )
    )
    return float(total or 0.0)


def _settlements_map(db: Session, deposit_ids: list[str]) -> dict[str, float]:
    if not deposit_ids:
        return {}
    rows = db.execute(
        select(
            DepositInterestPayment.deposit_id,
            func.coalesce(func.sum(DepositInterestPayment.amount), 0.0),
        )
        .where(DepositInterestPayment.deposit_id.in_(deposit_ids))
        .group_by(DepositInterestPayment.deposit_id)
    ).all()
    return {dep_id: float(total or 0.0) for dep_id, total in rows}


def _deposit_out(
    dep: Deposit,
    settled: float = 0.0,
    now: Optional[datetime] = None,
    tx_details: Optional[list[LinkedTxOut]] = None,
) -> DepositOut:
    now = now or datetime.now(timezone.utc)
    maturity_at = _utc(dep.maturity_at) if dep.maturity_at else None
    derived = derive_status(dep.status, dep.maturity_at, now)
    interest = expected_interest(dep.principal, dep.annual_rate, dep.term_months, dep.interest_override)
    days: Optional[int] = None
    if maturity_at is not None:
        days = days_until(maturity_at, now)
    return DepositOut(
        id=dep.id,
        user_id=dep.user_id,
        ledger_id=dep.ledger_id,
        name=dep.name,
        deposit_type=dep.deposit_type,  # type: ignore[arg-type]
        principal=dep.principal,
        currency_code=dep.currency_code,
        started_at=dep.started_at,
        term_months=dep.term_months,
        annual_rate=dep.annual_rate,
        interest_override=dep.interest_override,
        maturity_at=dep.maturity_at,
        on_maturity=dep.on_maturity,  # type: ignore[arg-type]
        interest_settlement=dep.interest_settlement,  # type: ignore[arg-type]
        settlement_day=dep.settlement_day,
        status=derived,  # type: ignore[arg-type]
        note=dep.note,
        certificate_file_ids=_json_loads_safe(dep.certificate_file_ids_json),
        account_id=dep.account_id,
        account_name=dep.account_name,
        flow_tx_sync_id=dep.flow_tx_sync_id,
        principal_return_tx_sync_id=dep.principal_return_tx_sync_id,
        interest_tx_sync_id=dep.interest_tx_sync_id,
        tx_sync_details=tx_details or [],
        expected_interest=interest,
        total_at_maturity=round(dep.principal + interest, 2),
        settled_interest=round(settled, 2),
        days_to_maturity=days,
        withdrawn_at=dep.withdrawn_at,
        withdrawn_interest=dep.withdrawn_interest,
        renewed_to_deposit_id=dep.renewed_to_deposit_id,
        parent_deposit_id=dep.parent_deposit_id,
        renewed_at=dep.renewed_at,
        created_at=dep.created_at,
        updated_at=dep.updated_at,
    )


def _deposit_payment_sync_ids(db: Session, dep: Deposit) -> list[str]:
    rows = list(
        db.scalars(
            select(DepositInterestPayment.tx_sync_id).where(
                DepositInterestPayment.deposit_id == dep.id
            )
        ).all()
    )
    return [s for s in rows if s]


def _deposit_tx_details(db: Session, dep: Deposit) -> list[LinkedTxOut]:
    """取存单关联流水的可读摘要(存入 / 本金回流 / 利息 / 结息)(0046)。"""
    sids = [
        dep.flow_tx_sync_id,
        dep.principal_return_tx_sync_id,
        dep.interest_tx_sync_id,
    ] + _deposit_payment_sync_ids(db, dep)
    _tx_map = _linked_tx_map(db, dep.ledger_id, sids)
    out: list[LinkedTxOut] = []
    seen: set[str] = set()
    for sid in sids:
        if sid and sid in _tx_map and sid not in seen:
            seen.add(sid)
            out.append(_tx_map[sid])
    return out


def _get_deposit_or_404(db: Session, user: User, deposit_id: str) -> Deposit:
    dep = db.get(Deposit, deposit_id)
    if dep is None or dep.user_id != user.id:
        raise HTTPException(status_code=404, detail="deposit not found")
    return dep


# --------------------------------------------------------------------------
# flow payload builders(喂给借款模块的流水引擎)
# --------------------------------------------------------------------------


def _build_deposit_flow_payload(
    *,
    dep: Deposit,
    account_id: Optional[str],
    account_name: Optional[str],
) -> dict:
    """存入划转:活期 → 虚拟定期存款账户(transfer,不进收支统计)。"""
    return {
        "tx_type": "transfer",
        "amount": dep.principal,
        "currency_code": dep.currency_code,
        "happened_at": dep.started_at.isoformat()
        if isinstance(dep.started_at, datetime)
        else dep.started_at,
        "from_account_id": account_id or "",
        "from_account_name": account_name,
        "to_account_id": VIRTUAL_DEPOSIT_ACCOUNT_ID,
        "to_account_name": VIRTUAL_DEPOSIT_ACCOUNT_NAME,
        "note": f"存入定期 · {dep.name}",
    }


def _build_principal_return_payload(
    *, dep: Deposit, account_id: str, account_name: Optional[str], at: datetime
) -> dict:
    """支取本金:虚拟定期存款账户 → 活期(transfer)。"""
    return {
        "tx_type": "transfer",
        "amount": dep.principal,
        "currency_code": dep.currency_code,
        "happened_at": at.isoformat(),
        "from_account_id": VIRTUAL_DEPOSIT_ACCOUNT_ID,
        "from_account_name": VIRTUAL_DEPOSIT_ACCOUNT_NAME,
        "to_account_id": account_id,
        "to_account_name": account_name,
        "note": f"定期支取 · {dep.name} · 本金",
    }


def _build_interest_income_payload(
    *,
    dep: Deposit,
    amount: float,
    account_id: str,
    account_name: Optional[str],
    at: datetime,
    label: str = "存款利息",
) -> dict:
    """利息收入(income,计入统计)。"""
    return {
        "tx_type": "income",
        "amount": amount,
        "currency_code": dep.currency_code,
        "happened_at": at.isoformat(),
        "account_id": account_id,
        "account_name": account_name,
        "category_name": label,
        "category_kind": "income",
        "note": f"{label} · {dep.name}",
    }


def _regenerate_deposit_flow(
    db: Session, *, ledger: Ledger, user: User, dep: Deposit
) -> None:
    """存单未支取/未转存时,把存入划转流水跟当前字段对齐(原地更新)。"""
    if dep.status in ("withdrawn", "renewed"):
        return
    if not dep.account_id:
        # 没有账户就不生成流水;之前有流水而现在账户被清空 → 删流水。
        if dep.flow_tx_sync_id:
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=dep.flow_tx_sync_id)
            dep.flow_tx_sync_id = None
        return
    payload = _build_deposit_flow_payload(
        dep=dep, account_id=dep.account_id, account_name=dep.account_name
    )
    # 存单照片作为划转流水附件(交易详情可见)。
    file_ids = _json_loads_safe(dep.certificate_file_ids_json)
    payload["attachments"] = _attachment_refs_from_ids(db, user_id=user.id, file_ids=file_ids)
    if dep.flow_tx_sync_id:
        _update_loan_flow_tx(
            db, ledger=ledger, user=user, tx_sync_id=dep.flow_tx_sync_id, payload=payload
        )
    else:
        dep.flow_tx_sync_id = _create_loan_flow_tx(db, ledger=ledger, user=user, payload=payload)


def _apply_deposit_fields(dep: Deposit, req: DepositUpdate) -> bool:
    """PATCH 赋值;返回是否触发了流水相关字段变化。"""
    flow_dirty = False
    if req.name is not None:
        dep.name = req.name
        flow_dirty = True
    if req.deposit_type is not None:
        dep.deposit_type = req.deposit_type
    if req.principal is not None:
        if dep.principal != req.principal:
            flow_dirty = True
        dep.principal = req.principal
    if req.currency_code is not None:
        dep.currency_code = req.currency_code
    if req.happened_at is not None:
        if dep.started_at != req.happened_at:
            flow_dirty = True
        dep.started_at = req.happened_at
    if req.term_months is not None:
        if dep.term_months != req.term_months:
            flow_dirty = True
        dep.term_months = req.term_months
    if req.annual_rate is not None:
        dep.annual_rate = req.annual_rate
    if req.interest_override is not None:
        dep.interest_override = req.interest_override
    if req.on_maturity is not None:
        dep.on_maturity = req.on_maturity
    if req.interest_settlement is not None:
        dep.interest_settlement = req.interest_settlement
    if req.settlement_day is not None:
        dep.settlement_day = req.settlement_day
    if req.note is not None:
        dep.note = req.note
    if req.certificate_file_ids is not None:
        dep.certificate_file_ids_json = _json_dumps(req.certificate_file_ids)
        flow_dirty = True
    if req.account_id is not None:
        if dep.account_id != req.account_id:
            flow_dirty = True
        dep.account_id = req.account_id or None
        if req.account_name is not None:
            dep.account_name = req.account_name
        elif dep.account_id and dep.account_name is None:
            dep.account_name = req.account_id
    elif req.account_name is not None:
        dep.account_name = req.account_name
        flow_dirty = True
    return flow_dirty


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@deposits_router.get("", response_model=list[DepositOut])
def list_deposits(
    ledger_id: str = Query(...),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DepositOut]:
    ledger = _require_ledger(db, current_user, ledger_id)
    stmt = select(Deposit).where(
        Deposit.ledger_id == ledger.id, Deposit.user_id == current_user.id
    )
    rows = list(db.scalars(stmt).all())
    settled = _settlements_map(db, [dep.id for dep in rows])
    now = datetime.now(timezone.utc)
    # 0046:批量取关联流水的可读摘要(存入 / 本金回流 / 利息,避免 N+1)。
    _payment_ids = []
    if rows:
        _payment_ids = list(
            db.scalars(
                select(DepositInterestPayment.tx_sync_id).where(
                    DepositInterestPayment.deposit_id.in_([d.id for d in rows])
                )
            ).all()
        )
    _tx_map = _linked_tx_map(
        db,
        {dep.ledger_id for dep in rows} or None,
        [dep.flow_tx_sync_id for dep in rows]
        + [dep.principal_return_tx_sync_id for dep in rows]
        + [dep.interest_tx_sync_id for dep in rows]
        + [s for s in _payment_ids if s],
    )
    outs = [
        _deposit_out(
            dep,
            settled.get(dep.id, 0.0),
            now,
            _deposit_tx_details(db, dep),
        )
        for dep in rows
    ]
    if status_filter:
        outs = [o for o in outs if o.status == status_filter]
    outs.sort(
        key=lambda o: (
            {"holding": 0, "matured": 1, "withdrawn": 2, "renewed": 3}.get(o.status, 9),
            -(o.started_at.timestamp()),
        )
    )
    return outs


@deposits_router.get("/summary", response_model=DepositSummaryOut)
def deposit_summary(
    ledger_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositSummaryOut:
    """「定期存款」虚拟账户:持有中 + 已到期存单的本金与预期利息。"""
    ledger = _require_ledger(db, current_user, ledger_id)
    rows = list(
        db.scalars(
            select(Deposit).where(
                Deposit.ledger_id == ledger.id, Deposit.user_id == current_user.id
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    total_principal = 0.0
    total_interest = 0.0
    count = 0
    for dep in rows:
        derived = derive_status(dep.status, dep.maturity_at, now)
        if derived not in ("holding", "matured"):
            continue
        count += 1
        total_principal += dep.principal
        total_interest += expected_interest(
            dep.principal, dep.annual_rate, dep.term_months, dep.interest_override
        )
    return DepositSummaryOut(
        ledger_id=ledger_id,
        total_principal=round(total_principal, 2),
        total_interest_expected=round(total_interest, 2),
        count=count,
    )


@deposits_router.get("/reminders", response_model=DepositRemindersOut)
def deposit_reminders(
    ledger_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositRemindersOut:
    """到期提醒:前 7 天(含当天)→ near_due;已到期未支取/未转存 → overdue。"""
    stmt = select(Deposit).where(Deposit.user_id == current_user.id)
    if ledger_id:
        ledger = _require_ledger(db, current_user, ledger_id)
        stmt = stmt.where(Deposit.ledger_id == ledger.id)
    rows = list(db.scalars(stmt).all())
    settled = _settlements_map(db, [dep.id for dep in rows])
    now = datetime.now(timezone.utc)
    near_due: list[DepositReminderItem] = []
    overdue: list[DepositReminderItem] = []
    for dep in rows:
        derived = derive_status(dep.status, dep.maturity_at, now)
        if derived not in ("holding", "matured"):
            continue
        if dep.maturity_at is None:
            continue
        maturity_utc = _utc(dep.maturity_at)
        if maturity_utc is None:
            continue
        days = days_until(maturity_utc, now)
        out = _deposit_out(dep, settled.get(dep.id, 0.0), now, _deposit_tx_details(db, dep))
        if days < 0:
            overdue.append(DepositReminderItem(deposit=out, days_to_maturity=days))
        elif days <= NEAR_MATURITY_DAYS:
            near_due.append(DepositReminderItem(deposit=out, days_to_maturity=days))
    near_due.sort(key=lambda x: x.days_to_maturity)
    overdue.sort(key=lambda x: x.days_to_maturity)
    return DepositRemindersOut(near_due=near_due, overdue=overdue)


@deposits_router.post("", response_model=DepositOut, status_code=201)
def create_deposit(
    req: DepositCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositOut:
    ledger = _require_ledger(db, current_user, req.ledger_id)
    started_at = req.happened_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    dep = Deposit(
        user_id=current_user.id,
        ledger_id=ledger.id,
        name=req.name,
        deposit_type=req.deposit_type,
        principal=req.principal,
        currency_code=req.currency_code,
        started_at=started_at,
        term_months=req.term_months,
        annual_rate=req.annual_rate,
        interest_override=req.interest_override,
        maturity_at=compute_maturity(started_at, req.term_months),
        on_maturity=req.on_maturity,
        interest_settlement=req.interest_settlement,
        settlement_day=req.settlement_day,
        status="holding",
        note=req.note,
        certificate_file_ids_json=_json_dumps(req.certificate_file_ids),
        account_id=req.account_id or None,
        account_name=req.account_name,
    )
    db.add(dep)
    db.flush()
    if dep.account_id:
        _regenerate_deposit_flow(db, ledger=ledger, user=current_user, dep=dep)
    db.commit()
    db.refresh(dep)
    return _deposit_out(dep, 0.0, tx_details=_deposit_tx_details(db, dep))


@deposits_router.patch("/{deposit_id}", response_model=DepositOut)
def update_deposit(
    deposit_id: str,
    req: DepositUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositOut:
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    ledger = _require_ledger(db, current_user, dep.ledger_id)
    flow_dirty = _apply_deposit_fields(dep, req)
    # 到期日随存入时间/存期重算。
    dep.maturity_at = compute_maturity(dep.started_at, dep.term_months)
    now = datetime.now(timezone.utc)
    if dep.status in ("holding", "matured"):
        # 非终态:按新到期日重派生。
        dep.status = derive_status("holding", dep.maturity_at, now)
    if flow_dirty:
        _regenerate_deposit_flow(db, ledger=ledger, user=current_user, dep=dep)
    db.commit()
    db.refresh(dep)
    return _deposit_out(dep, _settled_interest_total(db, dep.id), tx_details=_deposit_tx_details(db, dep))


@deposits_router.delete("/{deposit_id}", status_code=204)
def delete_deposit(
    deposit_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    ledger = _require_ledger(db, current_user, dep.ledger_id)
    # 删除联动流水:存入划转 / 支取本金 / 支取利息 / 结息流水。
    for sync_id in (dep.flow_tx_sync_id, dep.principal_return_tx_sync_id, dep.interest_tx_sync_id):
        if sync_id:
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=sync_id)
    payments = list(
        db.scalars(
            select(DepositInterestPayment).where(DepositInterestPayment.deposit_id == dep.id)
        ).all()
    )
    for pay in payments:
        if pay.tx_sync_id:
            _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=pay.tx_sync_id)
        db.delete(pay)
    # 转存链上的引用清空(子存单保留,父引用置 None;本存单转存出的子存单
    # parent 指向被删 → 置空)。
    children = list(
        db.scalars(select(Deposit).where(Deposit.parent_deposit_id == dep.id)).all()
    )
    for child in children:
        child.parent_deposit_id = None
    if dep.renewed_to_deposit_id:
        child = db.get(Deposit, dep.renewed_to_deposit_id)
        if child is not None and child.parent_deposit_id == dep.id:
            child.parent_deposit_id = None
    db.delete(dep)
    db.commit()


# --------------------------------------------------------------------------
# Actions: withdraw / renew / settle-interest
# --------------------------------------------------------------------------


@deposits_router.post("/{deposit_id}/withdraw", response_model=DepositOut)
def withdraw_deposit(
    deposit_id: str,
    req: DepositWithdrawRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositOut:
    """支取:本金 transfer 转回活期 + 利息 income(计入统计)。"""
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    if dep.status in ("withdrawn", "renewed"):
        raise HTTPException(status_code=409, detail="deposit already closed")
    ledger = _require_ledger(db, current_user, dep.ledger_id)
    account_id = req.account_id or dep.account_id
    account_name = req.account_name or dep.account_name
    at = req.withdrawn_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    settled = _settled_interest_total(db, dep.id)
    interest_expected = expected_interest(
        dep.principal, dep.annual_rate, dep.term_months, dep.interest_override
    )
    if req.interest_amount is not None:
        interest_amount = req.interest_amount
    else:
        interest_amount = max(0.0, round(interest_expected - settled, 2))
    if account_id:
        principal_payload = _build_principal_return_payload(
            dep=dep, account_id=account_id, account_name=account_name, at=at
        )
        if dep.principal_return_tx_sync_id:
            _update_loan_flow_tx(
                db, ledger=ledger, user=current_user,
                tx_sync_id=dep.principal_return_tx_sync_id, payload=principal_payload,
            )
        else:
            dep.principal_return_tx_sync_id = _create_loan_flow_tx(
                db, ledger=ledger, user=current_user, payload=principal_payload
            )
        if interest_amount > 0:
            interest_payload = _build_interest_income_payload(
                dep=dep, amount=interest_amount, account_id=account_id,
                account_name=account_name, at=at,
            )
            if dep.interest_tx_sync_id:
                _update_loan_flow_tx(
                    db, ledger=ledger, user=current_user,
                    tx_sync_id=dep.interest_tx_sync_id, payload=interest_payload,
                )
            else:
                dep.interest_tx_sync_id = _create_loan_flow_tx(
                    db, ledger=ledger, user=current_user, payload=interest_payload
                )
    dep.status = "withdrawn"
    dep.withdrawn_at = at
    dep.withdrawn_interest = interest_amount
    db.commit()
    db.refresh(dep)
    return _deposit_out(dep, settled, tx_details=_deposit_tx_details(db, dep))


@deposits_router.post("/{deposit_id}/renew", response_model=DepositOut)
def renew_deposit(
    deposit_id: str,
    req: DepositRenewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositOut:
    """转存:利息 income + 本金转回 + 新一期存单(重新划转)。

    一次性完成「旧存单结算 → 新存单开立」,账面每一步都有流水背书,
    避免虚拟账户余额与流水脱节。
    """
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    if dep.status in ("withdrawn", "renewed"):
        raise HTTPException(status_code=409, detail="deposit already closed")
    ledger = _require_ledger(db, current_user, dep.ledger_id)
    now = datetime.now(timezone.utc)
    settled = _settled_interest_total(db, dep.id)
    interest_expected = expected_interest(
        dep.principal, dep.annual_rate, dep.term_months, dep.interest_override
    )
    interest_amount = max(0.0, round(interest_expected - settled, 2)) if req.include_interest else 0.0
    new_principal = req.new_principal
    if new_principal is None:
        new_principal = dep.principal + interest_amount if req.include_interest else dep.principal
    new_started = req.started_at or (_utc(dep.maturity_at) or now)
    if new_started.tzinfo is None:
        new_started = new_started.replace(tzinfo=timezone.utc)
    account_id = req.account_id or dep.account_id
    account_name = req.account_name or dep.account_name

    # 1) 利息收入(转存即结算)。
    if account_id and interest_amount > 0:
        payload = _build_interest_income_payload(
            dep=dep, amount=interest_amount, account_id=account_id,
            account_name=account_name, at=new_started, label="存款利息(转存)",
        )
        dep.interest_tx_sync_id = _create_loan_flow_tx(
            db, ledger=ledger, user=current_user, payload=payload
        )
    # 2) 本金转回活期。
    if account_id:
        payload = _build_principal_return_payload(
            dep=dep, account_id=account_id, account_name=account_name, at=new_started
        )
        dep.principal_return_tx_sync_id = _create_loan_flow_tx(
            db, ledger=ledger, user=current_user, payload=payload
        )
    # 3) 旧存单标记转存。
    dep.status = "renewed"
    dep.renewed_at = new_started
    dep.withdrawn_interest = interest_amount or None
    # 4) 新存单开立 + 重新划转。
    new_term = req.new_term_months or dep.term_months
    new_dep = Deposit(
        user_id=current_user.id,
        ledger_id=ledger.id,
        name=req.new_name or dep.name,
        deposit_type=dep.deposit_type,
        principal=new_principal,
        currency_code=dep.currency_code,
        started_at=new_started,
        term_months=new_term,
        annual_rate=req.new_rate if req.new_rate is not None else dep.annual_rate,
        maturity_at=compute_maturity(new_started, new_term),
        on_maturity=dep.on_maturity,
        interest_settlement=dep.interest_settlement,
        settlement_day=dep.settlement_day,
        status="holding",
        note=dep.note,
        account_id=account_id,
        account_name=account_name,
        parent_deposit_id=dep.id,
    )
    db.add(new_dep)
    db.flush()
    dep.renewed_to_deposit_id = new_dep.id
    if account_id:
        _regenerate_deposit_flow(db, ledger=ledger, user=current_user, dep=new_dep)
    db.commit()
    db.refresh(new_dep)
    return _deposit_out(new_dep, 0.0, tx_details=_deposit_tx_details(db, new_dep))


@deposits_router.post(
    "/{deposit_id}/settle-interest",
    response_model=DepositInterestPaymentOut,
    status_code=201,
)
def settle_interest(
    deposit_id: str,
    req: DepositInterestSettleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositInterestPaymentOut:
    """周期结息(按月/按季):生成 income 流水 + 结息记录。"""
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    if dep.status in ("withdrawn", "renewed"):
        raise HTTPException(status_code=409, detail="deposit already closed")
    ledger = _require_ledger(db, current_user, dep.ledger_id)
    account_id = req.account_id or dep.account_id
    account_name = req.account_name or dep.account_name
    at = req.settled_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    pay = DepositInterestPayment(
        user_id=current_user.id,
        ledger_id=ledger.id,
        deposit_id=dep.id,
        amount=req.amount,
        settled_at=at,
        note=req.note,
        account_id=account_id,
        account_name=account_name,
    )
    db.add(pay)
    db.flush()
    if account_id:
        payload = _build_interest_income_payload(
            dep=dep, amount=req.amount, account_id=account_id,
            account_name=account_name, at=at,
        )
        pay.tx_sync_id = _create_loan_flow_tx(
            db, ledger=ledger, user=current_user, payload=payload
        )
    db.commit()
    db.refresh(pay)
    out = DepositInterestPaymentOut.model_validate(pay)
    _tx_map = _linked_tx_map(db, pay.ledger_id, [pay.tx_sync_id])
    if pay.tx_sync_id in _tx_map:
        out.tx_sync_details = [_tx_map[pay.tx_sync_id]]
    return out


@deposits_router.get(
    "/{deposit_id}/interest-payments",
    response_model=list[DepositInterestPaymentOut],
)
def list_interest_payments(
    deposit_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DepositInterestPaymentOut]:
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    rows = list(
        db.scalars(
            select(DepositInterestPayment)
            .where(DepositInterestPayment.deposit_id == dep.id)
            .order_by(DepositInterestPayment.settled_at.desc())
        ).all()
    )
    # 0046:批量取结息流水的可读摘要(避免 N+1)。
    _tx_map = _linked_tx_map(
        db, {r.ledger_id for r in rows} or None, [r.tx_sync_id for r in rows]
    )
    out = []
    for r in rows:
        item = DepositInterestPaymentOut.model_validate(r)
        if r.tx_sync_id in _tx_map:
            item.tx_sync_details = [_tx_map[r.tx_sync_id]]
        out.append(item)
    return out


@deposits_router.delete("/interest-payments/{payment_id}", status_code=204)
def delete_interest_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    pay = db.get(DepositInterestPayment, payment_id)
    if pay is None or pay.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="interest payment not found")
    ledger = _require_ledger(db, current_user, pay.ledger_id)
    if pay.tx_sync_id:
        _delete_loan_flow_tx(db, ledger=ledger, tx_sync_id=pay.tx_sync_id)
    db.delete(pay)
    db.commit()


@deposits_router.get("/{deposit_id}", response_model=DepositOut)
def get_deposit(
    deposit_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepositOut:
    dep = _get_deposit_or_404(db, current_user, deposit_id)
    return _deposit_out(dep, _settled_interest_total(db, dep.id), tx_details=_deposit_tx_details(db, dep))
