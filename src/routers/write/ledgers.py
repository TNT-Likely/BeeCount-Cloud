"""Ledgers write endpoints.

POST / PATCH / DELETE for /ledgers/{ledger_id}/ledgers(ledgers 自身除外)。
依赖 `._shared` 里的 _commit_write / _prepare_write / normalize helper /
WRITE 响应表。Endpoint 自身只管参数校验 + mutate lambda 的构造。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


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
        currency=currency,
    )
    db.add(ledger)
    db.flush()

    # 方案 B:不写 ledger_snapshot 行。emit 一个 ledger entity SyncChange 个体事件,
    # mobile /sync/pull 能收到这个 ledger 被创建的事件。
    row_change = SyncChange(
        user_id=current_user.id,
        ledger_id=ledger.id,
        entity_type="ledger",
        entity_sync_id=external_id,
        action="upsert",
        payload_json={"ledgerName": name, "currency": currency},
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
    if "currency" in payload:
        ledger.currency = payload["currency"]

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
    # 软删除:Ledger 行不动(留着外键历史),但 projection 清零,让 /read/* 立刻看不到
    projection._truncate_ledger(db, ledger.id)
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


