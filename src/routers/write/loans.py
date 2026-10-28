"""借款 write 端点(0023)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/loans。
实体不进 snapshot,直连投影表走 ``_commit_entity_write`` 快路径(对齐
refunds.py:锁 + SyncChange + 同事务投影 upsert + 广播)。

业务校验全是纯函数(validate_loan),不需要跨行查库 —— 还款进度 /
逾期是读端点派生,还款金额上限等强一致规则可在后续按需补充。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from starlette.concurrency import run_in_threadpool

from ... import projection
from ...entity_extensions import loan_to_camel, validate_loan
from ...models import ReadLoanProjection
from ...schemas import LoanRepaymentCreateRequest
from ...services.linked_transactions import reconcile_entity_linked_tx
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


def _loan_row_to_camel(row: ReadLoanProjection) -> dict[str, Any]:
    """ReadLoanProjection 行 → 写路径 camel payload(供 validate_loan / upsert)。"""
    return {
        "syncId": row.sync_id,
        "direction": row.direction or "borrowed",
        "counterparty": row.counterparty or "",
        "principal": float(row.principal or 0.0),
        "currency": row.currency or "CNY",
        "interestRate": float(row.interest_rate or 0.0),
        "startedAt": row.started_at.isoformat() if row.started_at else None,
        "dueAt": row.due_at.isoformat() if row.due_at else None,
        "repaidAmount": float(row.repaid_amount or 0.0),
        "status": row.status or "active",
        "accountId": row.account_sync_id,
        "accountName": row.account_name,
        "note": row.note,
        "repayments": projection.parse_loan_repayments(row.repayments_json),
    }


async def _commit_loan_repayment_write(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    loan_id: str,
    op: tuple[str, Any],
    request_payload: dict,
) -> WriteCommitMeta:
    """登记 / 撤销借款还款的锁定写入(0032)。

    必须在持 ledger 写锁、同一事务内完成「读最新借款行 → 追加 / 移除还款明细 →
    重算已还金额与状态 → upsert 借款投影 → 重算关联交易(本金 / 利息流水)」,
    避免并发两次还款相互覆盖还款明细。复用 _commit_entity_write 的锁 / 幂等 /
    广播骨架,但把明细装配放进锁内。
    """

    def _core() -> tuple[WriteCommitMeta, bool]:
        lock_ledger_for_materialize(db, ledger.id)
        now = _utcnow()
        row = db.scalar(
            select(ReadLoanProjection).where(
                ReadLoanProjection.ledger_id == ledger.id,
                ReadLoanProjection.sync_id == loan_id,
            )
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Loan not found")
        principal = float(row.principal or 0.0)
        existing = projection.parse_loan_repayments(row.repayments_json)

        if op[0] == "add":
            # 仅「新增还款」要求在借中(active):已结清 / 已取消不再接受新还款。
            # 撤销还款(remove)无此限制 —— 已结清的借款撤销某笔还款后应能回退为
            # 在借中(见测试:还清后删 rp-1 → 回退 400 / active)。
            if (row.status or "active") != "active":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="loan is not active; cannot register repayment")
            repayment = dict(op[1])
            ramt = float(repayment.get("amount") or 0.0)
            rint = float(repayment.get("interestAmount") or 0.0)
            if ramt <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="repayment amount must be positive")
            if rint < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="repayment interest cannot be negative")
            registered_sum = sum(float(r.get("amount") or 0.0) for r in existing)
            remaining = principal - registered_sum
            if remaining <= 1e-9:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="loan already fully repaid")
            if ramt > remaining + 1e-6:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="repayment amount exceeds remaining balance")
            new_repayments = existing + [repayment]
            new_repaid = registered_sum + ramt
        else:  # remove
            rid = str(op[1])
            new_repayments = [
                r for r in existing if str(r.get("id") or "") != rid
            ]
            if len(new_repayments) == len(existing):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="repayment not found")
            new_repaid = sum(float(r.get("amount") or 0.0) for r in new_repayments)

        new_status = "repaid" if new_repaid >= principal - 1e-9 else "active"

        camel = _loan_row_to_camel(row)
        camel["repaidAmount"] = new_repaid
        camel["status"] = new_status
        camel["repayments"] = new_repayments

        merged = validate_loan(camel, creating=False)

        change_row = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="loan",
            entity_sync_id=loan_id,
            action="upsert",
            payload_json=merged,
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )
        db.add(change_row)
        db.flush()
        projection.upsert_loan(
            db, ledger_id=ledger.id, user_id=ledger.user_id,
            source_change_id=change_row.change_id, payload=merged,
        )
        new_change_id = change_row.change_id

        reconcile_entity_linked_tx(
            db=db, ledger_id=ledger.id, owner_user_id=ledger.user_id,
            actor_user_id=current_user.id, device_id=device_id,
            source_change_id=new_change_id, action="upsert",
            entity_type="loan", entity_id=loan_id, payload=merged,
        )

        db.add(AuditLog(
            user_id=current_user.id, ledger_id=ledger.id, action=audit_action,
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": base_change_id,
                "newChangeId": new_change_id,
                "entityId": loan_id,
            },
        ))

        response = WriteCommitMeta(
            ledger_id=ledger.external_id, base_change_id=base_change_id,
            new_change_id=new_change_id, server_timestamp=now,
            idempotency_replayed=False, entity_id=loan_id,
        )

        request_hash = _hash_request(request.method, request.url.path, request_payload)
        if idempotency_key:
            db.add(SyncPushIdempotency(
                user_id=current_user.id, device_id=device_id,
                idempotency_key=idempotency_key, request_hash=request_hash,
                response_json=response.model_dump(mode="json"),
                created_at=now, expires_at=now + timedelta(hours=24),
            ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if idempotency_key:
                replay = _load_idempotent_response(
                    db, user_id=current_user.id, device_id=device_id,
                    idempotency_key=idempotency_key, request_hash=request_hash)
                if replay is not None:
                    return replay, True
            raise
        return response, False

    response, did_replay = await run_in_threadpool(_core)
    if not did_replay:
        from ...websocket_manager import broadcast_to_ledger

        await broadcast_to_ledger(
            db=db, ws_manager=request.app.state.ws_manager, ledger_id=ledger.id,
            payload={
                "type": "sync_change",
                "ledgerId": ledger.external_id,
                "serverCursor": response.new_change_id,
                "serverTimestamp": response.server_timestamp.isoformat(),
            },
        )
    return response


@router.post(
    "/ledgers/{ledger_id}/loans/{loan_id}/repayments",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_loan_repayment(
    ledger_id: str,
    loan_id: str,
    req: LoanRepaymentCreateRequest,
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
    repayment = {
        "id": req.repayment_id,
        "amount": float(req.amount),
        "interestAmount": float(req.interest_amount or 0.0),
        "repayAccountId": req.repay_account_id,
        "repayAccountName": req.repay_account_name,
        "happenedAt": req.happened_at,
        "note": req.note or "",
    }
    return await _commit_loan_repayment_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_loan_repayment_create",
        loan_id=loan_id,
        op=("add", repayment),
        request_payload=payload,
    )


@router.delete(
    "/ledgers/{ledger_id}/loans/{loan_id}/repayments/{repayment_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_loan_repayment(
    ledger_id: str,
    loan_id: str,
    repayment_id: str,
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
    return await _commit_loan_repayment_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_loan_repayment_delete",
        loan_id=loan_id,
        op=("remove", repayment_id),
        request_payload=payload,
    )
