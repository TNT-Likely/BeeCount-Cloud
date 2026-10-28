"""定期存款 write 端点(0021,Cloud issue #80)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/time-deposits。
实体不进 snapshot,直连投影表走 ``_commit_entity_write`` 快路径(结构对齐
transactions.py 的 fast path:锁 + SyncChange + 同事务投影 upsert + 广播)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ... import projection
from ...entity_extensions import time_deposit_to_camel, validate_time_deposit
from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/time-deposits",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_time_deposit(
    ledger_id: str,
    req: WriteTimeDepositCreateRequest,
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
    mutate_payload = time_deposit_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_time_deposit_create",
        entity_type="time_deposit",
        entity_id=None,
        action="upsert",
        model=ReadTimeDepositProjection,
        upsert_fn=projection.upsert_time_deposit,
        delete_fn=projection.delete_time_deposit,
        prefix="td",
        validate_fn=validate_time_deposit,
    )


@router.patch(
    "/ledgers/{ledger_id}/time-deposits/{deposit_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_time_deposit(
    ledger_id: str,
    deposit_id: str,
    req: WriteTimeDepositUpdateRequest,
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
    mutate_payload = time_deposit_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_time_deposit_update",
        entity_type="time_deposit",
        entity_id=deposit_id,
        action="upsert",
        model=ReadTimeDepositProjection,
        upsert_fn=projection.upsert_time_deposit,
        delete_fn=projection.delete_time_deposit,
        prefix="td",
        validate_fn=validate_time_deposit,
    )


@router.delete(
    "/ledgers/{ledger_id}/time-deposits/{deposit_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_time_deposit(
    ledger_id: str,
    deposit_id: str,
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
        audit_action="web_time_deposit_delete",
        entity_type="time_deposit",
        entity_id=deposit_id,
        action="delete",
        model=ReadTimeDepositProjection,
        upsert_fn=projection.upsert_time_deposit,
        delete_fn=projection.delete_time_deposit,
        prefix="td",
        validate_fn=validate_time_deposit,
    )
