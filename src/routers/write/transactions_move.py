"""Move existing transactions between two private ledgers."""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ... import projection, snapshot_cache
from ...concurrency import lock_ledger_for_materialize, lock_sqlite_writer
from ...database import get_db
from ...deps import get_current_user, require_scopes
from ...ledger_access import count_ledger_members
from ...models import (
    AttachmentFile,
    Device,
    Ledger,
    ReadTxProjection,
    SyncChange,
    TransactionMoveReceipt,
    TransactionMoveRetirement,
    User,
)
from ...security import SCOPE_APP_WRITE
from ...websocket_manager import broadcast_to_ledger
from ..attachments import _attachment_root
from ._shared import _hash_request, _load_ledger_for_write, _projection_row_to_tx_dict, _utcnow

router = APIRouter()
logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class MovePrepareRequest(BaseModel):
    target_ledger_id: str
    transaction_ids: list[str] = Field(min_length=1)


class MoveRevision(BaseModel):
    sync_id: str
    revision: int = Field(ge=0)


class MovePreparedItem(MoveRevision):
    transaction: dict[str, Any]


class MoveRequest(BaseModel):
    target_ledger_id: str
    items: list[MoveRevision] = Field(min_length=1)


class MovedItem(BaseModel):
    source_sync_id: str
    target_sync_id: str


class MoveResult(BaseModel):
    operation_id: UUID
    items: list[MovedItem]
    attachment_ids: dict[str, str]
    server_timestamp: datetime


def _chunks(items: list[_T]) -> Iterator[list[_T]]:
    for start in range(0, len(items), 500):
        yield items[start : start + 500]


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=409, detail=code)


def _ledgers(
    db: Session, user_id: str, source_id: str, target_id: str, *, lock: bool = False
) -> tuple[Ledger, Ledger]:
    if source_id == target_id:
        raise _conflict("MOVE_SAME_LEDGER")
    source, _ = _load_ledger_for_write(
        db, user_id=user_id, ledger_external_id=source_id, roles={"owner"}
    )
    target, _ = _load_ledger_for_write(
        db, user_id=user_id, ledger_external_id=target_id, roles={"owner"}
    )
    if lock:
        for ledger in sorted((source, target), key=lambda row: row.id):
            lock_ledger_for_materialize(db, ledger.id)
            db.refresh(ledger, with_for_update=True)
        # Recheck after acquiring the locks, including a concurrent ledger delete.
        _load_ledger_for_write(db, user_id=user_id, ledger_external_id=source_id, roles={"owner"})
        _load_ledger_for_write(db, user_id=user_id, ledger_external_id=target_id, roles={"owner"})
    if source.user_id != user_id or target.user_id != user_id:
        raise HTTPException(status_code=403, detail="MOVE_REQUIRES_OWNER")
    if any(count_ledger_members(db, ledger_id=ledger.id) > 1 for ledger in (source, target)):
        raise _conflict("MOVE_REQUIRES_PRIVATE_LEDGERS")
    if (source.currency or "CNY").upper() != (target.currency or "CNY").upper():
        raise _conflict("MOVE_REQUIRES_SAME_CURRENCY")
    return source, target


def _transactions(db: Session, ledger_id: str, ids: Iterable[str]) -> list[ReadTxProjection]:
    requested = list(ids)
    if len(set(requested)) != len(requested):
        raise HTTPException(status_code=422, detail="MOVE_DUPLICATE_SELECTION")
    found = {}
    for group in _chunks(requested):
        for row in db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == ledger_id, ReadTxProjection.sync_id.in_(group)
            )
        ):
            found[row.sync_id] = row
    if len(found) != len(requested):
        raise _conflict("MOVE_SOURCE_CHANGED")
    return [found[sync_id] for sync_id in requested]


def _payload(row: ReadTxProjection) -> dict[str, Any]:
    result = _projection_row_to_tx_dict(row)
    if row.attachments_json and not result.get("attachments"):
        raise _conflict("MOVE_ATTACHMENT_MISSING")
    return result


def _source_attachment(db: Session, source_id: str, item: Any) -> AttachmentFile:
    file_id = item.get("cloudFileId") if isinstance(item, dict) else None
    found = db.get(AttachmentFile, file_id) if isinstance(file_id, str) and file_id else None
    if (
        found is None
        or found.ledger_id != source_id
        or found.attachment_kind != "transaction"
        or not Path(found.storage_path).is_file()
    ):
        raise _conflict("MOVE_ATTACHMENT_MISSING")
    return found


def _copy_attachment(
    db: Session, *, target: Ledger, source: AttachmentFile, created_files: list[Path]
) -> AttachmentFile:
    copies = db.scalars(
        select(AttachmentFile).where(
            AttachmentFile.ledger_id == target.id,
            AttachmentFile.sha256 == source.sha256,
            AttachmentFile.attachment_kind == "transaction",
        )
    )
    for copy in copies:
        if Path(copy.storage_path).is_file():
            return copy

    file_id = str(uuid4())
    folder = _attachment_root() / target.user_id / target.id / "moved"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_id
    created_files.append(path)
    shutil.copyfile(source.storage_path, path)
    with path.open("rb") as content:
        if hashlib.file_digest(content, "sha256").hexdigest() != source.sha256:
            raise _conflict("MOVE_ATTACHMENT_MISSING")
    copy = AttachmentFile(
        id=file_id,
        ledger_id=target.id,
        user_id=target.user_id,
        sha256=source.sha256,
        size_bytes=source.size_bytes,
        mime_type=source.mime_type,
        file_name=source.file_name,
        storage_path=str(path),
        attachment_kind="transaction",
    )
    db.add(copy)
    db.flush()
    return copy


@router.post("/ledgers/{ledger_id}/transactions/batch/move/prepare")
def prepare_move(
    ledger_id: str,
    req: MovePrepareRequest,
    _scopes: set[str] = Depends(require_scopes(SCOPE_APP_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MovePreparedItem]:
    source, _ = _ledgers(db, current_user.id, ledger_id, req.target_ledger_id)
    result = []
    checked_files: set[str] = set()
    for row in _transactions(db, source.id, req.transaction_ids):
        payload = _payload(row)
        for item in payload.get("attachments", []):
            file_id = item.get("cloudFileId") if isinstance(item, dict) else None
            if not isinstance(file_id, str) or file_id not in checked_files:
                checked_files.add(_source_attachment(db, source.id, item).id)
        result.append(
            MovePreparedItem(
                sync_id=row.sync_id, revision=row.source_change_id, transaction=payload
            )
        )
    return result


def _replay(db: Session, user_id: str, operation_id: UUID, request_hash: str) -> MoveResult | None:
    receipt = db.get(TransactionMoveReceipt, (user_id, str(operation_id)))
    if receipt is None:
        return None
    if receipt.request_hash != request_hash:
        raise _conflict("MOVE_OPERATION_REUSED")
    return MoveResult.model_validate(receipt.result_json)


def _record_event(
    db: Session,
    ledger: Ledger,
    device_id: str,
    sync_id: str,
    action: str,
    payload: dict[str, Any],
) -> SyncChange:
    event = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=sync_id,
        action=action,
        payload_json=payload,
        updated_at=_utcnow(),
        updated_by_device_id=device_id,
        updated_by_user_id=ledger.user_id,
    )
    db.add(event)
    db.flush()
    return event


def _commit_move(
    db: Session, user_id: str, device_id: str, source_id: str, req: MoveRequest, operation_id: UUID
) -> tuple[MoveResult, list[tuple[str, str, int]]]:
    request_hash = _hash_request("POST", source_id, req.model_dump(mode="json"))
    # Device identity is also used by the existing pull protocol to skip its own events.
    device = db.get(Device, device_id)
    if device is None or device.user_id != user_id or device.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid device")
    replay = _replay(db, user_id, operation_id, request_hash)
    if replay:
        return replay, []

    lock_sqlite_writer(db)
    source, target = _ledgers(db, user_id, source_id, req.target_ledger_id, lock=True)
    replay = _replay(db, user_id, operation_id, request_hash)
    if replay:
        return replay, []
    revisions = {item.sync_id: item.revision for item in req.items}
    rows = _transactions(db, source.id, (item.sync_id for item in req.items))
    if any(row.source_change_id != revisions[row.sync_id] for row in rows):
        raise _conflict("MOVE_SOURCE_CHANGED")

    created_files: list[Path] = []
    attachments: dict[str, str] = {}
    moved: list[MovedItem] = []
    notices: dict[str, int] = {}
    try:
        # Prepare all file references before allocating any sync cursor or removing source rows.
        targets = []
        for row in rows:
            payload = _payload(row)
            for item in payload.get("attachments", []):
                old_id = item.get("cloudFileId") if isinstance(item, dict) else None
                if not isinstance(old_id, str) or old_id not in attachments:
                    original = _source_attachment(db, source.id, item)
                    copy = _copy_attachment(
                        db, target=target, source=original, created_files=created_files
                    )
                    attachments[original.id] = copy.id
                item["cloudFileId"] = attachments[old_id]
            targets.append((row, payload))

        index = (
            db.scalar(
                select(func.max(ReadTxProjection.tx_index)).where(
                    ReadTxProjection.ledger_id == target.id
                )
            )
            or 0
        )
        for offset, (row, payload) in enumerate(targets, start=1):
            new_id = str(uuid4())
            payload.update(
                syncId=new_id,
                ledgerSyncId=target.external_id,
                txIndex=index + offset,
                updatedByUserId=user_id,
            )
            added = _record_event(db, target, device_id, new_id, "upsert", payload)
            projection.upsert_tx(
                db,
                ledger_id=target.id,
                user_id=user_id,
                source_change_id=added.change_id,
                payload=payload,
            )
            # The initiating device must still receive this tombstone on an old-log replay.
            # Its target event uses the real device ID, so the receipt is the local writer.
            removed = _record_event(
                db, source, "server-transaction-move", row.sync_id, "delete", {}
            )
            projection.delete_tx(db, ledger_id=source.id, sync_id=row.sync_id)
            db.execute(
                delete(SyncChange).where(
                    SyncChange.ledger_id == source.id,
                    SyncChange.entity_type == "transaction",
                    SyncChange.entity_sync_id == row.sync_id,
                    SyncChange.action == "upsert",
                )
            )
            db.add(TransactionMoveRetirement(ledger_id=source.id, sync_id=row.sync_id))
            notices[target.id] = added.change_id
            notices[source.id] = removed.change_id
            moved.append(MovedItem(source_sync_id=row.sync_id, target_sync_id=new_id))

        result = MoveResult(
            operation_id=operation_id,
            items=moved,
            attachment_ids=attachments,
            server_timestamp=_utcnow(),
        )
        db.add(
            TransactionMoveReceipt(
                user_id=user_id,
                operation_id=str(operation_id),
                request_hash=request_hash,
                result_json=result.model_dump(mode="json"),
            )
        )
        db.flush()
        notifications = [
            (ledger.id, ledger.external_id, notices[ledger.id]) for ledger in (source, target)
        ]
    except Exception:
        db.rollback()
        for path in created_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("transaction.move: failed to clean a staged attachment")
        # A request reusing the same operation ID can have acquired a different ledger lock.
        replay = _replay(db, user_id, operation_id, request_hash)
        if replay:
            return replay, []
        raise

    # A failed connection during COMMIT has an unknown outcome. Keep the staged files
    # until a later cleanup can inspect the committed references.
    db.commit()
    for ledger_id, _, _ in notifications:
        snapshot_cache.invalidate(ledger_id)

    if attachments:
        try:
            lock_sqlite_writer(db)
            source_internal_id = notifications[0][0]
            lock_ledger_for_materialize(db, source_internal_id)
            projection.gc_orphan_attachments_for_ledger(
                db,
                ledger_id=source_internal_id,
                file_ids=attachments.keys(),
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("transaction.move: source attachment cleanup failed")
    return result, notifications


@router.post("/ledgers/{ledger_id}/transactions/batch/move", response_model=MoveResult)
async def move_transactions(
    ledger_id: str,
    req: MoveRequest,
    request: Request,
    operation_id: UUID = Header(alias="Idempotency-Key"),
    device_id: str = Header(alias="X-Device-ID"),
    _scopes: set[str] = Depends(require_scopes(SCOPE_APP_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MoveResult:
    result, notifications = await run_in_threadpool(
        _commit_move,
        db,
        current_user.id,
        device_id,
        ledger_id,
        req,
        operation_id,
    )
    for internal_id, external_id, cursor in notifications:
        await broadcast_to_ledger(
            db=db,
            ws_manager=request.app.state.ws_manager,
            ledger_id=internal_id,
            payload={
                "type": "sync_change",
                "ledgerId": external_id,
                "serverCursor": cursor,
                "serverTimestamp": _utcnow().isoformat(),
            },
        )
    return result
