from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..concurrency import lock_ledger_for_materialize
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes, require_scopes
from ..ledger_access import (
    ROLE_EDITOR,
    ROLE_OWNER,
    get_accessible_ledger_by_external_id,
)
from ..models import (
    AuditLog,
    Ledger,
    SyncChange,
    SyncPushIdempotency,
    User,
)
from ..schemas import (
    WriteAccountCreateRequest,
    WriteAccountUpdateRequest,
    WriteCategoryCreateRequest,
    WriteCategoryUpdateRequest,
    WriteCommitMeta,
    WriteEntityDeleteRequest,
    WriteLedgerCreateRequest,
    WriteLedgerMetaUpdateRequest,
    WriteTagCreateRequest,
    WriteTagUpdateRequest,
    WriteTransactionCreateRequest,
    WriteTransactionUpdateRequest,
)
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE
from .. import snapshot_cache
from ..snapshot_mutator import (
    create_account,
    create_category,
    create_tag,
    create_transaction,
    delete_account,
    delete_category,
    delete_tag,
    delete_transaction,
    ensure_snapshot_v2,
    update_account,
    update_category,
    update_tag,
    update_transaction,
)

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
_WRITE_SCOPE_DEP = (
    require_any_scopes(SCOPE_WEB_WRITE, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_WEB_WRITE)
)

_TRANSACTION_WRITE_ROLES = {ROLE_OWNER, ROLE_EDITOR}
_OWNER_ONLY_ROLES = {ROLE_OWNER}
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "description": "Write role forbidden",
    },
    status.HTTP_404_NOT_FOUND: {
        "description": "Ledger or entity not found",
    },
    status.HTTP_409_CONFLICT: {
        "description": "Write conflict",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "WRITE_CONFLICT",
                        "message": "Write conflict",
                        "request_id": "req_xxx",
                    },
                    "detail": "Write conflict",
                    "latest_change_id": 12,
                    "latest_server_timestamp": "2026-02-24T12:00:00+00:00",
                }
            }
        },
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _diff_entity_list(
    db: Session,
    ledger: Ledger,
    current_user: User,
    device_id: str,
    now: datetime,
    prev_list: list[dict[str, Any]],
    next_list: list[dict[str, Any]],
    entity_type: str,
) -> int:
    """Create individual SyncChange rows for entities that changed between two snapshots."""
    prev_map = {e["syncId"]: e for e in prev_list if "syncId" in e}
    next_map = {e["syncId"]: e for e in next_list if "syncId" in e}
    count = 0

    # Upserted entities (new or changed)
    for sync_id, entity in next_map.items():
        if sync_id not in prev_map or entity != prev_map[sync_id]:
            db.add(SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                entity_type=entity_type,
                entity_sync_id=sync_id,
                action="upsert",
                payload_json=entity,
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            ))
            count += 1

    # Deleted entities
    for sync_id in prev_map:
        if sync_id not in next_map:
            db.add(SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                entity_type=entity_type,
                entity_sync_id=sync_id,
                action="delete",
                payload_json={},
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            ))
            count += 1

    return count


def _emit_entity_diffs(
    db: Session,
    *,
    ledger: Ledger,
    current_user: User,
    device_id: str,
    prev: dict[str, Any] | None,
    next_snapshot: dict[str, Any],
    now: datetime,
) -> None:
    """Diff prev/next snapshots and emit individual SyncChange rows for each changed entity.

    顺序很重要：先 account / category / tag（被引用方），最后 transaction（引用
    方）。mobile 在 _pull 里按 change_id ASC 逐条 apply，若 tx change 的 change_id
    比它引用的 category change 还小，`_resolveCategoryId` 在 category 更新前就
    查老名字 → 查不到 → tx.categoryId = null。典型表现：web 改分类名后 mobile
    上的相关交易分类变空。
    """
    prev = prev or {}
    count = 0
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("accounts") or [], next_snapshot.get("accounts") or [], "account")
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("categories") or [], next_snapshot.get("categories") or [], "category")
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("tags") or [], next_snapshot.get("tags") or [], "tag")
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("items") or [], next_snapshot.get("items") or [], "transaction")
    logger.info("_emit_entity_diffs: emitted %d entity changes for ledger %s", count, ledger.external_id)


def _load_ledger_for_write(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str],  # noqa: ARG001 — back-compat, ignored under single-user-per-ledger
) -> tuple[Ledger, None]:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
    )
    if row is not None:
        return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")


def _latest_snapshot_change(db: Session, ledger_id: str) -> SyncChange | None:
    return db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.action == "upsert",
        )
        .order_by(SyncChange.change_id.desc())
    )


def _parse_snapshot(change: SyncChange | None) -> dict:
    if change is None:
        return ensure_snapshot_v2({})
    payload = change.payload_json
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str) or not content.strip():
        return ensure_snapshot_v2({})
    try:
        snapshot = json.loads(content)
    except json.JSONDecodeError:
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return ensure_snapshot_v2(snapshot)


def _hash_request(method: str, path: str, payload: dict) -> str:
    raw = json.dumps(
        {"method": method, "path": path, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _purge_expired_idempotency(db: Session) -> None:
    now = _utcnow()
    expired = db.scalars(
        select(SyncPushIdempotency).where(SyncPushIdempotency.expires_at < now).limit(200)
    ).all()
    for row in expired:
        db.delete(row)
    if expired:
        db.flush()


def _load_idempotent_response(
    db: Session,
    *,
    user_id: str,
    device_id: str,
    idempotency_key: str,
    request_hash: str,
) -> WriteCommitMeta | None:
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
    payload = dict(row.response_json)
    payload["idempotency_replayed"] = True
    return WriteCommitMeta.model_validate(payload)


async def _commit_write(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    request_payload: dict,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    mutate: Callable[[dict], tuple[dict, str | None]],
) -> WriteCommitMeta:
    # Serialize concurrent writers on the same ledger so the subsequent
    # snapshot write doesn't race with /sync/push's materializer.
    lock_ledger_for_materialize(db, ledger.id)
    latest = _latest_snapshot_change(db, ledger.id)
    latest_change_id = latest.change_id if latest is not None else 0

    # Legacy strict base_change_id comparison — guarded by a feature flag.
    # Default OFF: during a mobile fullPush the server-side materializer bumps
    # latest_change_id faster than any web retry can catch up, producing
    # endless 409s. Instead we always mutate against the LATEST snapshot and
    # let per-entity LWW (`_emit_entity_diffs` → incoming vs existing
    # updated_at tuples) resolve the rare real conflict.
    if settings.strict_base_change_id and base_change_id != latest_change_id:
        latest_ts = latest.updated_at.isoformat() if latest is not None else None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Write conflict",
                "latest_change_id": latest_change_id,
                "latest_server_timestamp": latest_ts,
            },
        )

    snapshot = _parse_snapshot(latest)
    # Keep an independent copy of prev snapshot for diffing —
    # mutate() may modify snapshot in-place via ensure_snapshot_v2 internals.
    prev_snapshot = json.loads(json.dumps(snapshot))
    try:
        next_snapshot, entity_id = mutate(snapshot)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now = _utcnow()
    row_change = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        entity_type="ledger_snapshot",
        entity_sync_id=ledger.external_id,
        action="upsert",
        payload_json={
            "content": json.dumps(next_snapshot, ensure_ascii=False),
            "metadata": {"source": "web_write"},
        },
        updated_at=now,
        updated_by_device_id=device_id,
        updated_by_user_id=current_user.id,
    )
    db.add(row_change)
    db.flush()
    snapshot_cache.invalidate(ledger.id)

    # Emit individual entity SyncChanges so Mobile can see Web changes
    _emit_entity_diffs(
        db,
        ledger=ledger,
        current_user=current_user,
        device_id=device_id,
        prev=prev_snapshot,
        next_snapshot=next_snapshot,
        now=now,
    )

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action=audit_action,
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": base_change_id,
                "newChangeId": row_change.change_id,
                "entityId": entity_id,
            },
        )
    )

    response = WriteCommitMeta(
        ledger_id=ledger.external_id,
        base_change_id=base_change_id,
        new_change_id=row_change.change_id,
        server_timestamp=row_change.updated_at,
        idempotency_replayed=False,
        entity_id=entity_id,
    )

    request_hash = _hash_request(request.method, request.url.path, request_payload)
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
    except IntegrityError as exc:
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
                return replay
        raise exc

    logger.info(
        "write.commit action=%s ledger=%s entity=%s change_id=%d device=%s user=%s",
        audit_action,
        ledger.external_id,
        entity_id,
        response.new_change_id,
        device_id,
        current_user.id,
    )
    # Single-user-per-ledger: notify only the owner.
    await request.app.state.ws_manager.broadcast_to_user(
        ledger.user_id,
        {
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": response.new_change_id,
            "serverTimestamp": response.server_timestamp.isoformat(),
        },
    )
    return response


def _prepare_write(
    *,
    db: Session,
    current_user: User,
    ledger_external_id: str,
    required_roles: set[str],
    idempotency_key: str | None,
    device_id: str,
    method: str,
    path: str,
    payload: dict,
) -> tuple[Ledger, WriteCommitMeta | None]:
    ledger, _ = _load_ledger_for_write(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles=required_roles,
    )
    if not idempotency_key:
        return ledger, None
    _purge_expired_idempotency(db)
    replay = _load_idempotent_response(
        db,
        user_id=current_user.id,
        device_id=device_id,
        idempotency_key=idempotency_key,
        request_hash=_hash_request(method, path, payload),
    )
    return ledger, replay


def _normalize_currency(raw: str | None) -> str:
    value = (raw or "CNY").strip().upper()
    if not value:
        return "CNY"
    return value[:16]


def _normalize_ledger_name(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ledger name is required")
    return value[:255]


def _payload_with_actor(payload: dict, current_user: User) -> dict:
    merged = dict(payload)
    merged["__actor_user_id"] = current_user.id
    merged["__actor_is_admin"] = bool(current_user.is_admin)
    return merged


def _assert_can_modify_entity(
    *,
    db: Session,  # noqa: ARG001 — retained for signature compat
    ledger: Ledger,
    current_user: User,
    entity_sync_id: str,  # noqa: ARG001 — retained for signature compat
) -> None:
    """Ownership check: ledger.user_id must match current user."""
    if ledger.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("/ledgers", response_model=WriteCommitMeta, responses=_WRITE_RESPONSES)
async def create_ledger(
    req: WriteLedgerCreateRequest,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    external_id = (req.ledger_id or f"ledger_{uuid4().hex[:12]}").strip()
    if not external_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ledger id is required")
    # Scope uniqueness to current user — different users can use the same
    # external_id (enforced by the (user_id, external_id) unique constraint).
    exists = db.scalar(
        select(Ledger).where(
            Ledger.external_id == external_id,
            Ledger.user_id == current_user.id,
        )
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ledger already exists")

    name = _normalize_ledger_name(req.ledger_name)
    currency = _normalize_currency(req.currency)
    now = _utcnow()

    ledger = Ledger(
        user_id=current_user.id,
        external_id=external_id,
        name=name,
    )
    db.add(ledger)
    db.flush()

    snapshot = ensure_snapshot_v2(
        {
            "ledgerName": name,
            "currency": currency,
            "items": [],
            "accounts": [],
            "categories": [],
            "tags": [],
            "count": 0,
        }
    )
    row_change = SyncChange(
        user_id=current_user.id,
        ledger_id=ledger.id,
        entity_type="ledger_snapshot",
        entity_sync_id=external_id,
        action="upsert",
        payload_json={
            "content": json.dumps(snapshot, ensure_ascii=False),
            "metadata": {"source": "web_write"},
        },
        updated_at=now,
        updated_by_device_id="web-console",
        updated_by_user_id=current_user.id,
    )
    db.add(row_change)
    db.flush()
    snapshot_cache.invalidate(ledger.id)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_ledger_create",
            metadata_json={
                "ledgerId": external_id,
                "newChangeId": row_change.change_id,
            },
        )
    )
    db.commit()

    await request.app.state.ws_manager.broadcast_to_user(
        current_user.id,
        {
            "type": "sync_change",
            "ledgerId": external_id,
            "serverCursor": row_change.change_id,
            "serverTimestamp": row_change.updated_at.isoformat(),
        },
    )

    logger.info(
        "write.ledger.create ledger=%s name=%s currency=%s user=%s",
        external_id,
        name,
        currency,
        current_user.id,
    )
    return WriteCommitMeta(
        ledger_id=external_id,
        base_change_id=0,
        new_change_id=row_change.change_id,
        server_timestamp=row_change.updated_at,
        idempotency_replayed=False,
        entity_id=external_id,
    )


@router.patch(
    "/ledgers/{ledger_id}/meta",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_ledger_meta(
    ledger_id: str,
    req: WriteLedgerMetaUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    if "ledger_name" in payload:
        payload["ledger_name"] = _normalize_ledger_name(payload.get("ledger_name"))
    if "currency" in payload:
        payload["currency"] = _normalize_currency(payload.get("currency"))
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay

    if "ledger_name" in payload:
        ledger.name = payload["ledger_name"]

    def mutate(snapshot: dict) -> tuple[dict, str]:
        next_snapshot = ensure_snapshot_v2(snapshot)
        if "ledger_name" in payload:
            next_snapshot["ledgerName"] = payload["ledger_name"]
        if "currency" in payload:
            next_snapshot["currency"] = payload["currency"]
        return next_snapshot, ledger.external_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_ledger_meta_update",
        mutate=mutate,
    )


@router.delete(
    "/ledgers/{ledger_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_ledger(
    ledger_id: str,
    request: Request,
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    """Soft-delete a ledger: append a ``ledger_snapshot action=delete`` tombstone
    SyncChange. Reads filter it out; historical rows are retained for audit."""
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")
    ledger, _ = row

    lock_ledger_for_materialize(db, ledger.id)
    now = _utcnow()
    tombstone = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        entity_type="ledger_snapshot",
        entity_sync_id=ledger.external_id,
        action="delete",
        payload_json={},
        updated_at=now,
        updated_by_device_id=device_id,
        updated_by_user_id=current_user.id,
    )
    db.add(tombstone)
    db.flush()
    snapshot_cache.invalidate(ledger.id)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_ledger_delete",
            metadata_json={
                "ledgerId": ledger.external_id,
                "newChangeId": tombstone.change_id,
            },
        )
    )
    db.commit()

    await request.app.state.ws_manager.broadcast_to_user(
        ledger.user_id,
        {
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": tombstone.change_id,
            "serverTimestamp": tombstone.updated_at.isoformat(),
        },
    )
    return WriteCommitMeta(
        ledger_id=ledger.external_id,
        base_change_id=0,
        new_change_id=tombstone.change_id,
        server_timestamp=tombstone.updated_at,
        idempotency_replayed=False,
        entity_id=ledger.external_id,
    )


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
    resolved_payload = payload
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=resolved_payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_update",
        mutate=lambda snapshot: (update_transaction(snapshot, tx_id, mutate_payload), tx_id),
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
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_delete",
        mutate=lambda snapshot: (delete_transaction(snapshot, tx_id, mutate_payload), tx_id),
    )


@router.post(
    "/ledgers/{ledger_id}/accounts",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_acc(
    ledger_id: str,
    req: WriteAccountCreateRequest,
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
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_account_create",
        mutate=lambda snapshot: create_account(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/accounts/{account_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_acc(
    ledger_id: str,
    account_id: str,
    req: WriteAccountUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    mutate_payload = _payload_with_actor(payload, current_user)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_account_update",
        mutate=lambda snapshot: (update_account(snapshot, account_id, mutate_payload), account_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/accounts/{account_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_acc(
    ledger_id: str,
    account_id: str,
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
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_account_delete",
        mutate=lambda snapshot: (delete_account(snapshot, account_id, mutate_payload), account_id),
    )


@router.post(
    "/ledgers/{ledger_id}/categories",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_cat(
    ledger_id: str,
    req: WriteCategoryCreateRequest,
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
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_category_create",
        mutate=lambda snapshot: create_category(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/categories/{category_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_cat(
    ledger_id: str,
    category_id: str,
    req: WriteCategoryUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    mutate_payload = _payload_with_actor(payload, current_user)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_category_update",
        mutate=lambda snapshot: (update_category(snapshot, category_id, mutate_payload), category_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/categories/{category_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_cat(
    ledger_id: str,
    category_id: str,
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
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_category_delete",
        mutate=lambda snapshot: (delete_category(snapshot, category_id, mutate_payload), category_id),
    )


@router.post(
    "/ledgers/{ledger_id}/tags",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_tag_api(
    ledger_id: str,
    req: WriteTagCreateRequest,
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
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tag_create",
        mutate=lambda snapshot: create_tag(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/tags/{tag_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_tag_api(
    ledger_id: str,
    tag_id: str,
    req: WriteTagUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    mutate_payload = _payload_with_actor(payload, current_user)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tag_update",
        mutate=lambda snapshot: (update_tag(snapshot, tag_id, mutate_payload), tag_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/tags/{tag_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_tag_api(
    ledger_id: str,
    tag_id: str,
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
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tag_delete",
        mutate=lambda snapshot: (delete_tag(snapshot, tag_id, mutate_payload), tag_id),
    )

