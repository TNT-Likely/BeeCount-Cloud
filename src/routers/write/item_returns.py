"""归物(退货/退还物品)write 端点(0021,App issue #465 退款诉求)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/item-returns。
同 time_deposits:实体不进 snapshot,直连投影表走 ``_commit_entity_write``。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ... import projection
from ...entity_extensions import item_return_to_camel, validate_item_return
from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/item-returns",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_item_return(
    ledger_id: str,
    req: WriteItemReturnCreateRequest,
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
    mutate_payload = item_return_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_item_return_create",
        entity_type="item_return",
        entity_id=None,
        action="upsert",
        model=ReadItemReturnProjection,
        upsert_fn=projection.upsert_item_return,
        delete_fn=projection.delete_item_return,
        prefix="ir",
        validate_fn=validate_item_return,
    )


@router.patch(
    "/ledgers/{ledger_id}/item-returns/{return_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_item_return(
    ledger_id: str,
    return_id: str,
    req: WriteItemReturnUpdateRequest,
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
    mutate_payload = item_return_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_item_return_update",
        entity_type="item_return",
        entity_id=return_id,
        action="upsert",
        model=ReadItemReturnProjection,
        upsert_fn=projection.upsert_item_return,
        delete_fn=projection.delete_item_return,
        prefix="ir",
        validate_fn=validate_item_return,
    )


@router.delete(
    "/ledgers/{ledger_id}/item-returns/{return_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_item_return(
    ledger_id: str,
    return_id: str,
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
        audit_action="web_item_return_delete",
        entity_type="item_return",
        entity_id=return_id,
        action="delete",
        model=ReadItemReturnProjection,
        upsert_fn=projection.upsert_item_return,
        delete_fn=projection.delete_item_return,
        prefix="ir",
        validate_fn=validate_item_return,
    )
