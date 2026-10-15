"""当前账本的交易附件墙：在数据库展开引用、去重与分页，不传输全部流水。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import String, case, cast, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user
from ...models import AttachmentFile, ReadTxProjection, User
from ._shared import _READ_SCOPE_DEP, _require_ledger, _to_utc, router


class GalleryAttachmentOut(BaseModel):
    key: str
    attachment: dict[str, Any]
    happened_at: datetime
    mime_type: str | None
    status: str
    transactions: list[dict[str, Any]]


class GalleryAttachmentPageOut(BaseModel):
    items: list[GalleryAttachmentOut]
    total: int
    transaction_total: int
    limit: int
    offset: int


def _references(db: Session, ledger_id: str):
    tx = ReadTxProjection
    ref: Any
    if db.get_bind().dialect.name == "postgresql":
        entries = func.jsonb_array_elements(cast(tx.attachments_json, JSONB)).table_valued(
            "value", with_ordinality="key"
        ).render_derived().alias("attachment_ref")
        ref = cast(entries.c.value, JSONB)
        field = lambda key: ref[key].astext  # noqa: E731
    else:
        valid = case((func.json_valid(tx.attachments_json), tx.attachments_json), else_="[]")
        entries = func.json_each(valid).table_valued("key", "value", "type").alias("attachment_ref")
        ref = case((entries.c.type == "object", entries.c.value), else_="{}")
        field = lambda key: func.json_extract(ref, f"$.{key}")  # noqa: E731
    cloud_id = func.nullif(field("cloudFileId"), "")
    key = func.coalesce(cloud_id, literal("local:") + tx.sync_id + ":" + cast(entries.c.key, String))
    return select(
        key.label("file_key"), cloud_id.label("cloud_file_id"), ref.label("ref"),
        field("originalName").label("original_name"), field("fileName").label("file_name"),
        tx.sync_id, tx.happened_at, tx.note, tx.category_name, tx.category_sync_id,
        tx.amount, tx.tx_type, tx.currency_code, tx.native_amount, tx.account_name,
    ).select_from(tx).join(entries, true()).where(
        tx.ledger_id == ledger_id, field("fileName").is_not(None),
    ).subquery()


@router.get("/workspace/attachments", response_model=GalleryAttachmentPageOut)
def list_gallery_attachments(
    ledger_id: str = Query(min_length=1),
    q: str | None = Query(default=None, max_length=200),
    category_sync_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GalleryAttachmentPageOut:
    ledger, _ = _require_ledger(db, user_id=current_user.id, ledger_external_id=ledger_id, is_admin=False)
    refs = _references(db, ledger.id)
    query = select(refs)
    if date_from is not None:
        query = query.where(refs.c.happened_at >= date_from)
    if date_to is not None:
        query = query.where(refs.c.happened_at < date_to)
    if category_sync_id:
        query = query.where(refs.c.category_sync_id == category_sync_id)
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(*[
            column.ilike(pattern, escape="\\") for column in (
                refs.c.original_name, refs.c.file_name, refs.c.note,
                refs.c.category_name, refs.c.account_name, cast(refs.c.amount, String),
            )
        ]))
    matched = query.subquery()
    grouped = select(
        matched.c.file_key, func.max(matched.c.happened_at).label("happened_at"),
    ).group_by(matched.c.file_key).subquery()
    total = db.scalar(select(func.count()).select_from(grouped)) or 0
    transaction_total = db.scalar(select(func.count(func.distinct(matched.c.sync_id)))) or 0
    page = db.execute(select(grouped).order_by(
        grouped.c.happened_at.desc(), grouped.c.file_key.asc(),
    ).offset(offset).limit(limit)).all()
    if not page:
        return GalleryAttachmentPageOut(items=[], total=total, transaction_total=transaction_total,
                                        limit=limit, offset=offset)
    # 同页文件的全部关联交易，一次查询；筛选只控制入选文件和分组日期。
    rows = db.execute(select(refs).where(refs.c.file_key.in_([p.file_key for p in page])).order_by(
        refs.c.happened_at.desc(), refs.c.sync_id,
    )).mappings().all()
    cloud_ids = {row["cloud_file_id"] for row in rows if row["cloud_file_id"]}
    stored = {f.id: f for f in db.scalars(select(AttachmentFile).where(
        AttachmentFile.id.in_(cloud_ids), AttachmentFile.ledger_id == ledger.id,
        AttachmentFile.attachment_kind == "transaction",
    )).all()} if cloud_ids else {}
    by_key: dict[str, GalleryAttachmentOut] = {}
    seen: dict[str, set[str]] = {}
    dates = {p.file_key: p.happened_at for p in page}
    for row in rows:
        key = row["file_key"]
        if key not in by_key:
            raw = row["ref"]
            attachment = json.loads(raw) if isinstance(raw, str) else raw
            file = stored.get(row["cloud_file_id"])
            by_key[key] = GalleryAttachmentOut(
                key=key, attachment=attachment, happened_at=_to_utc(dates[key]),
                mime_type=file.mime_type if file else None,
                status="pending" if not row["cloud_file_id"] else "ready" if file else "missing",
                transactions=[],
            )
            seen[key] = set()
        if row["sync_id"] in seen[key]:
            continue
        seen[key].add(row["sync_id"])
        by_key[key].transactions.append(dict(
            id=row["sync_id"], ledger_id=ledger.external_id, note=row["note"],
            category_name=row["category_name"], category_id=row["category_sync_id"],
            amount=row["amount"], tx_type=row["tx_type"], currency_code=row["currency_code"],
            native_amount=row["native_amount"], happened_at=_to_utc(row["happened_at"]),
        ))
    return GalleryAttachmentPageOut(items=[by_key[p.file_key] for p in page], total=total,
                                    transaction_total=transaction_total, limit=limit, offset=offset)
