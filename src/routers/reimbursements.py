"""Web-first REST endpoints for 报销(Reimbursement)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..linked_tx import linked_tx_map as _linked_tx_map
from ..deps import get_current_user
from ..models import (
    Company,
    Ledger,
    Reimbursement,
    ReimbursementItem,
    SyncChange,
    User,
    UserAccountProjection,
)
from ..projection import upsert_tx
from ..schemas_linked import LinkedTxOut
from ..schemas_rb import (
    ReimbursementCreateIn,
    ReimbursementItemCreateIn,
    ReimbursementItemOut,
    ReimbursementOut,
    ReimbursementUpdateIn,
)
from ..snapshot_mutator import create_transaction as _mutate_create_tx

rr_router = APIRouter()


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


def _require_company(db: Session, user: User, company_id: str) -> Company:
    """报销公司维度(0037)：公司对当前用户必须存在，否则 404。"""
    company = db.scalar(
        select(Company).where(Company.user_id == user.id, Company.id == company_id)
    )
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    return company


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """归一到 UTC。naive 视为 UTC，aware 统一 astimezone(UTC)。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(value) -> datetime:
    """把 writer payload 里的 ISO8601 字符串解析成 UTC datetime。"""
    if isinstance(value, datetime):
        return _utc(value) or datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            return _utc(datetime.fromisoformat(value.replace("Z", "+00:00"))) or datetime.now(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _parse_attachments(raw: str | None) -> Optional[list[dict]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else None
    except (ValueError, TypeError):
        return None


def _item_out(
    item: ReimbursementItem,
    tx_details: Optional[list[LinkedTxOut]] = None,
) -> ReimbursementItemOut:
    return ReimbursementItemOut(
        id=item.id,
        user_id=item.user_id,
        ledger_id=item.ledger_id,
        tx_sync_id=item.tx_sync_id,
        tx_sync_details=tx_details or [],
        amount=item.amount,
        happened_at=item.happened_at,
        note=item.note,
        category_name=item.category_name,
        account_id=item.account_id,
        account_name=item.account_name,
        currency_code=item.currency_code,
        attachments=_parse_attachments(item.attachments_json),
        company_id=item.company_id,
        company_name=item.company_name,
        status=item.status,
        reimbursement_id=item.reimbursement_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _rb_out(db: Session, rb: Reimbursement) -> ReimbursementOut:
    items = list(
        db.scalars(
            select(ReimbursementItem)
            .where(ReimbursementItem.reimbursement_id == rb.id)
            .order_by(ReimbursementItem.happened_at.desc())
        ).all()
    )
    # 0046:报销单自身 + 明细项的关联流水摘要(一次批量查)。
    _tx_map = _linked_tx_map(
        db,
        rb.ledger_id,
        [rb.tx_sync_id] + [i.tx_sync_id for i in items],
    )
    return ReimbursementOut(
        id=rb.id,
        user_id=rb.user_id,
        ledger_id=rb.ledger_id,
        amount=rb.amount,
        happened_at=rb.happened_at,
        note=rb.note,
        icon=rb.icon,
        attachments=_parse_attachments(rb.attachments_json),
        receive_account_id=rb.receive_account_id,
        receive_account_name=rb.receive_account_name,
        tx_sync_id=rb.tx_sync_id,
        tx_sync_details=[_tx_map[rb.tx_sync_id]] if rb.tx_sync_id in _tx_map else [],
        item_count=rb.item_count if rb.item_count is not None else len(items),
        items=[
            _item_out(i, [_tx_map[i.tx_sync_id]] if i.tx_sync_id in _tx_map else [])
            for i in items
        ],
        company_id=rb.company_id,
        company_name=rb.company_name,
        created_at=rb.created_at,
        updated_at=rb.updated_at,
    )


def _create_tx(
    db: Session,
    user: User,
    ledger: Ledger,
    payload: dict,
) -> str:
    """通用：生成一笔交易并落 projection，返回 tx_sync_id。"""
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
        updated_by_device_id="reimbursement",
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


def register_item_from_tx(
    db: Session,
    user: User,
    ledger: Ledger,
    tx_sync_id: str,
    payload: dict,
) -> Optional[ReimbursementItem]:
    """writer 层在新建/编辑账单勾选"需报销"时调用：登记或更新待报销项。

    幂等：同一 tx_sync_id 只保留一条。再次调用（编辑交易时）会同步公司/金额等
    快照；已报销的历史项不再改动，保留报销时的公司快照。
    """
    # 公司维度(0037)：writer 传 reimbursement_company_id，手动登记传 company_id。
    company_id = payload.get("company_id") or payload.get("reimbursement_company_id")
    company_name = payload.get("company_name") or payload.get("reimbursement_company_name")
    if company_id:
        company = _require_company(db, user, str(company_id))
        company_id, company_name = company.id, company.name

    existing = db.scalar(
        select(ReimbursementItem).where(
            ReimbursementItem.user_id == user.id,
            ReimbursementItem.tx_sync_id == tx_sync_id,
        )
    )
    if existing is not None:
        if existing.status == "pending":
            if company_id:
                existing.company_id = company_id
                existing.company_name = company_name
            if "amount" in payload and payload.get("amount") is not None:
                existing.amount = float(payload["amount"])
            if "happened_at" in payload and payload.get("happened_at") is not None:
                existing.happened_at = _parse_dt(payload["happened_at"])
            for field in ("note", "category_name", "account_id", "account_name", "currency_code"):
                if field in payload:
                    setattr(existing, field, payload[field] or None)
            if "attachments" in payload:
                atts = payload.get("attachments")
                existing.attachments_json = json.dumps(atts, ensure_ascii=False) if isinstance(atts, list) and atts else None
            db.commit()
            db.refresh(existing)
        return existing
    attachments = payload.get("attachments")
    item = ReimbursementItem(
        user_id=user.id,
        ledger_id=ledger.id,
        tx_sync_id=tx_sync_id,
        amount=float(payload.get("amount") or 0),
        happened_at=_parse_dt(payload.get("happened_at")),
        note=payload.get("note") or None,
        category_name=payload.get("category_name") or None,
        account_id=payload.get("account_id") or None,
        account_name=payload.get("account_name") or None,
        currency_code=payload.get("currency_code") or None,
        attachments_json=json.dumps(attachments, ensure_ascii=False) if isinstance(attachments, list) and attachments else None,
        company_id=company_id,
        company_name=company_name,
        status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ==================== 报销项（待报销 / 已报）====================

@rr_router.get("/items", response_model=list[ReimbursementItemOut])
def list_items(
    ledger_id: str = Query(...),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReimbursementItemOut]:
    ledger = _require_ledger(db, current_user, ledger_id)
    stmt = select(ReimbursementItem).where(
        ReimbursementItem.ledger_id == ledger.id,
        ReimbursementItem.user_id == current_user.id,
    )
    if status:
        stmt = stmt.where(ReimbursementItem.status == status)
    rows = list(
        db.scalars(stmt.order_by(ReimbursementItem.happened_at.desc())).all()
    )
    # 0046:批量取关联流水摘要(避免 N+1)。
    _tx_map = _linked_tx_map(
        db, {r.ledger_id for r in rows} or None, [r.tx_sync_id for r in rows]
    )
    return [
        _item_out(r, [_tx_map[r.tx_sync_id]] if r.tx_sync_id in _tx_map else [])
        for r in rows
    ]


@rr_router.get("/items/by-tx/{tx_sync_id}", response_model=Optional[ReimbursementItemOut])
def get_item_by_tx(
    tx_sync_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Optional[ReimbursementItemOut]:
    """编辑账单时回填"是否报销"开关用。"""
    item = db.scalar(
        select(ReimbursementItem).where(
            ReimbursementItem.user_id == current_user.id,
            ReimbursementItem.tx_sync_id == tx_sync_id,
        )
    )
    if item is None:
        return None
    _tx_map = _linked_tx_map(db, item.ledger_id, [item.tx_sync_id])
    return _item_out(
        item, [_tx_map[item.tx_sync_id]] if item.tx_sync_id in _tx_map else []
    )


@rr_router.post("/items", response_model=ReimbursementItemOut, status_code=201)
def create_item(
    req: ReimbursementItemCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReimbursementItemOut:
    """手动把已有交易登记为报销项（编辑交易时勾选报销）。公司必填。"""
    ledger = _require_ledger(db, current_user, req.ledger_id)
    if not (req.company_id or "").strip():
        raise HTTPException(status_code=400, detail="报销公司必填")
    _require_company(db, current_user, req.company_id.strip())
    return register_item_from_tx(
        db, current_user, ledger, req.tx_sync_id, req.model_dump(mode="json")
    )


@rr_router.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """取消报销标记（仅待报销的可取消）。"""
    item = db.get(ReimbursementItem, item_id)
    if item is None or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="reimbursement item not found")
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="item already reimbursed")
    db.delete(item)
    db.commit()


# ==================== 报销单 ====================

@rr_router.get("", response_model=list[ReimbursementOut])
def list_reimbursements(
    ledger_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReimbursementOut]:
    ledger = _require_ledger(db, current_user, ledger_id)
    rows = list(
        db.scalars(
            select(Reimbursement)
            .where(
                Reimbursement.ledger_id == ledger.id,
                Reimbursement.user_id == current_user.id,
            )
            .order_by(Reimbursement.happened_at.desc())
        ).all()
    )
    return [_rb_out(db, r) for r in rows]


@rr_router.post("", response_model=ReimbursementOut, status_code=201)
def create_reimbursement(
    req: ReimbursementCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReimbursementOut:
    """多笔一次性报销：校验待报销项 → 生成报销单 → 关联项改为已报。
    如指定收款账户，生成一笔 income 交易（强制不记收支/不记预算）。
    """
    ledger = _require_ledger(db, current_user, req.ledger_id)
    ids = list(dict.fromkeys(req.item_ids))
    items = list(
        db.scalars(
            select(ReimbursementItem).where(
                ReimbursementItem.id.in_(ids),
                ReimbursementItem.user_id == current_user.id,
                ReimbursementItem.ledger_id == ledger.id,
            )
        ).all()
    )
    if len(items) != len(ids):
        raise HTTPException(status_code=404, detail="some reimbursement items not found")
    for it in items:
        if it.status != "pending":
            raise HTTPException(status_code=409, detail=f"item {it.id} already reimbursed")

    # 公司维度(0037)：报销单归属公司必填，且所有待报销项须属同一公司；
    # 历史遗留的无公司项按本次公司回填快照。
    company_id = (req.company_id or "").strip()
    if not company_id:
        raise HTTPException(status_code=400, detail="报销公司必填")
    company = _require_company(db, current_user, company_id)
    for it in items:
        if it.company_id and it.company_id != company.id:
            raise HTTPException(status_code=409, detail="所选账单归属不同公司，请分开报销")

    happened = _utc(req.happened_at) or datetime.now(timezone.utc)

    # 收款账户校验（选填）
    account_id = req.receive_account_id or None
    account_name = req.receive_account_name or None
    if account_id:
        account = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == current_user.id,
                UserAccountProjection.sync_id == account_id,
            )
        )
        if account is None:
            raise HTTPException(status_code=404, detail="receive account not found")
        account_name = account_name or account.name

    tx_sync_id = None
    if account_id:
        # 报销到账收入交易 —— 强制不记收支/不记预算
        payload = {
            "tx_type": "income",
            "amount": req.amount,
            "happened_at": happened.isoformat(),
            "account_id": account_id,
            "account_name": account_name,
            "category_name": "报销",
            "category_kind": "income",
            "note": req.note or "报销到账",
            "attachments": req.attachments or [],
            "exclude_from_stats": True,
            "exclude_from_budget": True,
            "currency_code": items[0].currency_code if items else None,
        }
        tx_sync_id = _create_tx(db, current_user, ledger, payload)

    rb = Reimbursement(
        user_id=current_user.id,
        ledger_id=ledger.id,
        amount=req.amount,
        happened_at=happened,
        note=req.note,
        icon=req.icon,
        attachments_json=json.dumps(req.attachments, ensure_ascii=False) if req.attachments else None,
        receive_account_id=account_id,
        receive_account_name=account_name,
        tx_sync_id=tx_sync_id,
        item_count=len(items),
        company_id=company.id,
        company_name=company.name,
    )
    db.add(rb)
    db.flush()
    for it in items:
        if not it.company_id:
            it.company_id = company.id
            it.company_name = company.name
        it.status = "reimbursed"
        it.reimbursement_id = rb.id
        it.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rb)
    return _rb_out(db, rb)


@rr_router.patch("/{rb_id}", response_model=ReimbursementOut)
def update_reimbursement(
    rb_id: str,
    req: ReimbursementUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReimbursementOut:
    """修改报销单图标/备注/时间/附件。"""
    rb = db.get(Reimbursement, rb_id)
    if rb is None or rb.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="reimbursement not found")
    if req.icon is not None:
        rb.icon = req.icon or None
    if req.note is not None:
        rb.note = req.note or None
    if req.happened_at is not None:
        rb.happened_at = _utc(req.happened_at)
    if req.attachments is not None:
        rb.attachments_json = json.dumps(req.attachments, ensure_ascii=False) if req.attachments else None
    db.commit()
    db.refresh(rb)
    return _rb_out(db, rb)


@rr_router.delete("/{rb_id}", status_code=204)
def delete_reimbursement(
    rb_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """删除报销单；关联项退回待报销（若对应交易仍存在）。"""
    rb = db.get(Reimbursement, rb_id)
    if rb is None or rb.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="reimbursement not found")
    for it in list(db.scalars(select(ReimbursementItem).where(ReimbursementItem.reimbursement_id == rb.id)).all()):
        it.status = "pending"
        it.reimbursement_id = None
    db.delete(rb)
    db.commit()


# ---------------- 流水变更 → 报销同步(0042) ----------------


def sync_reimbursement_from_flow_edit(
    db: Session, *, ledger_id: str, tx_sync_id: str, new_amount: float
) -> bool:
    """交易流水金额变更 → 同步引用它的报销记录(0042)。

    两级引用都是**快照**,流水金额变了必须同步,否则报销页显示的金额
    与原始账单对不上:
      - ReimbursementItem.tx_sync_id:单个待报销项(勾选「需报销」的账单);
      - Reimbursement.tx_sync_id:报销到账收入流水对应的报销单。

    返回 True 表示命中并已提交。
    """
    touched = False
    amount = round(float(new_amount), 2)

    # 1) 报销项(勾选了「需报销」的原始账单)
    items = list(
        db.scalars(
            select(ReimbursementItem).where(
                ReimbursementItem.ledger_id == ledger_id,
                ReimbursementItem.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for item in items:
        item.amount = amount
        touched = True

    # 2) 报销单(报销到账收入流水)
    rb = db.scalar(
        select(Reimbursement).where(
            Reimbursement.ledger_id == ledger_id,
            Reimbursement.tx_sync_id == tx_sync_id,
        )
    )
    if rb is not None:
        rb.amount = amount
        rb.updated_at = datetime.now(timezone.utc)
        touched = True

    if touched:
        db.commit()
    return touched
