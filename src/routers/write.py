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

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes, require_scopes
from ..ledger_access import (
    ACTIVE_MEMBER_STATUS,
    ROLE_EDITOR,
    ROLE_OWNER,
    get_accessible_ledger_by_external_id,
)
from ..models import (
    AuditLog,
    Ledger,
    LedgerMember,
    SyncChange,
    SyncPushIdempotency,
    User,
    UserAccount,
    UserCategory,
    UserTag,
)
from ..schemas import (
    ReadAccountOut,
    ReadCategoryOut,
    ReadTagOut,
    WorkspaceAccountCreateRequest,
    WorkspaceAccountUpdateRequest,
    WorkspaceCategoryCreateRequest,
    WorkspaceCategoryUpdateRequest,
    WorkspaceTagCreateRequest,
    WorkspaceTagUpdateRequest,
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
from ..user_dictionary_service import deduplicate_user_dictionaries

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
    """Diff prev/next snapshots and emit individual SyncChange rows for each changed entity."""
    prev = prev or {}
    count = 0
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("items") or [], next_snapshot.get("items") or [], "transaction")
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("accounts") or [], next_snapshot.get("accounts") or [], "account")
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("categories") or [], next_snapshot.get("categories") or [], "category")
    count += _diff_entity_list(db, ledger, current_user, device_id, now,
                               prev.get("tags") or [], next_snapshot.get("tags") or [], "tag")
    logger.info("_emit_entity_diffs: emitted %d entity changes for ledger %s", count, ledger.external_id)


def _load_ledger_for_write(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str],
) -> tuple[Ledger, LedgerMember]:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=roles,
    )
    if row is not None:
        return row

    ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
    if ledger is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write role forbidden")


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
    latest = _latest_snapshot_change(db, ledger.id)
    latest_change_id = latest.change_id if latest is not None else 0
    if base_change_id != latest_change_id:
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

    member_user_ids = db.scalars(
        select(LedgerMember.user_id).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
    ).all()
    for member_user_id in set(member_user_ids):
        await request.app.state.ws_manager.broadcast_to_user(
            member_user_id,
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
    db: Session,
    ledger: Ledger,
    current_user: User,
    entity_sync_id: str,
) -> None:
    """Assert the current user owns the ledger and thus can modify any entity in it."""
    member = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == current_user.id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write role forbidden")


def _normalize_dict_name(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _split_tag_names(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _ensure_target_user_writable(*, current_user: User, target_user_id: str) -> None:
    if current_user.is_admin:
        return
    if current_user.id != target_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write role forbidden")


def _dedupe_workspace_user_dict(db: Session, *, target_user_id: str) -> None:
    if deduplicate_user_dictionaries(db, user_id=target_user_id):
        db.flush()


def _get_or_create_user_account(
    db: Session,
    *,
    target_user_id: str,
    name: str,
) -> UserAccount:
    row = db.scalar(
        select(UserAccount).where(
            UserAccount.user_id == target_user_id,
            func.lower(UserAccount.name) == name.lower(),
            UserAccount.deleted_at.is_(None),
        )
    )
    if row is not None:
        return row
    row = UserAccount(user_id=target_user_id, name=name)
    db.add(row)
    db.flush()
    return row


def _get_or_create_user_category(
    db: Session,
    *,
    target_user_id: str,
    kind: str,
    name: str,
) -> UserCategory:
    row = db.scalar(
        select(UserCategory).where(
            UserCategory.user_id == target_user_id,
            UserCategory.kind == kind,
            func.lower(UserCategory.name) == name.lower(),
            UserCategory.deleted_at.is_(None),
        )
    )
    if row is not None:
        return row
    row = UserCategory(user_id=target_user_id, kind=kind, name=name)
    db.add(row)
    db.flush()
    return row


def _get_or_create_user_tag(db: Session, *, target_user_id: str, name: str) -> UserTag:
    row = db.scalar(
        select(UserTag).where(
            UserTag.user_id == target_user_id,
            func.lower(UserTag.name) == name.lower(),
            UserTag.deleted_at.is_(None),
        )
    )
    if row is not None:
        return row
    row = UserTag(user_id=target_user_id, name=name)
    db.add(row)
    db.flush()
    return row


def _resolve_tx_dictionary_payload(
    db: Session,
    *,
    current_user: User,
    payload: dict,
    target_user_id: str,
) -> dict:
    _ensure_target_user_writable(current_user=current_user, target_user_id=target_user_id)
    resolved = dict(payload)

    def resolve_account(*, id_key: str, name_key: str) -> None:
        raw_id = resolved.get(id_key)
        raw_name = _normalize_dict_name(resolved.get(name_key))
        row: UserAccount | None = None
        if isinstance(raw_id, str) and raw_id.strip():
            row = db.scalar(
                select(UserAccount).where(
                    UserAccount.id == raw_id.strip(),
                    UserAccount.deleted_at.is_(None),
                )
            )
            if row is not None:
                _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
                if row.user_id != target_user_id:
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write role forbidden")
            elif raw_name:
                # ID not found in UserAccount table (e.g. mobile-synced snapshot ID),
                # fall back to name-based resolution
                row = _get_or_create_user_account(db, target_user_id=target_user_id, name=raw_name)
            else:
                # ID provided but not found, and no name fallback — keep the raw ID
                # so it passes through to the snapshot as-is
                return
        elif raw_name:
            row = _get_or_create_user_account(db, target_user_id=target_user_id, name=raw_name)
        if row is None:
            resolved[id_key] = None
            if name_key in resolved:
                resolved[name_key] = None
            return
        resolved[id_key] = row.id
        resolved[name_key] = row.name

    resolve_account(id_key="account_id", name_key="account_name")
    resolve_account(id_key="from_account_id", name_key="from_account_name")
    resolve_account(id_key="to_account_id", name_key="to_account_name")

    raw_category_id = resolved.get("category_id")
    raw_category_name = _normalize_dict_name(resolved.get("category_name"))
    raw_category_kind = _normalize_dict_name(resolved.get("category_kind"))
    category_row: UserCategory | None = None
    if isinstance(raw_category_id, str) and raw_category_id.strip():
        category_row = db.scalar(
            select(UserCategory).where(
                UserCategory.id == raw_category_id.strip(),
                UserCategory.deleted_at.is_(None),
            )
        )
        if category_row is not None:
            _ensure_target_user_writable(current_user=current_user, target_user_id=category_row.user_id)
            if category_row.user_id != target_user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write role forbidden")
        elif raw_category_name and raw_category_kind:
            # ID not found in UserCategory table (e.g. mobile-synced snapshot ID),
            # fall back to name-based resolution
            category_row = _get_or_create_user_category(
                db,
                target_user_id=target_user_id,
                kind=raw_category_kind,
                name=raw_category_name,
            )
    elif raw_category_name and raw_category_kind:
        category_row = _get_or_create_user_category(
            db,
            target_user_id=target_user_id,
            kind=raw_category_kind,
            name=raw_category_name,
        )
    if category_row is not None:
        resolved["category_id"] = category_row.id
        resolved["category_name"] = category_row.name
        resolved["category_kind"] = category_row.kind
    else:
        resolved["category_id"] = None

    tag_rows: list[UserTag] = []
    raw_tag_ids = resolved.get("tag_ids")
    if isinstance(raw_tag_ids, list) and raw_tag_ids:
        for raw_tag_id in raw_tag_ids:
            if not isinstance(raw_tag_id, str) or not raw_tag_id.strip():
                continue
            row = db.scalar(
                select(UserTag).where(
                    UserTag.id == raw_tag_id.strip(),
                    UserTag.deleted_at.is_(None),
                )
            )
            if row is None:
                # Tag ID not found (e.g. mobile-synced snapshot ID) — skip it
                continue
            _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
            if row.user_id != target_user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write role forbidden")
            tag_rows.append(row)
    else:
        for name in _split_tag_names(resolved.get("tags")):
            tag_rows.append(_get_or_create_user_tag(db, target_user_id=target_user_id, name=name))

    deduped_tag_ids: list[str] = []
    deduped_tag_names: list[str] = []
    for row in tag_rows:
        if row.id in deduped_tag_ids:
            continue
        deduped_tag_ids.append(row.id)
        deduped_tag_names.append(row.name)
    resolved["tag_ids"] = deduped_tag_ids or None
    resolved["tags"] = deduped_tag_names or None
    return resolved


def _workspace_target_user_id(*, current_user: User, user_id: str | None) -> str:
    if current_user.is_admin and isinstance(user_id, str) and user_id.strip():
        return user_id.strip()
    return current_user.id


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
    exists = db.scalar(select(Ledger).where(Ledger.external_id == external_id))
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

    db.add(
        LedgerMember(
            ledger_id=ledger.id,
            user_id=current_user.id,
            role=ROLE_OWNER,
            status=ACTIVE_MEMBER_STATUS,
            joined_at=now,
        )
    )

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
    resolved_payload = _resolve_tx_dictionary_payload(
        db,
        current_user=current_user,
        payload=payload,
        target_user_id=current_user.id,
    )
    mutate_payload = _payload_with_actor(resolved_payload, current_user)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=resolved_payload,
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
    target_user_id = current_user.id
    resolved_payload = _resolve_tx_dictionary_payload(
        db,
        current_user=current_user,
        payload=payload,
        target_user_id=target_user_id,
    )
    mutate_payload = _payload_with_actor(resolved_payload, current_user)
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


@router.post("/workspace/accounts", response_model=ReadAccountOut)
def create_workspace_account(
    req: WorkspaceAccountCreateRequest,
    user_id: str | None = None,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadAccountOut:
    target_user_id = _workspace_target_user_id(current_user=current_user, user_id=user_id)
    _ensure_target_user_writable(current_user=current_user, target_user_id=target_user_id)
    _dedupe_workspace_user_dict(db, target_user_id=target_user_id)
    normalized_name = _normalize_dict_name(req.name)
    exists = db.scalar(
        select(UserAccount).where(
            UserAccount.user_id == target_user_id,
            func.lower(UserAccount.name) == normalized_name.lower(),
            UserAccount.deleted_at.is_(None),
        )
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicated account name")
    row = UserAccount(
        user_id=target_user_id,
        name=normalized_name,
        account_type=req.account_type,
        currency=req.currency,
        initial_balance=req.initial_balance,
    )
    db.add(row)
    db.flush()
    email = db.scalar(select(User.email).where(User.id == target_user_id))
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_account_create",
            metadata_json={"accountId": row.id, "targetUserId": target_user_id},
        )
    )
    db.commit()
    return ReadAccountOut(
        id=row.id,
        name=row.name,
        account_type=row.account_type,
        currency=row.currency,
        initial_balance=row.initial_balance,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=target_user_id,
        created_by_email=email,
    )


@router.patch("/workspace/accounts/{account_id}", response_model=ReadAccountOut)
def update_workspace_account(
    account_id: str,
    req: WorkspaceAccountUpdateRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadAccountOut:
    row = db.scalar(
        select(UserAccount).where(
            UserAccount.id == account_id,
            UserAccount.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
    _dedupe_workspace_user_dict(db, target_user_id=row.user_id)
    payload = req.model_dump(exclude_unset=True)
    if "name" in payload:
        normalized = _normalize_dict_name(payload.get("name"))
        exists = db.scalar(
            select(UserAccount).where(
                UserAccount.id != row.id,
                UserAccount.user_id == row.user_id,
                func.lower(UserAccount.name) == normalized.lower(),
                UserAccount.deleted_at.is_(None),
            )
        )
        if exists is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicated account name")
        row.name = normalized
    if "account_type" in payload:
        row.account_type = payload.get("account_type")
    if "currency" in payload:
        row.currency = payload.get("currency")
    if "initial_balance" in payload:
        row.initial_balance = payload.get("initial_balance")
    row.updated_at = _utcnow()
    email = db.scalar(select(User.email).where(User.id == row.user_id))
    db.add(row)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_account_update",
            metadata_json={"accountId": row.id, "targetUserId": row.user_id},
        )
    )
    db.commit()
    return ReadAccountOut(
        id=row.id,
        name=row.name,
        account_type=row.account_type,
        currency=row.currency,
        initial_balance=row.initial_balance,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.delete("/workspace/accounts/{account_id}", response_model=ReadAccountOut)
def delete_workspace_account(
    account_id: str,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadAccountOut:
    row = db.scalar(
        select(UserAccount).where(
            UserAccount.id == account_id,
            UserAccount.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
    row.deleted_at = _utcnow()
    row.updated_at = _utcnow()
    email = db.scalar(select(User.email).where(User.id == row.user_id))
    db.add(row)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_account_delete",
            metadata_json={"accountId": row.id, "targetUserId": row.user_id},
        )
    )
    db.commit()
    return ReadAccountOut(
        id=row.id,
        name=row.name,
        account_type=row.account_type,
        currency=row.currency,
        initial_balance=row.initial_balance,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.post("/workspace/categories", response_model=ReadCategoryOut)
def create_workspace_category(
    req: WorkspaceCategoryCreateRequest,
    user_id: str | None = None,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCategoryOut:
    target_user_id = _workspace_target_user_id(current_user=current_user, user_id=user_id)
    _ensure_target_user_writable(current_user=current_user, target_user_id=target_user_id)
    _dedupe_workspace_user_dict(db, target_user_id=target_user_id)
    normalized_name = _normalize_dict_name(req.name)
    exists = db.scalar(
        select(UserCategory).where(
            UserCategory.user_id == target_user_id,
            UserCategory.kind == req.kind,
            func.lower(UserCategory.name) == normalized_name.lower(),
            UserCategory.deleted_at.is_(None),
        )
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicated category")
    row = UserCategory(
        user_id=target_user_id,
        name=normalized_name,
        kind=req.kind,
        level=req.level,
        sort_order=req.sort_order,
        icon=req.icon,
        icon_type=req.icon_type,
        custom_icon_path=req.custom_icon_path,
        icon_cloud_file_id=req.icon_cloud_file_id,
        icon_cloud_sha256=req.icon_cloud_sha256,
    )
    db.add(row)
    db.flush()
    email = db.scalar(select(User.email).where(User.id == target_user_id))
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_category_create",
            metadata_json={"categoryId": row.id, "targetUserId": target_user_id},
        )
    )
    db.commit()
    return ReadCategoryOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        level=row.level,
        sort_order=row.sort_order,
        icon=row.icon,
        icon_type=row.icon_type,
        custom_icon_path=row.custom_icon_path,
        icon_cloud_file_id=row.icon_cloud_file_id,
        icon_cloud_sha256=row.icon_cloud_sha256,
        parent_name=None,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.patch("/workspace/categories/{category_id}", response_model=ReadCategoryOut)
def update_workspace_category(
    category_id: str,
    req: WorkspaceCategoryUpdateRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCategoryOut:
    row = db.scalar(
        select(UserCategory).where(
            UserCategory.id == category_id,
            UserCategory.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
    _dedupe_workspace_user_dict(db, target_user_id=row.user_id)
    payload = req.model_dump(exclude_unset=True)
    next_name = row.name
    next_kind = row.kind
    if "name" in payload:
        next_name = _normalize_dict_name(payload.get("name"))
    if "kind" in payload and payload.get("kind"):
        next_kind = str(payload.get("kind"))
    exists = db.scalar(
        select(UserCategory).where(
            UserCategory.id != row.id,
            UserCategory.user_id == row.user_id,
            UserCategory.kind == next_kind,
            func.lower(UserCategory.name) == next_name.lower(),
            UserCategory.deleted_at.is_(None),
        )
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicated category")
    row.name = next_name
    row.kind = next_kind
    if "level" in payload:
        row.level = payload.get("level")
    if "sort_order" in payload:
        row.sort_order = payload.get("sort_order")
    if "icon" in payload:
        row.icon = payload.get("icon")
    if "icon_type" in payload:
        row.icon_type = payload.get("icon_type")
    if "custom_icon_path" in payload:
        row.custom_icon_path = payload.get("custom_icon_path")
    if "icon_cloud_file_id" in payload:
        row.icon_cloud_file_id = payload.get("icon_cloud_file_id")
    if "icon_cloud_sha256" in payload:
        row.icon_cloud_sha256 = payload.get("icon_cloud_sha256")
    row.updated_at = _utcnow()
    email = db.scalar(select(User.email).where(User.id == row.user_id))
    db.add(row)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_category_update",
            metadata_json={"categoryId": row.id, "targetUserId": row.user_id},
        )
    )
    db.commit()
    return ReadCategoryOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        level=row.level,
        sort_order=row.sort_order,
        icon=row.icon,
        icon_type=row.icon_type,
        custom_icon_path=row.custom_icon_path,
        icon_cloud_file_id=row.icon_cloud_file_id,
        icon_cloud_sha256=row.icon_cloud_sha256,
        parent_name=None,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.delete("/workspace/categories/{category_id}", response_model=ReadCategoryOut)
def delete_workspace_category(
    category_id: str,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadCategoryOut:
    row = db.scalar(
        select(UserCategory).where(
            UserCategory.id == category_id,
            UserCategory.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
    row.deleted_at = _utcnow()
    row.updated_at = _utcnow()
    email = db.scalar(select(User.email).where(User.id == row.user_id))
    db.add(row)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_category_delete",
            metadata_json={"categoryId": row.id, "targetUserId": row.user_id},
        )
    )
    db.commit()
    return ReadCategoryOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        level=row.level,
        sort_order=row.sort_order,
        icon=row.icon,
        icon_type=row.icon_type,
        custom_icon_path=row.custom_icon_path,
        icon_cloud_file_id=row.icon_cloud_file_id,
        icon_cloud_sha256=row.icon_cloud_sha256,
        parent_name=None,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.post("/workspace/tags", response_model=ReadTagOut)
def create_workspace_tag(
    req: WorkspaceTagCreateRequest,
    user_id: str | None = None,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadTagOut:
    target_user_id = _workspace_target_user_id(current_user=current_user, user_id=user_id)
    _ensure_target_user_writable(current_user=current_user, target_user_id=target_user_id)
    _dedupe_workspace_user_dict(db, target_user_id=target_user_id)
    normalized_name = _normalize_dict_name(req.name)
    exists = db.scalar(
        select(UserTag).where(
            UserTag.user_id == target_user_id,
            func.lower(UserTag.name) == normalized_name.lower(),
            UserTag.deleted_at.is_(None),
        )
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicated tag")
    row = UserTag(user_id=target_user_id, name=normalized_name, color=req.color)
    db.add(row)
    db.flush()
    email = db.scalar(select(User.email).where(User.id == target_user_id))
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_tag_create",
            metadata_json={"tagId": row.id, "targetUserId": target_user_id},
        )
    )
    db.commit()
    return ReadTagOut(
        id=row.id,
        name=row.name,
        color=row.color,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.patch("/workspace/tags/{tag_id}", response_model=ReadTagOut)
def update_workspace_tag(
    tag_id: str,
    req: WorkspaceTagUpdateRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadTagOut:
    row = db.scalar(
        select(UserTag).where(
            UserTag.id == tag_id,
            UserTag.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
    _dedupe_workspace_user_dict(db, target_user_id=row.user_id)
    payload = req.model_dump(exclude_unset=True)
    if "name" in payload:
        normalized_name = _normalize_dict_name(payload.get("name"))
        exists = db.scalar(
            select(UserTag).where(
                UserTag.id != row.id,
                UserTag.user_id == row.user_id,
                func.lower(UserTag.name) == normalized_name.lower(),
                UserTag.deleted_at.is_(None),
            )
        )
        if exists is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicated tag")
        row.name = normalized_name
    if "color" in payload:
        row.color = payload.get("color")
    row.updated_at = _utcnow()
    email = db.scalar(select(User.email).where(User.id == row.user_id))
    db.add(row)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_tag_update",
            metadata_json={"tagId": row.id, "targetUserId": row.user_id},
        )
    )
    db.commit()
    return ReadTagOut(
        id=row.id,
        name=row.name,
        color=row.color,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )


@router.delete("/workspace/tags/{tag_id}", response_model=ReadTagOut)
def delete_workspace_tag(
    tag_id: str,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReadTagOut:
    row = db.scalar(
        select(UserTag).where(
            UserTag.id == tag_id,
            UserTag.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _ensure_target_user_writable(current_user=current_user, target_user_id=row.user_id)
    row.deleted_at = _utcnow()
    row.updated_at = _utcnow()
    email = db.scalar(select(User.email).where(User.id == row.user_id))
    db.add(row)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=None,
            action="web_workspace_tag_delete",
            metadata_json={"tagId": row.id, "targetUserId": row.user_id},
        )
    )
    db.commit()
    return ReadTagOut(
        id=row.id,
        name=row.name,
        color=row.color,
        last_change_id=0,
        ledger_id=None,
        ledger_name=None,
        created_by_user_id=row.user_id,
        created_by_email=email,
    )
