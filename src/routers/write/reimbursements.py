"""报销 write 端点(0023)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/reimbursements。
实体不进 snapshot,直连投影表走 ``_commit_entity_write`` 快路径(对齐
refunds.py / loans.py:锁 + SyncChange + 同事务投影 upsert + 广播)。

业务校验全是纯函数(validate_reimbursement),不需要跨行查库 —— 报销金额
上限等强一致规则可在后续按需补充。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ... import projection
from ...entity_extensions import reimbursement_to_camel, validate_reimbursement
from ...models import ReadReimbursementProjection
from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/reimbursements",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_reimbursement(
    ledger_id: str,
    req: WriteReimbursementCreateRequest,
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
    mutate_payload = reimbursement_to_camel(
        _payload_with_actor(payload, current_user, ledger=ledger)
    )
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_reimbursement_create",
        entity_type="reimbursement",
        entity_id=None,
        action="upsert",
        model=ReadReimbursementProjection,
        upsert_fn=projection.upsert_reimbursement,
        delete_fn=projection.delete_reimbursement,
        prefix="rb",
        validate_fn=validate_reimbursement,
    )


@router.patch(
    "/ledgers/{ledger_id}/reimbursements/{reimbursement_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_reimbursement(
    ledger_id: str,
    reimbursement_id: str,
    req: WriteReimbursementUpdateRequest,
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
    mutate_payload = reimbursement_to_camel(
        _payload_with_actor(payload, current_user, ledger=ledger)
    )
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_reimbursement_update",
        entity_type="reimbursement",
        entity_id=reimbursement_id,
        action="upsert",
        model=ReadReimbursementProjection,
        upsert_fn=projection.upsert_reimbursement,
        delete_fn=projection.delete_reimbursement,
        prefix="rb",
        validate_fn=validate_reimbursement,
    )


@router.delete(
    "/ledgers/{ledger_id}/reimbursements/{reimbursement_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_reimbursement(
    ledger_id: str,
    reimbursement_id: str,
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
        audit_action="web_reimbursement_delete",
        entity_type="reimbursement",
        entity_id=reimbursement_id,
        action="delete",
        model=ReadReimbursementProjection,
        upsert_fn=projection.upsert_reimbursement,
        delete_fn=projection.delete_reimbursement,
        prefix="rb",
        validate_fn=validate_reimbursement,
    )
