"""POST /api/v1/write/ledgers/{ledger_id}/transactions/batch/delete — 批量删除交易。

设计:.docs/web-tx-batch-actions.md §4.2

跟 transactions_batch.py(create)同模式:单 snapshot lock + 一次 SyncChange
broadcast + 一次 idempotency。区别:

- 跑 `delete_transaction` mutator(逐个 sync_id)
- 部分失败按 tx 粒度返回 `failed[]`,只在事务级错误才 500
- 不开放 base_change_id 严格校验(用户多选时不应该被并发其它写入卡死;现有
  单笔 DELETE 也用 lenient 模式)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import snapshot_builder
from ...concurrency import lock_ledger_for_materialize
from ...database import get_db
from ...deps import get_current_user
from sqlalchemy import select

from ...models import AuditLog, SyncPushIdempotency, User
from ...snapshot_mutator import delete_transaction
from ._shared import (
    _TRANSACTION_WRITE_ROLES,
    _WRITE_RESPONSES,
    _WRITE_SCOPE_DEP,
    _emit_entity_diffs,
    _hash_request,
    _load_idempotent_response,
    _payload_with_actor,
    _prepare_write,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class BatchTxDeleteRequest(BaseModel):
    tx_ids: list[str] = Field(..., min_length=1, max_length=200)
    base_change_id: int = 0


class BatchTxFailure(BaseModel):
    tx_id: str
    reason: Literal["not_found", "permission_denied", "conflict"]
    message: str | None = None


class BatchTxDeleteResponse(BaseModel):
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    deleted_tx_ids: list[str] = Field(default_factory=list)
    failed: list[BatchTxFailure] = Field(default_factory=list)


@router.post(
    "/ledgers/{ledger_id}/transactions/batch/delete",
    response_model=BatchTxDeleteResponse,
    responses=_WRITE_RESPONSES,
)
async def delete_tx_batch(
    ledger_id: str,
    req: BatchTxDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchTxDeleteResponse:
    payload_for_ide = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload_for_ide,
    )
    if replay:
        # _prepare_write 返回的 replay 是通用 WriteCommitMeta(字段集少),
        # 我们的批量响应字段更多 —— 直接从 DB 拿原始 response_json 重建。
        row = db.scalar(
            select(SyncPushIdempotency).where(
                SyncPushIdempotency.user_id == current_user.id,
                SyncPushIdempotency.device_id == device_id,
                SyncPushIdempotency.idempotency_key == idempotency_key,
            )
        )
        if row is not None and row.response_json:
            return BatchTxDeleteResponse.model_validate(row.response_json)
        # 兜底:落到通用 meta 的字段子集(理论上不该走到这里)
        return BatchTxDeleteResponse(
            ledger_id=ledger.external_id,
            base_change_id=req.base_change_id,
            new_change_id=replay.new_change_id,
            server_timestamp=replay.server_timestamp,
            deleted_tx_ids=[],
            failed=[],
        )

    # 去重 —— 同一 sync_id 只删一次
    unique_ids: list[str] = []
    seen: set[str] = set()
    for tx_id in req.tx_ids:
        if tx_id and tx_id not in seen:
            unique_ids.append(tx_id)
            seen.add(tx_id)

    lock_ledger_for_materialize(db, ledger.id)
    snapshot = snapshot_builder.build(db, ledger)
    # 深拷贝快照用于 diff(跟 batch_create 同模式)
    prev_snapshot = {**snapshot}
    for _k in ("items", "accounts", "categories", "tags", "budgets"):
        arr = snapshot.get(_k)
        if isinstance(arr, list):
            prev_snapshot[_k] = [dict(e) if isinstance(e, dict) else e for e in arr]

    # 循环 mutate;单个 tx 报错 → 记 failed 继续。事务级错误(KeyboardInterrupt 等)
    # 不在这里捕获,按惯例往上抛由 FastAPI 处理。
    deleted_ids: list[str] = []
    failed: list[BatchTxFailure] = []
    delete_payload = _payload_with_actor({}, current_user)

    for tx_id in unique_ids:
        try:
            snapshot = delete_transaction(snapshot, tx_id, delete_payload)
            deleted_ids.append(tx_id)
        except KeyError:
            # _find_by_sync_id 抛 KeyError → tx 不在 snapshot(已删 / ID 错 / 跨 ledger)
            failed.append(
                BatchTxFailure(tx_id=tx_id, reason="not_found", message="transaction not in ledger")
            )
        except PermissionError as exc:
            failed.append(
                BatchTxFailure(tx_id=tx_id, reason="permission_denied", message=str(exc))
            )
        except ValueError as exc:
            # snapshot_mutator 在 sync_id prefix 不对 / 其它校验失败时抛
            failed.append(BatchTxFailure(tx_id=tx_id, reason="conflict", message=str(exc)))

    # diff + emit changes(只针对实际变更的 items)
    now = datetime.now(timezone.utc)
    if deleted_ids:
        emitted_change_ids = _emit_entity_diffs(
            db,
            ledger=ledger,
            current_user=current_user,
            device_id=device_id,
            prev=prev_snapshot,
            next_snapshot=snapshot,
            now=now,
        )
        new_change_id = max(emitted_change_ids) if emitted_change_ids else (
            snapshot_builder.latest_change_id(db, ledger.id)
        )
    else:
        new_change_id = snapshot_builder.latest_change_id(db, ledger.id)

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_tx_batch_delete",
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": req.base_change_id,
                "newChangeId": new_change_id,
                "deletedCount": len(deleted_ids),
                "deletedIds": deleted_ids,
                "failedCount": len(failed),
                "failedIds": [f.tx_id for f in failed],
            },
        )
    )

    response = BatchTxDeleteResponse(
        ledger_id=ledger.external_id,
        base_change_id=req.base_change_id,
        new_change_id=new_change_id,
        server_timestamp=now,
        deleted_tx_ids=deleted_ids,
        failed=failed,
    )

    request_hash = _hash_request(request.method, request.url.path, payload_for_ide)
    if idempotency_key:
        db.add(
            SyncPushIdempotency(
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_json=response.model_dump(mode="json"),
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            replay = _load_idempotent_response(
                db,
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return BatchTxDeleteResponse(**replay.model_dump()) if hasattr(replay, "model_dump") else replay  # type: ignore[return-value]
        raise

    logger.info(
        "tx.batch_delete ledger=%s deleted=%d failed=%d change_id=%d device=%s user=%s",
        ledger.external_id, len(deleted_ids), len(failed), new_change_id, device_id, current_user.id,
    )

    if deleted_ids:
        await request.app.state.ws_manager.broadcast_to_user(
            ledger.user_id,
            {
                "type": "sync_change",
                "ledgerId": ledger.external_id,
                "serverCursor": new_change_id,
                "serverTimestamp": now.isoformat(),
            },
        )
    return response
