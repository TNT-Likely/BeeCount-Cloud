"""Web-first REST endpoints for 储蓄罐(SavingPlan)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..linked_tx import linked_tx_map as _linked_tx_map
from ..deps import get_current_user
from ..models import (
    Ledger,
    ReadTxProjection,
    SavingContribution,
    SavingPlan,
    SyncChange,
    User,
    UserAccountProjection,
)
from ..projection import delete_tx, upsert_tx
from ..schemas_linked import LinkedTxOut
from ..schemas_sp import (
    ContributionIn,
    ContributionOut,
    ContributionUpdate,
    PlanCreate,
    PlanOut,
    PlanUpdate,
)
from ..snapshot_mutator import create_transaction as _mutate_create_tx

sp_router = APIRouter()


def _require_ledger(db: Session, user: User, ledger_id: str) -> Ledger:
    ledger = db.scalar(
        select(Ledger).where(
            (Ledger.id == ledger_id) | (Ledger.external_id == ledger_id),
            Ledger.user_id == user.id,
        )
    )
    if ledger is None:
        raise HTTPException(status_code=404, detail="ledger not found")
    return ledger


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """归一到 UTC。naive 视为 UTC(与 read/_shared._to_utc 同口径),
    aware 统一 astimezone(UTC) —— 存进 SQLite 时是 UTC naive,
    响应层再补 Z(见 schemas_sp._iso_utc)。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _effective_amounts(
    db: Session, contributions: list[SavingContribution]
) -> dict[str, float]:
    """按关联交易的实时状态计算每笔存款的「有效金额」。

    储蓄罐金额必须与对应流水同步:
    - 关联流水被删除(read_tx_projection 中已无该行)→ 该笔存款计 0;
    - 流水金额被编辑 → 以 projection 里的当前金额为准;
    - 流水被退款 → 扣掉指向该流水的退款金额(refund_of_sync_id)。
    这样无需在每个写接口(单笔/批量删除、退款、编辑、全量同步)埋钩子,
    所有写路径都会更新 projection,这里统一派生即可。
    """
    if not contributions:
        return {}
    ledger_ids = {c.ledger_id for c in contributions}
    # 每笔存款的关联流水:扣款流水(expense)优先,其次收款流水(income)。
    # 一笔存款可能同时生成两笔(双账户),但储蓄罐金额只计一次。
    linked: dict[str, list[str]] = {}
    all_ids: set[str] = set()
    for c in contributions:
        ids = [i for i in (c.tx_sync_id, c.receive_tx_sync_id) if i]
        linked[c.id] = ids
        all_ids.update(ids)

    tx_rows: dict[tuple[str, str], ReadTxProjection] = {}
    refund_rows: dict[tuple[str, str], float] = {}
    if all_ids:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id.in_(ledger_ids),
                ReadTxProjection.sync_id.in_(all_ids),
            )
        ).all()
        tx_rows = {(r.ledger_id, r.sync_id): r for r in rows}
        # 查指向这些流水的退款交易(refund_of_sync_id 指向被退的原始交易)
        refunds = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id.in_(ledger_ids),
                ReadTxProjection.refund_of_sync_id.in_(all_ids),
            )
        ).all()
        for r in refunds:
            key = (r.ledger_id, r.refund_of_sync_id)
            refund_rows[key] = refund_rows.get(key, 0.0) + float(r.amount or 0.0)

    result: dict[str, float] = {}
    for c in contributions:
        ids = linked[c.id]
        if not ids:
            # 没有关联流水(仅记录的存入)→ 用存入时登记的金额
            result[c.id] = round(float(c.amount or 0.0), 2)
            continue
        # 取第一笔仍然存在的流水(expense 优先);全部被删则计 0
        base_id = next((i for i in ids if (c.ledger_id, i) in tx_rows), None)
        if base_id is None:
            result[c.id] = 0.0
            continue
        row = tx_rows[(c.ledger_id, base_id)]
        net = float(row.amount or 0.0) - refund_rows.get((c.ledger_id, base_id), 0.0)
        result[c.id] = round(max(0.0, net), 2)
    return result


def _saved_amount(db: Session, plan_id: str) -> float:
    contribs = list(
        db.scalars(
            select(SavingContribution).where(SavingContribution.plan_id == plan_id)
        ).all()
    )
    return round(sum(_effective_amounts(db, contribs).values()), 2)


def _contrib_out(
    c: SavingContribution,
    effective_amount: float | None = None,
    tx_details: Optional[list[LinkedTxOut]] = None,
    receive_tx_details: Optional[list[LinkedTxOut]] = None,
) -> ContributionOut:
    attachments = None
    if c.attachments_json:
        try:
            parsed = json.loads(c.attachments_json)
            if isinstance(parsed, list):
                attachments = parsed
        except (ValueError, TypeError):
            attachments = None
    return ContributionOut(
        id=c.id,
        plan_id=c.plan_id,
        amount=c.amount,
        effective_amount=round(
            float(c.amount if effective_amount is None else effective_amount), 2
        ),
        happened_at=c.happened_at,
        note=c.note,
        tx_sync_id=c.tx_sync_id,
        account_id=c.account_id,
        account_name=c.account_name,
        receive_account_id=c.receive_account_id,
        receive_account_name=c.receive_account_name,
        receive_tx_sync_id=c.receive_tx_sync_id,
        tx_sync_details=tx_details or [],
        receive_tx_sync_details=receive_tx_details or [],
        category_name=c.category_name,
        currency_code=c.currency_code,
        native_amount=c.native_amount,
        attachments=attachments,
        exclude_from_stats=bool(c.exclude_from_stats),
        exclude_from_budget=bool(c.exclude_from_budget),
        created_at=c.created_at,
    )


def _create_tx(
    db: Session,
    user: User,
    ledger: Ledger,
    payload: dict,
) -> str:
    """生成一笔交易并落 projection,返回 tx_sync_id。"""
    _snap, new_tx_id = _mutate_create_tx({"items": [], "count": 0}, payload)
    item = _snap["items"][0]
    now = datetime.now(timezone.utc)
    change_row = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=new_tx_id,
        action="upsert",
        payload_json=item,
        updated_at=now,
        updated_by_device_id="saving-plan",
        updated_by_user_id=user.id,
    )
    db.add(change_row)
    db.flush()
    upsert_tx(
        db,
        ledger_id=ledger.id,
        user_id=ledger.user_id,
        source_change_id=change_row.change_id,
        payload=item,
    )
    return new_tx_id


def _delete_tx(db: Session, ledger_id: str, tx_sync_id: Optional[str]) -> None:
    """删除一笔由储蓄罐生成的流水(连同其 SyncChange 快照)。

    0042:删除存入记录 / 删除储蓄罐时必须一并删除它生成的流水,否则会留下
    孤儿交易(交易列表里还在,但储蓄罐已不存在)。
    """
    if not tx_sync_id:
        return
    delete_tx(db, ledger_id=ledger_id, sync_id=tx_sync_id)
    db.execute(
        delete(SyncChange).where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "transaction",
            SyncChange.entity_sync_id == tx_sync_id,
        )
    )


def _update_tx(
    db: Session,
    user: User,
    ledger: Ledger,
    tx_sync_id: Optional[str],
    *,
    amount: Optional[float] = None,
    happened_at: Optional[datetime] = None,
    note: Optional[str] = None,
) -> None:
    """更新一笔由储蓄罐生成的流水(金额/时间/备注)。

    为什么必须同步改流水:存入记录的「有效金额」是**实时派生**的
    (_effective_amounts 读 read_tx_projection),不是快照。只改
    SavingContribution.amount 的话,已存金额仍按流水里的旧金额算 → 用户
    看到的数字不跟着变,两头对不上。

    实现走 SyncChange(upsert)+ upsert_tx,跟 _create_tx 同一套写入路径,
    这样 mobile 端 pull 能收到这次改动。
    """
    if not tx_sync_id:
        return
    # 取该流水最近一条 SyncChange 的 payload 作为「当前快照」(camelCase item)。
    # SyncChange 的 payload 是完整 item —— upsert_tx 会把它所有字段写回投影,
    # 所以必须拿到**完整**快照,不能只取几个字段(否则其余字段会被抹成 NULL)。
    change = db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "transaction",
            SyncChange.entity_sync_id == tx_sync_id,
        )
        .order_by(SyncChange.change_id.desc())
    )
    if change is not None:
        item = dict(change.payload_json or {})
    else:
        # 兜底:没有 SyncChange(理论上储蓄罐生成的流水一定有),退投影行。
        from .write._shared import _projection_row_to_tx_dict

        row = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.sync_id == tx_sync_id,
            )
        )
        if row is None:
            return
        item = _projection_row_to_tx_dict(row)
    if not item.get("syncId"):
        item["syncId"] = tx_sync_id

    payload: dict[str, object] = {}
    if amount is not None:
        payload["amount"] = round(float(amount), 2)
    if happened_at is not None:
        payload["happened_at"] = happened_at
    if note is not None:
        payload["note"] = note
    if not payload:
        return

    # 用 mutator 走一遍(处理外币 nativeAmount 联动 / note 空串清除等规则)。
    from ..snapshot_mutator import update_transaction as _mutate_update_tx

    minimal = {"items": [item], "count": 1}
    minimal = _mutate_update_tx(minimal, tx_sync_id, payload)
    new_item = minimal["items"][0]

    now = datetime.now(timezone.utc)
    change_row = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx_sync_id,
        action="upsert",
        payload_json=new_item,
        updated_at=now,
        updated_by_device_id="saving-plan",
        updated_by_user_id=user.id,
    )
    db.add(change_row)
    db.flush()
    upsert_tx(
        db,
        ledger_id=ledger.id,
        user_id=ledger.user_id,
        source_change_id=change_row.change_id,
        payload=new_item,
    )


def _plan_out(db: Session, plan: SavingPlan) -> PlanOut:
    saved = _saved_amount(db, plan.id)
    progress = 0.0
    if plan.target_amount and plan.target_amount > 0:
        progress = min(100.0, round(saved / plan.target_amount * 100, 2))
    return PlanOut(
        id=plan.id,
        user_id=plan.user_id,
        ledger_id=plan.ledger_id,
        name=plan.name,
        icon=plan.icon,
        target_amount=plan.target_amount,
        currency_code=plan.currency_code,
        deadline=plan.deadline,
        account_id=plan.account_id,
        account_name=plan.account_name,
        plan_type=plan.plan_type,  # type: ignore[arg-type]
        monthly_amount=plan.monthly_amount,
        status=plan.status,  # type: ignore[arg-type]
        note=plan.note,
        saved_amount=round(saved, 2),
        progress=progress,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


@sp_router.get("", response_model=list[PlanOut])
def list_plans(
    ledger_id: str = Query(...),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PlanOut]:
    ledger = _require_ledger(db, current_user, ledger_id)
    stmt = select(SavingPlan).where(
        SavingPlan.ledger_id == ledger.id, SavingPlan.user_id == current_user.id
    )
    if status:
        stmt = stmt.where(SavingPlan.status == status)
    rows = list(db.scalars(stmt).all())
    return [_plan_out(db, p) for p in rows]


@sp_router.post("", response_model=PlanOut, status_code=201)
def create_plan(
    req: PlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanOut:
    ledger = _require_ledger(db, current_user, req.ledger_id)
    deadline = _utc(req.deadline)
    plan = SavingPlan(
        user_id=current_user.id,
        ledger_id=ledger.id,
        name=req.name,
        icon=req.icon,
        target_amount=req.target_amount,
        currency_code=req.currency_code,
        deadline=deadline,
        account_id=req.account_id,
        account_name=req.account_name,
        plan_type=req.plan_type,
        monthly_amount=req.monthly_amount,
        note=req.note,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_out(db, plan)


@sp_router.get("/{plan_id}", response_model=PlanOut)
def get_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanOut:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    return _plan_out(db, plan)


@sp_router.patch("/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: str,
    req: PlanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanOut:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    if req.name is not None:
        plan.name = req.name
    if req.icon is not None:
        plan.icon = req.icon
    if req.target_amount is not None:
        plan.target_amount = req.target_amount
    if req.currency_code is not None:
        plan.currency_code = req.currency_code
    if req.deadline is not None:
        plan.deadline = _utc(req.deadline)
    if req.account_id is not None:
        plan.account_id = req.account_id or None
    if req.account_name is not None:
        plan.account_name = req.account_name
    if req.plan_type is not None:
        plan.plan_type = req.plan_type
    if req.monthly_amount is not None:
        plan.monthly_amount = req.monthly_amount
    if req.status is not None:
        plan.status = req.status
    if req.note is not None:
        plan.note = req.note
    db.commit()
    db.refresh(plan)
    return _plan_out(db, plan)


@sp_router.delete("/{plan_id}", status_code=204)
def delete_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    # 删除关联贡献记录(连同它们生成的流水,避免留下孤儿交易)。
    for c in list(db.scalars(select(SavingContribution).where(SavingContribution.plan_id == plan.id)).all()):
        _delete_tx(db, plan.ledger_id, c.tx_sync_id)
        _delete_tx(db, plan.ledger_id, c.receive_tx_sync_id)
        db.delete(c)
    db.delete(plan)
    db.commit()


@sp_router.get("/{plan_id}/contributions", response_model=list[ContributionOut])
def list_contributions(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ContributionOut]:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    rows = list(
        db.scalars(
            select(SavingContribution)
            .where(SavingContribution.plan_id == plan.id)
            .order_by(SavingContribution.happened_at.desc())
        ).all()
    )
    effective = _effective_amounts(db, rows)
    # 0046:批量取关联流水的可读摘要(存入流水 / 到账流水,避免 N+1)。
    _tx_map = _linked_tx_map(
        db,
        {r.ledger_id for r in rows} or None,
        [r.tx_sync_id for r in rows] + [r.receive_tx_sync_id for r in rows],
    )
    return [
        _contrib_out(
            r,
            effective.get(r.id),
            [_tx_map[r.tx_sync_id]] if r.tx_sync_id in _tx_map else [],
            (
                [_tx_map[r.receive_tx_sync_id]]
                if r.receive_tx_sync_id in _tx_map
                else []
            ),
        )
        for r in rows
    ]


@sp_router.post("/{plan_id}/contribute", response_model=ContributionOut, status_code=201)
def contribute(
    plan_id: str,
    req: ContributionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContributionOut:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    if plan.status != "active":
        raise HTTPException(status_code=409, detail="plan not active")
    happened = _utc(req.happened_at) or datetime.now(timezone.utc)
    ledger = db.get(Ledger, plan.ledger_id) or _require_ledger(db, current_user, plan.ledger_id)

    # 扣款账户(可选):选中则生成一笔支出交易并扣减账户余额
    debit_id = req.account_id or None
    receive_id = req.receive_account_id or None

    def _resolve_account(acc_id: str):
        # 账户投影是 user-global 的(主键 user_id + sync_id),没有 ledger_id 字段
        acc = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == current_user.id,
                UserAccountProjection.sync_id == acc_id,
            )
        )
        if acc is None:
            raise HTTPException(status_code=404, detail=f"account not found: {acc_id}")
        return acc

    debit_acc = _resolve_account(debit_id) if debit_id else None
    receive_acc = _resolve_account(receive_id) if receive_id else None

    note = req.note or f"储蓄罐「{plan.name}」存入"
    category_name = req.category_name or "储蓄"
    attachments = req.attachments or []

    # ---- 生成流水 ----
    # 三种口径(0057):
    #   1) 关联已有交易(tx_sync_id)→ 直接绑定那笔既有流水,不再另建;
    #   2) 同时选了扣款账户 + 收款账户 → 资金从 A 转到 B,记为**一笔转账**
    #      (真实银行流水就是一笔,拆成「一笔支出 + 一笔收入」会虚增收支笔数);
    #   3) 只选扣款账户 → 一笔支出;只选收款账户 → 一笔收入。
    tx_sync_id = req.tx_sync_id
    debit_account_id = debit_account_name = None
    receive_tx_sync_id = None
    receive_account_id = receive_account_name = None

    if not tx_sync_id:
        if debit_acc is not None and receive_acc is not None:
            # 双账户 = 转账:一条流水,转出/转入都在同一笔里。
            payload = {
                "tx_type": "transfer",
                "amount": req.amount,
                "currency_code": req.currency_code,
                "native_amount": req.native_amount,
                "happened_at": happened.isoformat(),
                "from_account_id": debit_acc.sync_id,
                "from_account_name": debit_acc.name,
                "to_account_id": receive_acc.sync_id,
                "to_account_name": receive_acc.name,
                "note": note,
                "exclude_from_stats": bool(req.exclude_from_stats),
                "exclude_from_budget": bool(req.exclude_from_budget),
                "attachments": attachments,
            }
            tx_sync_id = _create_tx(db, current_user, ledger, payload)
            debit_account_id, debit_account_name = debit_acc.sync_id, debit_acc.name
            receive_account_id, receive_account_name = receive_acc.sync_id, receive_acc.name
        elif debit_acc is not None:
            # 支出交易(扣款账户)
            payload = {
                "tx_type": "expense",
                "amount": req.amount,
                "currency_code": req.currency_code,
                "native_amount": req.native_amount,
                "happened_at": happened.isoformat(),
                "account_id": debit_acc.sync_id,
                "account_name": debit_acc.name,
                "category_name": category_name,
                "category_kind": "expense",
                "note": note,
                "exclude_from_stats": bool(req.exclude_from_stats),
                "exclude_from_budget": bool(req.exclude_from_budget),
                "attachments": attachments,
            }
            tx_sync_id = _create_tx(db, current_user, ledger, payload)
            debit_account_id, debit_account_name = debit_acc.sync_id, debit_acc.name
        elif receive_acc is not None:
            # 收入交易(收款账户)
            payload = {
                "tx_type": "income",
                "amount": req.amount,
                "currency_code": req.currency_code,
                "native_amount": req.native_amount,
                "happened_at": happened.isoformat(),
                "account_id": receive_acc.sync_id,
                "account_name": receive_acc.name,
                "category_name": category_name,
                "category_kind": "income",
                "note": note,
                "exclude_from_stats": bool(req.exclude_from_stats),
                "exclude_from_budget": bool(req.exclude_from_budget),
                "attachments": attachments,
            }
            receive_tx_sync_id = _create_tx(db, current_user, ledger, payload)
            receive_account_id, receive_account_name = receive_acc.sync_id, receive_acc.name
    else:
        # 关联已有交易:这笔钱已经在既有流水里记过了,储蓄罐只做归属,
        # 账户信息沿用那笔流水的,避免重复记账。
        linked_tx = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == plan.ledger_id,
                ReadTxProjection.sync_id == tx_sync_id,
            )
        )
        if linked_tx is None:
            raise HTTPException(status_code=404, detail="linked transaction not found")
        debit_account_id = linked_tx.account_sync_id
        debit_account_name = linked_tx.account_name
        receive_account_id = linked_tx.to_account_sync_id
        receive_account_name = linked_tx.to_account_name

    c = SavingContribution(
        user_id=current_user.id,
        ledger_id=plan.ledger_id,
        plan_id=plan.id,
        amount=req.amount,
        happened_at=happened,
        note=req.note,
        tx_sync_id=tx_sync_id,
        account_id=debit_account_id,
        account_name=debit_account_name,
        receive_account_id=receive_account_id,
        receive_account_name=receive_account_name,
        receive_tx_sync_id=receive_tx_sync_id,
        category_name=(
            # 转账(双账户)不带分类 —— 分类是收支口径,转账本就不计收支统计。
            category_name
            if (debit_acc or receive_acc) and not (debit_acc and receive_acc)
            else None
        ),
        currency_code=req.currency_code,
        native_amount=req.native_amount,
        attachments_json=json.dumps(attachments, ensure_ascii=False) if attachments else None,
        exclude_from_stats=bool(req.exclude_from_stats),
        exclude_from_budget=bool(req.exclude_from_budget),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    # 自动完成检测
    saved = _saved_amount(db, plan.id)
    if saved >= plan.target_amount and plan.status == "active":
        plan.status = "completed"
        db.commit()
    _tx_map = _linked_tx_map(
        db, c.ledger_id, [c.tx_sync_id, c.receive_tx_sync_id]
    )
    return _contrib_out(
        c,
        _effective_amounts(db, [c]).get(c.id),
        [_tx_map[c.tx_sync_id]] if c.tx_sync_id in _tx_map else [],
        (
            [_tx_map[c.receive_tx_sync_id]] if c.receive_tx_sync_id in _tx_map else []
        ),
    )


@sp_router.delete("/contributions/{contrib_id}", status_code=204)
def delete_contribution(
    contrib_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    c = db.get(SavingContribution, contrib_id)
    if c is None or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="contribution not found")
    # 0042:一并删除它生成的流水(否则交易列表会留下孤儿记录)。
    _delete_tx(db, c.ledger_id, c.tx_sync_id)
    _delete_tx(db, c.ledger_id, c.receive_tx_sync_id)
    db.delete(c)
    db.commit()


@sp_router.patch("/contributions/{contrib_id}", response_model=ContributionOut)
def update_contribution(
    contrib_id: str,
    req: ContributionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContributionOut:
    """修改一笔存入记录(金额 / 时间 / 备注 / 分类 / 统计开关)。

    关键点:金额是**实时派生**的(读时从关联流水算),所以改了金额必须
    同步改它生成的流水,否则储蓄罐已存金额不跟随 → 用户看到"改了没用"。
    """
    c = db.get(SavingContribution, contrib_id)
    if c is None or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="contribution not found")
    plan = db.get(SavingPlan, c.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    ledger = db.get(Ledger, c.ledger_id) or _require_ledger(
        db, current_user, c.ledger_id
    )

    if req.amount is not None:
        c.amount = round(float(req.amount), 2)
    if req.happened_at is not None:
        c.happened_at = _utc(req.happened_at) or c.happened_at
    if req.note is not None:
        c.note = req.note
    if req.category_name is not None:
        c.category_name = req.category_name
    if req.exclude_from_stats is not None:
        c.exclude_from_stats = bool(req.exclude_from_stats)
    if req.exclude_from_budget is not None:
        c.exclude_from_budget = bool(req.exclude_from_budget)
    if req.currency_code is not None:
        c.currency_code = req.currency_code
    if req.native_amount is not None:
        c.native_amount = req.native_amount

    # 同步它生成的流水(扣款侧 + 收款侧),保证派生金额跟着变。
    # 只同步**本次真的改了**的字段 —— 否则会把流水上自动生成的默认备注
    # (如「储蓄罐「旅行基金」存入」)抹掉。
    tx_amount = c.amount if req.amount is not None else None
    tx_happened_at = c.happened_at if req.happened_at is not None else None
    tx_note = req.note if req.note is not None else None
    _update_tx(
        db,
        current_user,
        ledger,
        c.tx_sync_id,
        amount=tx_amount,
        happened_at=tx_happened_at,
        note=tx_note,
    )
    _update_tx(
        db,
        current_user,
        ledger,
        c.receive_tx_sync_id,
        amount=tx_amount,
        happened_at=tx_happened_at,
        note=tx_note,
    )
    db.commit()
    db.refresh(c)

    _tx_map = _linked_tx_map(db, c.ledger_id, [c.tx_sync_id, c.receive_tx_sync_id])
    return _contrib_out(
        c,
        _effective_amounts(db, [c]).get(c.id),
        [_tx_map[c.tx_sync_id]] if c.tx_sync_id in _tx_map else [],
        (
            [_tx_map[c.receive_tx_sync_id]]
            if c.receive_tx_sync_id in _tx_map
            else []
        ),
    )


@sp_router.post("/{plan_id}/abandon", response_model=PlanOut)
def abandon_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanOut:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    plan.status = "abandoned"
    db.commit()
    db.refresh(plan)
    return _plan_out(db, plan)


@sp_router.post("/{plan_id}/complete", response_model=PlanOut)
def complete_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanOut:
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    plan.status = "completed"
    db.commit()
    db.refresh(plan)
    return _plan_out(db, plan)


@sp_router.post("/{plan_id}/archive", response_model=PlanOut)
def archive_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PlanOut:
    """归档计划:保留数据但不再出现在活跃列表。已删除的计划返回 404。"""
    plan = db.get(SavingPlan, plan_id)
    if plan is None or plan.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="plan not found")
    plan.status = "archived"
    db.commit()
    db.refresh(plan)
    return _plan_out(db, plan)