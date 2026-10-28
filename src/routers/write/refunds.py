"""交易退款 write 端点(0022,App issue #465 退款功能)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/refunds。
实体不进 snapshot,直连投影表走 ``_commit_entity_write`` 快路径(结构对齐
time_deposits.py:锁 + SyncChange + 同事务投影 upsert + 广播)。

比定期存款/归物多两条查库校验(见 ``_make_refund_validator``):
- 原始交易必须存在且是支出(退款只对支出有意义);
- 同一原单的有效退款累计(非 cancelled)不得超过原单金额。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select

from ... import projection
from ...entity_extensions import refund_to_camel, validate_refund
from ...models import ReadTxProjection
from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()

# 金额容差,跟 snapshot_mutator._validate_payment_legs 同款(浮点累计误差)。
_AMOUNT_TOLERANCE = 0.011


def _make_refund_validator(db, ledger, entity_id: str | None):
    """构造带查库校验的 validate_fn(闭包捕获当前请求的 db / ledger)。

    ``_commit_entity_write`` 的 validate_fn 签名是 ``(payload, *, creating)``,
    纯函数校验(validate_refund)之外的两条规则需要查库:

    1. originalTxId 对应交易必须存在且 tx_type='expense';
       顺手把原单金额快照进 originalAmount、缺省币种取原交易币种。
    2. 同单有效退款(非 cancelled,排除正在编辑的自身)累计 + 本笔金额
       不得超过原单金额 —— 支持部分退/多次退,但不允许超退。
    """

    def validate(payload: dict, *, creating: bool) -> dict:
        out = dict(payload)
        original_tx_id = str(out.get("originalTxId") or "").strip()
        if not original_tx_id:
            raise ValueError("write validation failed: originalTxId is required")

        tx = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.sync_id == original_tx_id,
            )
        )
        if tx is None:
            raise ValueError(
                "write validation failed: original transaction not found"
            )
        if (tx.tx_type or "") != "expense":
            raise ValueError(
                "write validation failed: refunds are only supported "
                "for expense transactions"
            )
        original_amount = float(tx.amount or 0.0)
        out["originalAmount"] = original_amount
        if not out.get("currency"):
            out["currency"] = tx.currency_code or "CNY"

        merged = validate_refund(out, creating=creating)

        # 累计校验:cancelled 不计入;update 时排除自身那笔。
        already = projection.sum_refunds_for_tx(
            db,
            ledger_id=ledger.id,
            original_tx_id=original_tx_id,
            exclude_sync_id=entity_id,
        )
        total = already + float(merged["amount"])
        if total > original_amount + _AMOUNT_TOLERANCE:
            raise ValueError(
                "write validation failed: refund total "
                f"{total:.2f} exceeds original amount {original_amount:.2f}"
            )
        return merged

    return validate


@router.post(
    "/ledgers/{ledger_id}/refunds",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_refund(
    ledger_id: str,
    req: WriteRefundCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = refund_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_refund_create",
        entity_type="refund",
        entity_id=None,
        action="upsert",
        model=ReadRefundProjection,
        upsert_fn=projection.upsert_refund,
        delete_fn=projection.delete_refund,
        prefix="rf",
        validate_fn=_make_refund_validator(db, ledger, None),
    )


@router.patch(
    "/ledgers/{ledger_id}/refunds/{refund_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_refund(
    ledger_id: str,
    refund_id: str,
    req: WriteRefundUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = refund_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_refund_update",
        entity_type="refund",
        entity_id=refund_id,
        action="upsert",
        model=ReadRefundProjection,
        upsert_fn=projection.upsert_refund,
        delete_fn=projection.delete_refund,
        prefix="rf",
        validate_fn=_make_refund_validator(db, ledger, refund_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/refunds/{refund_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_refund(
    ledger_id: str,
    refund_id: str,
    req: WriteEntityDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_refund_delete",
        entity_type="refund",
        entity_id=refund_id,
        action="delete",
        model=ReadRefundProjection,
        upsert_fn=projection.upsert_refund,
        delete_fn=projection.delete_refund,
        prefix="rf",
        validate_fn=_make_refund_validator(db, ledger, refund_id),
    )
