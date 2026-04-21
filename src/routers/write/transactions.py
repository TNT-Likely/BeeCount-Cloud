"""Transactions write endpoints.

POST / PATCH / DELETE for /ledgers/{ledger_id}/transactions(ledgers 自身除外)。
依赖 `._shared` 里的 _commit_write / _prepare_write / normalize helper /
WRITE 响应表。Endpoint 自身只管参数校验 + mutate lambda 的构造。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/transactions",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_tx(
    ledger_id: str,
    req: WriteTransactionCreateRequest,
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
    # 旧架构这里要跑 _resolve_tx_dictionary_payload 去 UserAccount/Category/Tag
    # 三张投影表里查 id / 建 row。新架构所有实体都是 snapshot 里的 syncId,
    # web UI 下拉选项也从 snapshot 读,account_id / category_id / tag_ids 直接
    # 是 syncId,不再需要任何投影表。payload 直接传给 snapshot_mutator。
    mutate_payload = _payload_with_actor(payload, current_user)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_create",
        mutate=lambda snapshot: create_transaction(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/transactions/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_tx(
    ledger_id: str,
    tx_id: str,
    req: WriteTransactionUpdateRequest,
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
    _assert_can_modify_entity(
        db=db,
        ledger=ledger,
        current_user=current_user,
        entity_sync_id=tx_id,
    )
    # 跟 create_tx 同样改动:account/category/tag 的 id 直接走 snapshot syncId,
    # 不再经 UserAccount 投影表。
    mutate_payload = _payload_with_actor(payload, current_user)
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_update",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="upsert",
    )


@router.delete(
    "/ledgers/{ledger_id}/transactions/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_tx(
    ledger_id: str,
    tx_id: str,
    req: WriteEntityDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    mutate_payload = _payload_with_actor(payload, current_user)
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
    _assert_can_modify_entity(
        db=db,
        ledger=ledger,
        current_user=current_user,
        entity_sync_id=tx_id,
    )
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_delete",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="delete",
    )


