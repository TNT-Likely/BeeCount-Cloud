from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import (
    ROLE_EDITOR,
    ROLE_OWNER,
    get_accessible_ledger_by_external_id,
)
from ..models import AttachmentFile, Ledger, User
from ..schemas import (
    AttachmentBatchExistsRequest,
    AttachmentBatchExistsResponse,
    AttachmentExistsItem,
    AttachmentUploadOut,
)
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

logger = logging.getLogger(__name__)

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
    forbidden_detail: str | None = None,  # noqa: ARG001 — 角色不足统一 404
) -> tuple[Ledger, str]:
    """权限不足 → 404(避免泄露账本存在性)。返 (ledger, caller_role)。

    共享账本 Phase 1:走 ledger_access 层,自动覆盖 Owner + Editor。
    空 ``roles`` 表示"任何 member 都能访问"(read 路径),非空时按角色过滤。
    """
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles=roles if roles else None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row


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
    # 上传附件:Owner / Editor 可写。Viewer / 非成员 → 404。
    ledger, _ = _resolve_ledger(
        db,
        ledger_external_id=ledger_id,
        current_user=current_user,
        roles={ROLE_OWNER, ROLE_EDITOR},
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
        logger.info(
            "attachments.upload.dedup ledger=%s sha256=%s size=%d user=%s",
            ledger.external_id,
            sha256,
            len(data),
            current_user.id,
        )
        return _to_upload_out(existing, ledger.external_id)

    safe_name = _safe_file_name(file.filename or "attachment.bin")
    storage_name = f"{uuid4().hex}_{safe_name}"
    # 路径加 user_id 前缀隔离多用户:同一 external_id 的账本(比如两个用户
    # 各自的 "default")不再共用一个目录,删号 / 迁移也能按用户打包。
    storage_dir = _attachment_root() / ledger.user_id / ledger.external_id / sha256[:2]
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
    logger.info(
        "attachments.upload ledger=%s file=%s size=%d sha256=%s user=%s",
        ledger.external_id,
        safe_name,
        len(data),
        sha256,
        current_user.id,
    )
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
        roles=set(),
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


@router.post("/category-icons/upload", response_model=AttachmentUploadOut)
async def upload_category_icon(
    file: UploadFile = File(...),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AttachmentUploadOut:
    """分类自定义图标上传专用 endpoint。

    跟 `/upload` 的区别:
      - 不需要 ledger_id 参数(分类是 user-global,跨账本共享)
      - 落库的 AttachmentFile 行 ledger_id=NULL, attachment_kind='category_icon'
      - 存储路径不含 ledger 维度:
        `<root>/<user_id>/category-icons/<sha256[:2]>/<uuid>_<safe_name>`
      - 去重 key 是 (user_id, sha256),同一用户上传同图标不会复制存储

    历史上分类图标走通用 `/upload` + ledger_id 路径,每个账本各上传一份,
    `attachment_files` 表里同 sha256 出现 N 行(N=用户账本数)。新 endpoint
    去掉这个倍数膨胀。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Attachment file is empty")
    max_bytes = get_settings().attachment_max_upload_bytes
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Attachment upload too large")

    sha256 = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(AttachmentFile).where(
            AttachmentFile.user_id == current_user.id,
            AttachmentFile.attachment_kind == "category_icon",
            AttachmentFile.sha256 == sha256,
        )
    )
    if existing is not None:
        logger.info(
            "attachments.category_icon.dedup sha256=%s size=%d user=%s",
            sha256, len(data), current_user.id,
        )
        return _to_upload_out(existing, ledger_external_id="")

    safe_name = _safe_file_name(file.filename or "category_icon.png")
    storage_name = f"{uuid4().hex}_{safe_name}"
    # 路径:user_id/category-icons/<sha256[:2]>/...,不含 ledger 维度
    storage_dir = _attachment_root() / current_user.id / "category-icons" / sha256[:2]
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / storage_name
    storage_path.write_bytes(data)

    row = AttachmentFile(
        ledger_id=None,
        user_id=current_user.id,
        sha256=sha256,
        size_bytes=len(data),
        mime_type=file.content_type,
        file_name=safe_name,
        storage_path=str(storage_path),
        attachment_kind="category_icon",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "attachments.category_icon.upload file=%s size=%d sha256=%s user=%s",
        safe_name, len(data), sha256, current_user.id,
    )
    return _to_upload_out(row, ledger_external_id="")


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

    # 权限校验:
    # 1) admin 直接通过(管理后台需求)
    # 2) row.ledger_id 为 NULL(category_icon 类型) → 校验 row.user_id == current_user.id
    # 3) row.ledger_id 非 NULL → 当前用户必须是该 ledger 的 member(任何角色)
    if not current_user.is_admin:
        if row.ledger_id is None:
            if row.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Attachment access forbidden",
                )
        else:
            # 共享账本 Phase 1:从 Ledger.user_id 单 owner 校验改为 ledger_members 查询。
            # Editor / Viewer 也能看见同账本的附件(read 权限)。
            ledger = db.scalar(
                select(Ledger).where(Ledger.id == row.ledger_id)
            )
            if ledger is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Attachment access forbidden",
                )
            row_access = get_accessible_ledger_by_external_id(
                db,
                user_id=current_user.id,
                ledger_external_id=ledger.external_id,
            )
            if row_access is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Attachment access forbidden",
                )

    path = Path(row.storage_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing")

    logger.info(
        "attachments.download file=%s name=%s size=%d user=%s",
        row.id,
        row.file_name,
        row.size_bytes,
        current_user.id,
    )
    return FileResponse(
        path=path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.file_name or path.name,
    )
