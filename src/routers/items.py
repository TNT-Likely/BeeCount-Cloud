"""Web-first REST endpoints for 物品管理(Item)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..linked_tx import linked_tx_amounts as _linked_tx_amounts
from ..linked_tx import linked_tx_details as _linked_tx_details
from ..linked_tx import linked_tx_total as _linked_tx_total
from ..deps import get_current_user
from ..models import (
    Item,
    ItemFlow,
    Ledger,
    ReadTxProjection,
    SyncChange,
    User,
    UserAccountProjection,
)
from ..projection import delete_tx, upsert_tx
from ..schemas_items import (
    ItemCreate,
    ItemFlowCreate,
    ItemFlowOut,
    ItemFlowUpdate,
    ItemOut,
    ItemUpdate,
)
from ..snapshot_mutator import create_transaction as _mutate_create_tx

items_router = APIRouter()


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
    if dt is None:
        return None
    if isinstance(dt, str):
        # 写接口的 payload 经 model_dump(mode="json") 后 datetime 变 ISO 字符串。
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_attachments(raw: Optional[str]) -> Optional[list[dict]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    except (ValueError, TypeError):
        return None


def _dump_attachments(att: Optional[list[dict]]) -> Optional[str]:
    return json.dumps(att, ensure_ascii=False) if isinstance(att, list) and att else None


def _resolve_account(db: Session, user: User, account_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """校验账户并补全账户名。返回 (account_id, account_name)。"""
    if not account_id:
        return None, None
    account = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account.sync_id, account.name


def _require_existing_tx(db: Session, ledger: Ledger, tx_sync_id: Optional[str]) -> None:
    if not tx_sync_id:
        raise HTTPException(status_code=400, detail="tx_sync_id required when link_mode=existing")
    row = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.sync_id == tx_sync_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="transaction not found")


def _parse_tx_ids(raw: Optional[str]) -> list[str]:
    """解析 tx_sync_ids_json → syncId 列表(去重、保持顺序)。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for x in parsed:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _dump_tx_ids(tx_sync_ids: Optional[list[str]]) -> Optional[str]:
    """syncId 列表 → JSON 文本;空列表返回 None。"""
    ids = [str(x).strip() for x in (tx_sync_ids or []) if str(x or "").strip()]
    if not ids:
        return None
    # 去重但保持顺序
    seen: set[str] = set()
    uniq: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return json.dumps(uniq, ensure_ascii=False)


def _resolve_link_tx_ids(
    db: Session,
    ledger: Ledger,
    *,
    tx_sync_ids: Optional[list[str]],
    tx_sync_id: Optional[str],
) -> list[str]:
    """解析「关联已有交易」的 syncId 列表并逐笔校验存在性(0040)。

    - tx_sync_ids 与 tx_sync_id 都传时以 tx_sync_ids 为准(多选优先);
    - 都为空 → 400(link_mode=existing 必须至少关联一笔);
    - 逐笔校验:交易必须存在于该账本,否则 404。
    """
    ids: list[str] = []
    for src in (tx_sync_ids or []):
        s = str(src or "").strip()
        if s and s not in ids:
            ids.append(s)
    if not ids and tx_sync_id:
        s = str(tx_sync_id).strip()
        if s:
            ids.append(s)
    if not ids:
        raise HTTPException(
            status_code=400, detail="tx_sync_ids required when link_mode=existing"
        )
    for sid in ids:
        _require_existing_tx(db, ledger, sid)
    return ids


def _create_tx(db: Session, user: User, ledger: Ledger, payload: dict) -> str:
    """生成一笔交易并落 sync_changes + projection,返回 tx_sync_id。"""
    _snap, new_tx_id = _mutate_create_tx({"items": [], "count": 0}, payload)
    item = _snap["items"][0]
    change_row = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=new_tx_id,
        action="upsert",
        payload_json=item,
        updated_at=datetime.now(timezone.utc),
        updated_by_device_id="items",
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


def _delete_tx(db: Session, ledger: Ledger, tx_sync_id: Optional[str]) -> None:
    """删除本功能新建的交易(连同 SyncChange)。existing 关联交易不删。"""
    if not tx_sync_id:
        return
    delete_tx(db, ledger_id=ledger.id, sync_id=tx_sync_id)
    db.execute(
        delete(SyncChange).where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "transaction",
            SyncChange.entity_sync_id == tx_sync_id,
        )
    )


def _flow_out(db: Session, flow: ItemFlow) -> ItemFlowOut:
    tx_ids = _parse_tx_ids(getattr(flow, "tx_sync_ids_json", None))
    return ItemFlowOut(
        id=flow.id,
        item_id=flow.item_id,
        ledger_id=flow.ledger_id,
        flow_type=flow.flow_type,
        amount=float(flow.amount or 0.0),
        happened_at=flow.happened_at,
        category_name=flow.category_name,
        note=flow.note,
        link_mode=flow.link_mode,
        tx_sync_id=flow.tx_sync_id,
        tx_sync_ids=tx_ids,
        tx_sync_amounts=_linked_tx_amounts(db, flow.ledger_id, tx_ids),
        tx_sync_details=_linked_tx_details(db, flow.ledger_id, tx_ids),
        account_id=flow.account_id,
        account_name=flow.account_name,
        attachments=_parse_attachments(flow.attachments_json),
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


def _day_metrics(mode: str, value: float, days_exact: float, days_calendar: int) -> Optional[float]:
    if mode == "none":
        return None
    if mode == "daily_avg":
        return round(value / max(days_exact, 1.0), 2)
    if mode == "per_day":
        return round(value / max(days_calendar, 1), 2)
    return None


def _item_out(db: Session, item: Item, flows: Optional[list[ItemFlow]] = None) -> ItemOut:
    if flows is None:
        flows = list(
            db.scalars(select(ItemFlow).where(ItemFlow.item_id == item.id)).all()
        )
    total_income = sum(float(f.amount or 0.0) for f in flows if f.flow_type == "income")
    total_expense = sum(float(f.amount or 0.0) for f in flows if f.flow_type == "expense")
    net_cost = float(item.price or 0.0) + total_expense - total_income
    now = datetime.now(timezone.utc)
    purchased = _utc(item.purchased_at) or now
    delta = (now - purchased).total_seconds()
    days_exact = max(delta / 86400.0, 0.0)
    days_calendar = max((now.date() - purchased.date()).days + 1, 1)
    tx_ids = _parse_tx_ids(getattr(item, "tx_sync_ids_json", None))
    return ItemOut(
        id=item.id,
        ledger_id=item.ledger_id,
        name=item.name,
        price=float(item.price or 0.0),
        purchased_at=item.purchased_at,
        purchase_channel=item.purchase_channel,
        category_name=item.category_name,
        location=item.location,
        link_mode=item.link_mode,
        tx_sync_id=item.tx_sync_id,
        tx_sync_ids=tx_ids,
        tx_sync_amounts=_linked_tx_amounts(db, item.ledger_id, tx_ids),
        tx_sync_details=_linked_tx_details(db, item.ledger_id, tx_ids),
        account_id=item.account_id,
        account_name=item.account_name,
        average_mode=item.average_mode,
        target_cost_mode=item.target_cost_mode,
        note=item.note,
        attachments=_parse_attachments(item.attachments_json),
        total_income=round(total_income, 2),
        total_expense=round(total_expense, 2),
        net_cost=round(net_cost, 2),
        flow_count=len(flows),
        days_held=round(days_exact, 2),
        average_cost=_day_metrics(item.average_mode, net_cost, days_exact, days_calendar),
        target_cost=_day_metrics(item.target_cost_mode, float(item.price or 0.0), days_exact, days_calendar),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ---------------- 交易 ⇄ 物品 关联(0044) ----------------


def link_tx_to_item(
    db: Session,
    user: User,
    ledger: Ledger,
    tx_sync_id: str,
    item_id: Optional[str],
    payload: Optional[dict] = None,
) -> Optional[ItemFlow]:
    """把一笔交易关联到物品(0044):新建/编辑账单时选物品。

    语义:该交易成为这件物品的一条**支出/收入流水**(ItemFlow),
    这样物品的收支统计、净成本自动包含它,反向链接也能查到。

    幂等:同一 tx_sync_id 在同一物品下只保留一条 ItemFlow;换物品 = 移动。
    item_id 为空 → 解除关联(删除之前由该交易产生的 ItemFlow)。
    """
    payload = payload or {}

    # 先解掉旧的关联(换物品 / 解除关联)。
    old_flows = list(
        db.scalars(
            select(ItemFlow).where(
                ItemFlow.ledger_id == ledger.id,
                ItemFlow.user_id == user.id,
                ItemFlow.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    if not item_id:
        for f in old_flows:
            db.delete(f)
        if old_flows:
            db.commit()
        return None

    item = db.get(Item, item_id)
    if item is None or item.user_id != user.id or item.ledger_id != ledger.id:
        raise HTTPException(status_code=404, detail="item not found")

    # 已经挂在别的物品下 → 移过来(旧的那条删掉)。
    for f in old_flows:
        if f.item_id != item.id:
            db.delete(f)

    # 编辑场景可能只改 item_id(不带金额/时间/类型)→ 从投影读交易当前状态,
    # 避免把已有流水清空或把收入当成支出。
    tx_row = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.sync_id == tx_sync_id,
        )
    )
    tx_type = str(payload.get("tx_type") or "")
    if not tx_type and tx_row is not None:
        tx_type = str(tx_row.tx_type or "")
    flow_type = "income" if tx_type == "income" else "expense"

    amount = payload.get("amount")
    happened = _utc(payload.get("happened_at"))
    category_name = payload.get("category_name")
    note = payload.get("note")
    account_id = payload.get("account_id")
    account_name = payload.get("account_name")
    if tx_row is not None:
        if amount is None:
            amount = tx_row.amount
        if happened is None:
            happened = _utc(tx_row.happened_at)
        if category_name is None:
            category_name = tx_row.category_name
        if account_id is None:
            account_id = tx_row.account_sync_id
        if account_name is None:
            account_name = tx_row.account_name
    amount = float(amount or 0.0)
    if happened is None:
        happened = datetime.now(timezone.utc)
    category_name = category_name or None
    note = note or None
    account_id = account_id or None
    account_name = account_name or None

    existing = next((f for f in old_flows if f.item_id == item.id), None)
    if existing is not None:
        existing.flow_type = flow_type
        existing.amount = amount
        existing.happened_at = happened
        existing.category_name = category_name
        existing.note = note
        existing.account_id = account_id
        existing.account_name = account_name
        existing.link_mode = "existing"
        existing.tx_sync_ids_json = _dump_tx_ids([tx_sync_id])
        existing.tx_sync_id = tx_sync_id
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    flow = ItemFlow(
        user_id=user.id,
        ledger_id=ledger.id,
        item_id=item.id,
        flow_type=flow_type,
        amount=amount,
        happened_at=happened,
        category_name=category_name,
        note=note,
        link_mode="existing",
        tx_sync_id=tx_sync_id,
        tx_sync_ids_json=_dump_tx_ids([tx_sync_id]),
        account_id=account_id,
        account_name=account_name,
        attachments_json=None,
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return flow


# ---------------- Items ----------------

@items_router.get("", response_model=list[ItemOut])
def list_items(
    ledger_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ItemOut]:
    ledger = _require_ledger(db, current_user, ledger_id)
    items = list(
        db.scalars(
            select(Item).where(
                Item.ledger_id == ledger.id, Item.user_id == current_user.id
            ).order_by(Item.purchased_at.desc())
        ).all()
    )
    if not items:
        return []
    flows = list(
        db.scalars(
            select(ItemFlow).where(
                ItemFlow.ledger_id == ledger.id,
                ItemFlow.item_id.in_([i.id for i in items]),
            )
        ).all()
    )
    by_item: dict[str, list[ItemFlow]] = {}
    for f in flows:
        by_item.setdefault(f.item_id, []).append(f)
    return [_item_out(db, i, by_item.get(i.id, [])) for i in items]


@items_router.post("", response_model=ItemOut, status_code=201)
def create_item(
    req: ItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemOut:
    ledger = _require_ledger(db, current_user, req.ledger_id)
    purchased = _utc(req.purchased_at) or datetime.now(timezone.utc)
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    tx_sync_ids: list[str] = []

    if req.link_mode == "new":
        account_id, account_name = _resolve_account(db, current_user, req.account_id)
        payload = {
            "tx_type": "expense",
            "amount": req.price,
            "happened_at": purchased.isoformat(),
            "account_id": account_id,
            "account_name": account_name,
            "category_name": req.category_name or "购物",
            "category_kind": "expense",
            "note": req.note or f"购入物品「{req.name}」",
            "attachments": req.attachments or [],
        }
        tx_sync_id = _create_tx(db, current_user, ledger, payload)
        tx_sync_ids = [tx_sync_id]
    elif req.link_mode == "existing":
        # 可关联**多笔**已有交易(0040):一件物品的购入可能由多笔账单组成。
        tx_sync_ids = _resolve_link_tx_ids(
            db, ledger, tx_sync_ids=req.tx_sync_ids, tx_sync_id=req.tx_sync_id
        )
        tx_sync_id = tx_sync_ids[0]

    # 0044:关联了已有流水 → 价格自动 = 各笔流水金额之和(成本汇总)。
    # 用户手动指定过价格(auto_price=False)时以用户值为准。
    price = req.price
    if req.link_mode == "existing" and tx_sync_ids and req.auto_price:
        price = _linked_tx_total(db, ledger.id, tx_sync_ids)

    item = Item(
        user_id=current_user.id,
        ledger_id=ledger.id,
        name=req.name,
        price=price,
        purchased_at=purchased,
        purchase_channel=req.purchase_channel,
        category_name=req.category_name,
        location=req.location,
        link_mode=req.link_mode,
        tx_sync_id=tx_sync_id,
        tx_sync_ids_json=_dump_tx_ids(tx_sync_ids),
        account_id=account_id,
        account_name=account_name,
        average_mode=req.average_mode,
        target_cost_mode=req.target_cost_mode,
        note=req.note,
        attachments_json=_dump_attachments(req.attachments),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_out(db, item, [])


@items_router.get("/{item_id}", response_model=ItemOut)
def get_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemOut:
    item = db.get(Item, item_id)
    if item is None or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="item not found")
    return _item_out(db, item)


@items_router.patch("/{item_id}", response_model=ItemOut)
def update_item(
    item_id: str,
    req: ItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemOut:
    item = db.get(Item, item_id)
    if item is None or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="item not found")
    data = req.model_dump(exclude_unset=True)
    if "purchased_at" in data:
        data["purchased_at"] = _utc(data["purchased_at"])
    if "attachments" in data:
        data["attachments_json"] = _dump_attachments(data.pop("attachments"))
    auto_price = data.pop("auto_price", None)
    link_mode = data.pop("link_mode", None)
    tx_sync_id_single = data.pop("tx_sync_id", None)
    account_id_in = data.pop("account_id", None)
    account_name_in = data.pop("account_name", None)

    ledger = _require_ledger(db, current_user, item.ledger_id)

    # 0044:改关联方式 / 关联流水 → 价格按「实付合计 + 优惠合计」自动重算。
    if link_mode is not None and link_mode != "existing":
        # 切到 none / new:清空已有流水关联(新建交易的关联在下面单独处理)。
        item.tx_sync_ids_json = None
        item.tx_sync_id = None
    if "tx_sync_ids" in data:
        # 替换关联的交易流水(0040):传列表 = 替换全部关联;传空列表 = 清空关联。
        raw_ids = data.pop("tx_sync_ids")
        if raw_ids:
            ids = _resolve_link_tx_ids(
                db, ledger, tx_sync_ids=raw_ids, tx_sync_id=None
            )
            item.tx_sync_ids_json = _dump_tx_ids(ids)
            item.tx_sync_id = ids[0]
            if item.link_mode == "none":
                item.link_mode = "existing"
        else:
            item.tx_sync_ids_json = None
            item.tx_sync_id = None
            if item.link_mode == "existing":
                item.link_mode = "none"
    elif tx_sync_id_single:
        ids = _resolve_link_tx_ids(
            db, ledger, tx_sync_ids=[tx_sync_id_single], tx_sync_id=None
        )
        item.tx_sync_ids_json = _dump_tx_ids(ids)
        item.tx_sync_id = ids[0]
        if item.link_mode == "none":
            item.link_mode = "existing"

    if link_mode is not None:
        item.link_mode = link_mode
    if account_id_in is not None:
        item.account_id, item.account_name = _resolve_account(
            db, current_user, account_id_in
        )
    elif account_name_in is not None:
        item.account_name = account_name_in

    # 自动汇总成本:关联的流水变了 → 价格重算为各笔金额之和。
    if auto_price is None:
        auto_price = True
    if auto_price and item.link_mode == "existing":
        ids = _parse_tx_ids(getattr(item, "tx_sync_ids_json", None))
        item.price = _linked_tx_total(db, ledger.id, ids)

    for key, value in data.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return _item_out(db, item)


@items_router.delete("/{item_id}", status_code=204)
def delete_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    item = db.get(Item, item_id)
    if item is None or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="item not found")
    ledger = db.get(Ledger, item.ledger_id)
    # 删除物品自己 new 出来的交易（existing 关联交易是外部数据，不删）
    if item.link_mode == "new" and ledger is not None:
        _delete_tx(db, ledger, item.tx_sync_id)
    # 删除所有流水及其 new 交易
    flows = list(db.scalars(select(ItemFlow).where(ItemFlow.item_id == item.id)).all())
    for f in flows:
        if f.link_mode == "new" and ledger is not None:
            _delete_tx(db, ledger, f.tx_sync_id)
    db.execute(delete(ItemFlow).where(ItemFlow.item_id == item.id))
    db.delete(item)
    db.commit()


# ---------------- Item flows ----------------

@items_router.get("/{item_id}/flows", response_model=list[ItemFlowOut])
def list_item_flows(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ItemFlowOut]:
    item = db.get(Item, item_id)
    if item is None or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="item not found")
    flows = list(
        db.scalars(
            select(ItemFlow).where(ItemFlow.item_id == item.id).order_by(ItemFlow.happened_at.desc())
        ).all()
    )
    return [_flow_out(db, f) for f in flows]


@items_router.post("/{item_id}/flows", response_model=ItemFlowOut, status_code=201)
def create_item_flow(
    item_id: str,
    req: ItemFlowCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemFlowOut:
    item = db.get(Item, item_id)
    if item is None or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="item not found")
    if req.item_id != item_id:
        raise HTTPException(status_code=400, detail="item_id mismatch")
    ledger = _require_ledger(db, current_user, item.ledger_id)
    happened = _utc(req.happened_at) or datetime.now(timezone.utc)
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    tx_sync_ids: list[str] = []

    if req.link_mode == "new":
        account_id, account_name = _resolve_account(db, current_user, req.account_id)
        payload = {
            "tx_type": req.flow_type,
            "amount": req.amount,
            "happened_at": happened.isoformat(),
            "account_id": account_id,
            "account_name": account_name,
            "category_name": req.category_name or ("其他收入" if req.flow_type == "income" else "其他支出"),
            "category_kind": req.flow_type,
            "note": req.note or f"物品「{item.name}」",
            "attachments": req.attachments or [],
        }
        tx_sync_id = _create_tx(db, current_user, ledger, payload)
        tx_sync_ids = [tx_sync_id]
    elif req.link_mode == "existing":
        # 可关联**多笔**已有交易(0040)。
        tx_sync_ids = _resolve_link_tx_ids(
            db, ledger, tx_sync_ids=req.tx_sync_ids, tx_sync_id=req.tx_sync_id
        )
        tx_sync_id = tx_sync_ids[0]

    # 0044:关联已有流水 → 金额自动 = 各笔流水金额之和。
    amount = req.amount
    if req.link_mode == "existing" and tx_sync_ids and req.auto_amount:
        amount = _linked_tx_total(db, ledger.id, tx_sync_ids)

    flow = ItemFlow(
        user_id=current_user.id,
        ledger_id=ledger.id,
        item_id=item.id,
        flow_type=req.flow_type,
        amount=amount,
        happened_at=happened,
        category_name=req.category_name,
        note=req.note,
        link_mode=req.link_mode,
        tx_sync_id=tx_sync_id,
        tx_sync_ids_json=_dump_tx_ids(tx_sync_ids),
        account_id=account_id,
        account_name=account_name,
        attachments_json=_dump_attachments(req.attachments),
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return _flow_out(db, flow)


@items_router.patch("/flows/{flow_id}", response_model=ItemFlowOut)
def update_item_flow(
    flow_id: str,
    req: ItemFlowUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemFlowOut:
    flow = db.get(ItemFlow, flow_id)
    if flow is None or flow.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="flow not found")
    data = req.model_dump(exclude_unset=True)
    if "happened_at" in data:
        data["happened_at"] = _utc(data["happened_at"])
    if "attachments" in data:
        data["attachments_json"] = _dump_attachments(data.pop("attachments"))
    auto_amount = data.pop("auto_amount", None)
    link_mode = data.pop("link_mode", None)
    tx_sync_id_single = data.pop("tx_sync_id", None)

    ledger = db.get(Ledger, flow.ledger_id)
    if link_mode is not None and link_mode != "existing":
        flow.tx_sync_ids_json = None
        flow.tx_sync_id = None
    if "tx_sync_ids" in data:
        # 替换关联的交易流水(0040):传列表 = 替换全部;传空列表 = 清空。
        raw_ids = data.pop("tx_sync_ids")
        if raw_ids:
            ids = _resolve_link_tx_ids(
                db, ledger, tx_sync_ids=raw_ids, tx_sync_id=None
            )
            flow.tx_sync_ids_json = _dump_tx_ids(ids)
            flow.tx_sync_id = ids[0]
            if flow.link_mode == "none":
                flow.link_mode = "existing"
        else:
            flow.tx_sync_ids_json = None
            flow.tx_sync_id = None
            if flow.link_mode == "existing":
                flow.link_mode = "none"
    elif tx_sync_id_single:
        ids = _resolve_link_tx_ids(
            db, ledger, tx_sync_ids=[tx_sync_id_single], tx_sync_id=None
        )
        flow.tx_sync_ids_json = _dump_tx_ids(ids)
        flow.tx_sync_id = ids[0]
        if flow.link_mode == "none":
            flow.link_mode = "existing"
    if link_mode is not None:
        flow.link_mode = link_mode

    # 0044:关联的流水变了 → 金额重算为各笔金额之和。
    if auto_amount is None:
        auto_amount = True
    if auto_amount and flow.link_mode == "existing" and ledger is not None:
        ids = _parse_tx_ids(getattr(flow, "tx_sync_ids_json", None))
        flow.amount = _linked_tx_total(db, ledger.id, ids)

    for key, value in data.items():
        setattr(flow, key, value)
    db.commit()
    db.refresh(flow)
    return _flow_out(db, flow)


@items_router.delete("/flows/{flow_id}", status_code=204)
def delete_item_flow(
    flow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    flow = db.get(ItemFlow, flow_id)
    if flow is None or flow.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="flow not found")
    if flow.link_mode == "new":
        ledger = db.get(Ledger, flow.ledger_id)
        if ledger is not None:
            _delete_tx(db, ledger, flow.tx_sync_id)
    db.delete(flow)
    db.commit()


# ---------------- 流水变更 → 物品同步(0042) ----------------


def sync_item_from_flow_edit(
    db: Session, *, ledger_id: str, tx_sync_id: str, new_amount: float
) -> list[str]:
    """交易流水金额变更 → 同步引用它的物品流水金额(0042)。

    物品流水(ItemFlow)的 amount 是**快照**,关联的流水被改金额后必须同步,
    否则会出现「流水 300 / 物品流水 250」,导致净成本/均值算错。

    物品本身(Item.price)不跟着改:价格是用户独立填写的购入价(可能由多笔
    定金/尾款汇总而来),流水只是「关联标记」,不构成价格来源。
    """
    touched: list[str] = []
    rows = list(
        db.scalars(
            select(ItemFlow).where(
                ItemFlow.ledger_id == ledger_id,
                ItemFlow.tx_sync_id == tx_sync_id,
            )
        ).all()
    )
    for row in rows:
        row.amount = round(float(new_amount), 2)
        row.updated_at = datetime.now(timezone.utc)
        touched.append(f"item_flow:{row.id[:8]}")
    if rows:
        db.commit()
    return touched
