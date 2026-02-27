from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import ACTIVE_MEMBER_STATUS, READABLE_ROLES, WRITABLE_ROLES
from ..models import AttachmentFile, Ledger, LedgerMember, User
from ..schemas import (
    AttachmentBatchExistsRequest,
    AttachmentBatchExistsResponse,
    AttachmentExistsItem,
    AttachmentUploadOut,
)
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

router = APIRouter()
_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE)
_WRITE_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE)


def _attachment_root() -> Path:
    settings = get_settings()
    root = Path(settings.attachment_storage_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_file_name(raw: str) -> str:
    value = Path(raw or "").name.strip()
    return value[:255] or "attachment.bin"


def _resolve_ledger(
    db: Session,
    *,
    ledger_external_id: str,
    current_user: User,
    roles: set[str],
    forbidden_detail: str | None = None,
) -> tuple[Ledger, LedgerMember | None]:
    if current_user.is_admin:
        ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
        if ledger is None:
            raise HTTPException(status_code=404, detail="Ledger not found")
        membership = db.scalar(
            select(LedgerMember).where(
                LedgerMember.ledger_id == ledger.id,
                LedgerMember.user_id == current_user.id,
                LedgerMember.status == ACTIVE_MEMBER_STATUS,
            )
        )
        return ledger, membership

    row = db.execute(
        select(Ledger, LedgerMember)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(
            Ledger.external_id == ledger_external_id,
            LedgerMember.user_id == current_user.id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
            LedgerMember.role.in_(roles),
        )
        .limit(1)
    ).first()
    if row is None:
        if forbidden_detail:
            readable = db.execute(
                select(Ledger, LedgerMember)
                .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
                .where(
                    Ledger.external_id == ledger_external_id,
                    LedgerMember.user_id == current_user.id,
                    LedgerMember.status == ACTIVE_MEMBER_STATUS,
                    LedgerMember.role.in_(READABLE_ROLES),
                )
                .limit(1)
            ).first()
            if readable is not None:
                raise HTTPException(status_code=403, detail=forbidden_detail)
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row[0], row[1]


def _to_upload_out(row: AttachmentFile, ledger_external_id: str) -> AttachmentUploadOut:
    return AttachmentUploadOut(
        file_id=row.id,
        ledger_id=ledger_external_id,
        sha256=row.sha256,
        size=row.size_bytes,
        mime_type=row.mime_type,
        file_name=row.file_name,
        created_at=row.created_at,
    )


@router.post("/upload", response_model=AttachmentUploadOut)
async def upload_attachment(
    ledger_id: str = Form(...),
    file: UploadFile = File(...),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttachmentUploadOut:
    ledger, _ = _resolve_ledger(
        db,
        ledger_external_id=ledger_id,
        current_user=current_user,
        roles=WRITABLE_ROLES,
        forbidden_detail="Attachment write forbidden",
    )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Attachment file is empty")
    max_bytes = get_settings().attachment_max_upload_bytes
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Attachment upload too large")

    sha256 = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(AttachmentFile).where(
            AttachmentFile.ledger_id == ledger.id,
            AttachmentFile.sha256 == sha256,
        )
    )
    if existing is not None:
        return _to_upload_out(existing, ledger.external_id)

    safe_name = _safe_file_name(file.filename or "attachment.bin")
    storage_name = f"{uuid4().hex}_{safe_name}"
    storage_dir = _attachment_root() / ledger.external_id / sha256[:2]
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / storage_name
    storage_path.write_bytes(data)

    row = AttachmentFile(
        ledger_id=ledger.id,
        user_id=current_user.id,
        sha256=sha256,
        size_bytes=len(data),
        mime_type=file.content_type,
        file_name=safe_name,
        storage_path=str(storage_path),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_upload_out(row, ledger.external_id)


@router.post("/batch-exists", response_model=AttachmentBatchExistsResponse)
def batch_exists(
    req: AttachmentBatchExistsRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttachmentBatchExistsResponse:
    ledger, _ = _resolve_ledger(
        db,
        ledger_external_id=req.ledger_id,
        current_user=current_user,
        roles=WRITABLE_ROLES,
        forbidden_detail="Attachment write forbidden",
    )
    wanted = [value.strip().lower() for value in req.sha256_list if isinstance(value, str) and value.strip()]
    if not wanted:
        return AttachmentBatchExistsResponse(items=[])
    rows = db.scalars(
        select(AttachmentFile).where(
            AttachmentFile.ledger_id == ledger.id,
            AttachmentFile.sha256.in_(wanted),
        )
    ).all()
    by_sha = {row.sha256: row for row in rows}
    items = []
    for sha in wanted:
        row = by_sha.get(sha)
        items.append(
            AttachmentExistsItem(
                sha256=sha,
                exists=row is not None,
                file_id=row.id if row is not None else None,
                size=row.size_bytes if row is not None else None,
                mime_type=row.mime_type if row is not None else None,
            )
        )
    return AttachmentBatchExistsResponse(items=items)


@router.get("/{file_id}")
def download_attachment(
    file_id: str,
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    row = db.scalar(select(AttachmentFile).where(AttachmentFile.id == file_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if current_user.is_admin:
        pass
    else:
        membership = db.scalar(
            select(LedgerMember).where(
                and_(
                    LedgerMember.ledger_id == row.ledger_id,
                    LedgerMember.user_id == current_user.id,
                    LedgerMember.status == ACTIVE_MEMBER_STATUS,
                    LedgerMember.role.in_(READABLE_ROLES),
                )
            )
        )
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Attachment access forbidden")

    path = Path(row.storage_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing")

    return FileResponse(
        path=path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.file_name or path.name,
    )
