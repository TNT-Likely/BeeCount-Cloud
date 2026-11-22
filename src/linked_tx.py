"""关联交易流水的公共查询(0046)。

各业务模块(物品 / 借款 / 还款 / 储蓄罐 / 定期存款 / 报销)都要把
「我引用了哪几条交易流水」用可读形式展示出来。这里提供统一的查询入口:

    details = linked_tx_details(db, ledger_id, ["tx_a", "tx_b"])

返回顺序与传入的 sync_id 顺序一致;流水已被删除的 sync_id 也会列出
(其余字段为空),前端据此显示「已删除」而不是静默少一条。
"""
from __future__ import annotations

from typing import Iterable, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ReadTxProjection
from .schemas_linked import LinkedTxOut


def _ledger_filter(ledger_id: Union[str, Iterable[str], None]):
    """构造 ledger 过滤条件。

    - str → 单账本;
    - 可迭代 → 多个账本(跨账本共享的实体列表);
    - None → 不按账本过滤(syncId 全局唯一,安全)。
    """
    if ledger_id is None:
        return None
    if isinstance(ledger_id, str):
        ids = [ledger_id]
    else:
        ids = [str(x) for x in ledger_id if str(x or "").strip()]
    if not ids:
        return None
    if len(ids) == 1:
        return ReadTxProjection.ledger_id == ids[0]
    return ReadTxProjection.ledger_id.in_(ids)


def _dedupe(ids: Iterable[Optional[str]]) -> list[str]:
    out: list[str] = []
    for raw in ids:
        s = str(raw or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def linked_tx_details(
    db: Session,
    ledger_id: Union[str, Iterable[str], None],
    tx_sync_ids: Iterable[Optional[str]],
) -> list[LinkedTxOut]:
    """查关联交易流水的可读摘要。

    - 一次 IN 查询取回全部流水(避免 N+1);
    - 顺序与传入顺序一致;
    - 流水已删除 → 仍返回该 sync_id,字段留空。
    """
    ids = _dedupe(tx_sync_ids)
    if not ids:
        return []
    rows = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.sync_id.in_(ids),
            *([_ledger_filter(ledger_id)] if _ledger_filter(ledger_id) is not None else []),
        )
    ).all()
    by_id = {r.sync_id: r for r in rows}
    out: list[LinkedTxOut] = []
    for sid in ids:
        r = by_id.get(sid)
        if r is None:
            out.append(LinkedTxOut(sync_id=sid))
            continue
        out.append(
            LinkedTxOut(
                sync_id=sid,
                tx_type=str(r.tx_type or "expense"),
                amount=float(r.amount or 0.0),
                happened_at=r.happened_at,
                note=r.note,
                category_name=r.category_name,
                category_kind=r.category_kind,
                account_name=r.account_name,
                currency_code=r.currency_code,
                exclude_from_stats=bool(r.exclude_from_stats),
            )
        )
    return out


def linked_tx_amounts(
    db: Session,
    ledger_id: Union[str, Iterable[str], None],
    tx_sync_ids: Iterable[Optional[str]],
) -> dict[str, float]:
    """关联交易流水的金额快照(syncId → 金额)。

    用于「成本自动汇总」:前端回填关联项时用它算合计,不必再单独查流水。
    """
    ids = _dedupe(tx_sync_ids)
    if not ids:
        return {}
    rows = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.sync_id.in_(ids),
            *([_ledger_filter(ledger_id)] if _ledger_filter(ledger_id) is not None else []),
        )
    ).all()
    return {r.sync_id: float(r.amount or 0.0) for r in rows}


def linked_tx_map(
    db: Session,
    ledger_id: Union[str, Iterable[str], None],
    tx_sync_ids: Iterable[Optional[str]],
) -> dict[str, LinkedTxOut]:
    """批量查关联流水摘要 → {sync_id: LinkedTxOut}。

    列表页一次查完所有实体的关联流水,避免 N+1。

    **查不到的 sync_id 也会返回占位**(字段留空)—— 关联的流水被删除后,
    详情页仍要显示这一行并标注「已删除」,而不是静默消失。
    """
    ids = _dedupe(tx_sync_ids)
    if not ids:
        return {}
    rows = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.sync_id.in_(ids),
            *([_ledger_filter(ledger_id)] if _ledger_filter(ledger_id) is not None else []),
        )
    ).all()
    out: dict[str, LinkedTxOut] = {}
    for sid in ids:
        r = next((x for x in rows if x.sync_id == sid), None)
        if r is None:
            out[sid] = LinkedTxOut(sync_id=sid)
            continue
        out[sid] = LinkedTxOut(
            sync_id=r.sync_id,
            tx_type=str(r.tx_type or "expense"),
            amount=float(r.amount or 0.0),
            happened_at=r.happened_at,
            note=r.note,
            category_name=r.category_name,
            category_kind=r.category_kind,
            account_name=r.account_name,
            currency_code=r.currency_code,
            exclude_from_stats=bool(r.exclude_from_stats),
        )
    return out


def linked_tx_total(
    db: Session,
    ledger_id: Union[str, Iterable[str], None],
    tx_sync_ids: Iterable[Optional[str]],
) -> float:
    """关联交易流水的金额合计(成本汇总)。"""
    return round(sum(linked_tx_amounts(db, ledger_id, tx_sync_ids).values()), 2)
