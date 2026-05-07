"""POST /api/v1/write/ledgers/{ledger_id}/transactions/batch — 批量创建交易。

设计:.docs/web-cmdk-ai-paste-screenshot.md §六(共享给 B2 / B3)。

特殊能力(跟 single create_tx 比):
- N 笔交易一次 commit(一次 snapshot lock + 一批 SyncChange + 一次 WS broadcast)
- `auto_ai_tag`(默认 true):自动加「AI 记账」tag(lookup 已有 ILIKE `%AI%` →
  没命中按 user.locale 创建对应名)
- `extra_tag_name`:额外标签(B2 「图片记账」 / B3 「文字记账」)
- `attach_image_id`(B2 only):从 image_cache 取出 LLM 解析时缓存的图片字节,
  正式存为 attachment(共享一份),N 笔 tx 都关联同一个 attachment_id
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import snapshot_builder
from ...concurrency import lock_ledger_for_materialize
from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user
from ...models import (
    AttachmentFile,
    AuditLog,
    Ledger,
    ReadTagProjection,
    SyncPushIdempotency,
    User,
)
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE
from ...services.ai.image_cache import consume_image
from ...snapshot_mutator import create_transaction
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


class BatchTransactionItem(BaseModel):
    """跟 WriteTransactionCreateRequest 同 schema(只是不带 base_change_id)。"""
    tx_type: str = "expense"  # expense | income | transfer
    amount: float
    happened_at: datetime
    note: str | None = None
    category_name: str | None = None
    category_kind: str | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: list[str] | None = None  # 仅接受 list 形式(B2/B3 LLM 输出 array)


class BatchCreateTxRequest(BaseModel):
    """POST 体。"""
    base_change_id: int = 0
    transactions: list[BatchTransactionItem] = Field(min_length=1, max_length=50)
    # 自动加 「AI 记账」 标签(跟 mobile 行为对齐)
    auto_ai_tag: bool = True
    # 额外标签(B2 = 「图片记账」/ B3 = 「文字记账」)
    extra_tag_name: str | None = None
    # B2 only:从 ai parse-tx-image 拿到的 image_id,server 转 attachment 共享给所有 tx
    attach_image_id: str | None = None
    # locale 用来在创建 AI tag / 图片记账 tag 时按用户语言起名
    locale: str = "zh"


class BatchCreateTxResponse(BaseModel):
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    created_sync_ids: list[str] = Field(default_factory=list)
    attachment_id: str | None = None  # 共享 attachment 的 file_id(若 attach_image_id 命中)


_AI_TAG_BY_LOCALE = {
    "zh": "AI记账",
    "zh-CN": "AI记账",
    "zh-TW": "AI記帳",
    "en": "AI",
}


@router.post(
    "/ledgers/{ledger_id}/transactions/batch",
    response_model=BatchCreateTxResponse,
    responses=_WRITE_RESPONSES,
)
async def create_tx_batch(
    ledger_id: str,
    req: BatchCreateTxRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchCreateTxResponse:
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
        # 幂等回放 — 直接拿之前的 BatchCreateTxResponse
        return BatchCreateTxResponse(**replay.model_dump()) if hasattr(replay, "model_dump") else replay  # type: ignore[return-value]

    # 1. (可选)attach_image_id → 转正式 attachment 一份
    attachment_dict: dict | None = None
    attachment_file_id: str | None = None
    if req.attach_image_id:
        cached = consume_image(image_id=req.attach_image_id, user_id=current_user.id)
        if cached is None:
            logger.warning(
                "tx.batch image_id=%s not found / expired / user_id mismatch",
                req.attach_image_id,
            )
        else:
            af = _create_attachment_from_bytes(
                db=db,
                ledger=ledger,
                user_id=current_user.id,
                image_bytes=cached.image_bytes,
                mime_type=cached.mime_type,
            )
            attachment_file_id = af.id
            attachment_dict = {
                "cloudFileId": af.id,
                "fileName": af.file_name or "screenshot.jpg",
                "mimeType": af.mime_type,
                "sha256": af.sha256,
                "sizeBytes": af.size_bytes,
            }

    # 2. 解析自动 tag(AI 记账 + 可选 extra_tag),lookup ledger 已有名 → 没命中走 locale 默认
    auto_tag_names: list[str] = []
    if req.auto_ai_tag:
        ai_tag = _resolve_or_make_ai_tag_name(db, ledger, req.locale)
        auto_tag_names.append(ai_tag)
    if req.extra_tag_name:
        # extra_tag 直接用调用方给的字符串(B2「图片记账」/ B3「文字记账」)— mobile 端
        # 也是同名 i18n,跨设备一致
        auto_tag_names.append(req.extra_tag_name)

    # 3. lock + build snapshot
    lock_ledger_for_materialize(db, ledger.id)

    if get_settings().strict_base_change_id:
        latest_any_change_id = snapshot_builder.latest_change_id(db, ledger.id)
        if req.base_change_id != latest_any_change_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Write conflict",
                    "latest_change_id": latest_any_change_id,
                },
            )

    snapshot = snapshot_builder.build(db, ledger)
    prev_snapshot = {**snapshot}
    for _k in ("items", "accounts", "categories", "tags", "budgets"):
        arr = snapshot.get(_k)
        if isinstance(arr, list):
            prev_snapshot[_k] = [dict(e) if isinstance(e, dict) else e for e in arr]

    # 4. 循环 mutate snapshot,创建 N 笔
    created_sync_ids: list[str] = []
    try:
        for i, item in enumerate(req.transactions):
            tx_payload = _build_tx_payload(
                item=item,
                auto_tag_names=auto_tag_names,
                attachment_dict=attachment_dict,
                actor_user=current_user,
            )
            snapshot, sync_id = create_transaction(snapshot, tx_payload)
            if sync_id:
                created_sync_ids.append(sync_id)
    except (KeyError, ValueError, PermissionError) as exc:
        logger.warning("tx.batch mutate failed at idx=%d: %s", i, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "BATCH_TX_INVALID", "message": str(exc), "failed_index": i},
        )

    # 5. emit 整批 diffs(_emit_entity_diffs 已经支持多 entity)
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

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_tx_batch_create",
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": req.base_change_id,
                "newChangeId": new_change_id,
                "createdCount": len(created_sync_ids),
                "createdIds": created_sync_ids,
                "attachmentFileId": attachment_file_id,
                "autoTagNames": auto_tag_names,
            },
        )
    )

    response = BatchCreateTxResponse(
        ledger_id=ledger.external_id,
        base_change_id=req.base_change_id,
        new_change_id=new_change_id,
        server_timestamp=now,
        created_sync_ids=created_sync_ids,
        attachment_id=attachment_file_id,
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
                return BatchCreateTxResponse(**replay.model_dump()) if hasattr(replay, "model_dump") else replay  # type: ignore[return-value]
        raise

    logger.info(
        "tx.batch_create ledger=%s count=%d change_id=%d device=%s user=%s tags=%s attach=%s",
        ledger.external_id, len(created_sync_ids), new_change_id, device_id,
        current_user.id, auto_tag_names, attachment_file_id,
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


# ──────────────────────────────────────────────────────────────────────


def _build_tx_payload(
    *,
    item: BatchTransactionItem,
    auto_tag_names: list[str],
    attachment_dict: dict | None,
    actor_user: User,
) -> dict[str, Any]:
    """把 BatchTransactionItem + 自动 tag + 共享 attachment 拼成 single create
    所需的 payload(对应 WriteTransactionCreateRequest schema)。"""
    payload: dict[str, Any] = {
        "tx_type": item.tx_type,
        "amount": item.amount,
        "happened_at": item.happened_at.isoformat(),
        "note": item.note,
        "category_name": item.category_name,
        "category_kind": item.category_kind,
        "account_name": item.account_name,
        "from_account_name": item.from_account_name,
        "to_account_name": item.to_account_name,
        "category_id": item.category_id,
        "account_id": item.account_id,
        "from_account_id": item.from_account_id,
        "to_account_id": item.to_account_id,
    }
    # 合并 auto_tag(LLM 已识别的 tags + 自动加的 AI 记账 / 图片记账)
    user_tags = list(item.tags or [])
    merged_tags = user_tags + [t for t in auto_tag_names if t and t not in user_tags]
    if merged_tags:
        payload["tags"] = merged_tags

    if attachment_dict is not None:
        payload["attachments"] = [attachment_dict]

    return _payload_with_actor(payload, actor_user)


def _resolve_or_make_ai_tag_name(db: Session, ledger: Ledger, locale: str) -> str:
    """先 lookup ledger 已有的 AI tag(name ILIKE %AI%),没命中按 locale 起名。

    跨设备 / 跨 B2 B3 一致:
    - 已经有 mobile 创建的 `AI记账` → web 复用同名(ILIKE 命中)
    - 完全没有 → 按当前 locale 起名(zh:AI记账 / zh-TW:AI記帳 / en:AI)
    """
    rows = db.scalars(
        select(ReadTagProjection)
        .where(ReadTagProjection.ledger_id == ledger.id)
        .where(ReadTagProjection.name.ilike("%AI%"))
    ).all()
    for r in rows:
        if r.name and "AI" in r.name.upper():
            return r.name
    # 没命中 — 起名
    locale_norm = (locale or "zh").lower().replace("_", "-")
    if locale_norm.startswith("zh-tw") or locale_norm in {"zh-hant", "zh-hk", "zh-mo"}:
        return _AI_TAG_BY_LOCALE["zh-TW"]
    if locale_norm.startswith("zh"):
        return _AI_TAG_BY_LOCALE["zh"]
    return _AI_TAG_BY_LOCALE["en"]


def _create_attachment_from_bytes(
    *,
    db: Session,
    ledger: Ledger,
    user_id: str,
    image_bytes: bytes,
    mime_type: str,
) -> AttachmentFile:
    """把 image bytes 转 AttachmentFile(写盘 + 入库)。dedup 用 sha256:已有相同
    sha 的就复用,不重复存。"""
    sha256 = hashlib.sha256(image_bytes).hexdigest()
    existing = db.scalar(
        select(AttachmentFile).where(
            AttachmentFile.ledger_id == ledger.id,
            AttachmentFile.sha256 == sha256,
        )
    )
    if existing is not None:
        return existing

    settings = get_settings()
    storage_root = Path(settings.attachment_storage_dir).expanduser()
    storage_dir = storage_root / ledger.user_id / ledger.external_id / sha256[:2]
    storage_dir.mkdir(parents=True, exist_ok=True)
    ext = _ext_from_mime(mime_type)
    file_name = f"screenshot{ext}"
    storage_name = f"{uuid4().hex}_{file_name}"
    storage_path = storage_dir / storage_name
    storage_path.write_bytes(image_bytes)

    row = AttachmentFile(
        ledger_id=ledger.id,
        user_id=user_id,
        sha256=sha256,
        size_bytes=len(image_bytes),
        mime_type=mime_type,
        file_name=file_name,
        storage_path=str(storage_path),
    )
    db.add(row)
    db.flush()  # 拿 row.id,但还没 commit(跟 batch 一起 commit)
    return row


def _ext_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    if m == "image/jpeg":
        return ".jpg"
    if m == "image/png":
        return ".png"
    if m == "image/webp":
        return ".webp"
    if m == "image/gif":
        return ".gif"
    return ".bin"
