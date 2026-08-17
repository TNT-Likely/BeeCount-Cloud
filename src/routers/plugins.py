"""Generic plugin discovery and execution endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import snapshot_builder
from ..concurrency import lock_ledger_for_materialize
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..models import AuditLog, SyncPushIdempotency, User
from ..plugins import registry
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE
from ..snapshot_mutator import create_transaction
from .write._shared import (
    _TRANSACTION_WRITE_ROLES,
    _emit_entity_diffs,
    _hash_request,
    _payload_with_actor,
    _prepare_write,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_LIST_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE)
_RUN_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE)


class PluginRunRequest(BaseModel):
    ledger_id: str = Field(min_length=1, max_length=128)
    base_change_id: int = Field(default=0, ge=0)
    input: dict[str, Any] = Field(default_factory=dict)


class PluginRunResponse(BaseModel):
    plugin_id: str
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    created_sync_ids: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


@router.get("/plugins")
def list_plugins(
    _scopes: set[str] = Depends(_LIST_SCOPE_DEP),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return {"plugins": [plugin.manifest() for plugin in registry.list()]}


@router.post("/plugins/{plugin_id}/run", response_model=PluginRunResponse)
async def run_plugin(
    plugin_id: str,
    req: PluginRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_RUN_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PluginRunResponse:
    plugin = registry.get(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    payload_for_ide = req.model_dump(mode="json")
    request_hash = _hash_request(request.method, request.url.path, payload_for_ide)
    ledger, _ = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=req.ledger_id,
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=None,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload_for_ide,
    )
    if idempotency_key:
        replay = _load_plugin_idempotent_response(
            db,
            user_id=current_user.id,
            device_id=device_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay

    try:
        plugin_input = plugin.input_model.model_validate(req.input)
        result = plugin.run(plugin_input)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if not result.transactions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Plugin produced no transactions",
        )

    lock_ledger_for_materialize(db, ledger.id)
    if get_settings().strict_base_change_id:
        latest_any_change_id = snapshot_builder.latest_change_id(db, ledger.id)
        if req.base_change_id != latest_any_change_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Write conflict", "latest_change_id": latest_any_change_id},
            )

    snapshot = snapshot_builder.build(db, ledger)
    prev_snapshot = {**snapshot}
    for key in ("items", "accounts", "categories", "tags", "budgets"):
        arr = snapshot.get(key)
        if isinstance(arr, list):
            prev_snapshot[key] = [dict(e) if isinstance(e, dict) else e for e in arr]

    created_sync_ids: list[str] = []
    try:
        for tx_payload in result.transactions:
            snapshot, sync_id = create_transaction(
                snapshot,
                _payload_with_actor(tx_payload, current_user),
            )
            created_sync_ids.append(sync_id)
    except (KeyError, ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "PLUGIN_TX_INVALID", "message": str(exc)},
        ) from exc

    now = datetime.now(timezone.utc)
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
    response = PluginRunResponse(
        plugin_id=plugin.plugin_id,
        ledger_id=ledger.external_id,
        base_change_id=req.base_change_id,
        new_change_id=new_change_id,
        server_timestamp=now,
        created_sync_ids=created_sync_ids,
        summary=result.summary,
    )

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="plugin_run",
            metadata_json={
                "pluginId": plugin.plugin_id,
                "ledgerId": ledger.external_id,
                "baseChangeId": req.base_change_id,
                "newChangeId": new_change_id,
                "createdCount": len(created_sync_ids),
            },
        )
    )
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
            replay = _load_plugin_idempotent_response(
                db,
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
        raise

    logger.info(
        "plugin.run plugin=%s ledger=%s tx_count=%d change_id=%d device=%s user=%s",
        plugin.plugin_id,
        ledger.external_id,
        len(created_sync_ids),
        new_change_id,
        device_id,
        current_user.id,
    )
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


def _load_plugin_idempotent_response(
    db: Session,
    *,
    user_id: str,
    device_id: str,
    idempotency_key: str,
    request_hash: str,
) -> PluginRunResponse | None:
    row = db.scalar(
        select(SyncPushIdempotency).where(
            SyncPushIdempotency.user_id == user_id,
            SyncPushIdempotency.device_id == device_id,
            SyncPushIdempotency.idempotency_key == idempotency_key,
        )
    )
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key reused with different payload",
        )
    return PluginRunResponse.model_validate(row.response_json)
