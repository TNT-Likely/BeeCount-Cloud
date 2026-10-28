"""借款 write 端点(0023)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/loans。
实体不进 snapshot,直连投影表走 ``_commit_entity_write`` 快路径(对齐
refunds.py:锁 + SyncChange + 同事务投影 upsert + 广播)。

业务校验全是纯函数(validate_loan),不需要跨行查库 —— 还款进度 /
逾期是读端点派生,还款金额上限等强一致规则可在后续按需补充。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ... import projection
from ...entity_extensions import loan_to_camel, validate_loan
from ...models import ReadLoanProjection
from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/loans",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_loan(
    ledger_id: str,
    req: WriteLoanCreateRequest,
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
    mutate_payload = loan_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_loan_create",
        entity_type="loan",
        entity_id=None,
        action="upsert",
        model=ReadLoanProjection,
        upsert_fn=projection.upsert_loan,
        delete_fn=projection.delete_loan,
        prefix="ln",
        validate_fn=validate_loan,
    )


@router.patch(
    "/ledgers/{ledger_id}/loans/{loan_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_loan(
    ledger_id: str,
    loan_id: str,
    req: WriteLoanUpdateRequest,
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
    mutate_payload = loan_to_camel(_payload_with_actor(payload, current_user, ledger=ledger))
    return await _commit_entity_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=mutate_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_loan_update",
        entity_type="loan",
        entity_id=loan_id,
        action="upsert",
        model=ReadLoanProjection,
        upsert_fn=projection.upsert_loan,
        delete_fn=projection.delete_loan,
        prefix="ln",
        validate_fn=validate_loan,
    )


@router.delete(
    "/ledgers/{ledger_id}/loans/{loan_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_loan(
    ledger_id: str,
    loan_id: str,
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
        audit_action="web_loan_delete",
        entity_type="loan",
        entity_id=loan_id,
        action="delete",
        model=ReadLoanProjection,
        upsert_fn=projection.upsert_loan,
        delete_fn=projection.delete_loan,
        prefix="ln",
        validate_fn=validate_loan,
    )
