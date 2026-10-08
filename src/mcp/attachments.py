"""将已上传文件的权威元数据转换为 App/Web 共用的交易附件引用。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AttachmentFile


def resolve_transaction_attachments(
    db: Session, *, ledger_id: str, file_ids: list[str]
) -> list[dict]:
    """保持传入顺序；不允许引用其他账本、分类图标或未知文件。"""
    if any(not isinstance(value, str) or not value.strip() for value in file_ids):
        raise ValueError("attachments must contain non-empty uploaded file IDs")
    ids = [value.strip() for value in file_ids]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate attachment file IDs are not allowed")
    if not ids:
        return []
    rows = db.scalars(select(AttachmentFile).where(
        AttachmentFile.id.in_(ids),
        AttachmentFile.ledger_id == ledger_id,
        AttachmentFile.attachment_kind == "transaction",
    )).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(ids):
        # 不泄露文件是否在其他用户/账本中存在。
        raise ValueError("Attachment not found in the target ledger; upload it there first")
    return [{
        "fileName": f"{row.id}_{row.file_name or 'attachment.bin'}",
        "originalName": row.file_name or "attachment.bin",
        "fileSize": row.size_bytes,
        "mimeType": row.mime_type,
        "cloudFileId": row.id,
        "cloudSha256": row.sha256,
        "sortOrder": index,
    } for index, file_id in enumerate(ids) for row in [by_id[file_id]]]
