"""POST /api/v1/ai/ask — A1 文档 Q&A。

流程:
1. 从 user.profile.ai_config_json 解析 chat provider(textProviderId 对应的)
2. server-side embed 用户问题(用 EMBEDDING_API_KEY 那把 key,跟 build 同 provider)
3. 在 docs index top-K 检索 chunks
4. 拼 prompt(system + chunks + question)
5. 调 chat provider stream → SSE 转发给前端 + 末尾贴 sources

错误码(前端按 error_code 显示对应 fallback):
- AI_NO_CHAT_PROVIDER:用户没配,引导去 SettingsAiPage
- AI_EMBEDDING_UNAVAILABLE:server 没配 EMBEDDING_API_KEY(运营者侧)
- AI_DOCS_INDEX_EMPTY:索引文件没 build / 没拉到(运营者侧)
- AI_PROVIDER_ERROR:调 chat provider 出错
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user, require_any_scopes
from ...models import User, UserProfile
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE
from ...services.ai import (
    ChatProviderError,
    NoChatProviderError,
    build_ask_messages,
    embed_query,
    get_docs_index,
    resolve_chat_provider,
    stream_chat_completion,
)
from ...services.ai.provider_client import EmbeddingNotConfiguredError

logger = logging.getLogger(__name__)
router = APIRouter()

_ASK_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE)

_TOP_K = 4
_MAX_QUERY_CHARS = 1000


class AskRequest(BaseModel):
    """前端 POST /ai/ask 的 body。"""

    query: str = Field(min_length=1, max_length=_MAX_QUERY_CHARS)
    locale: str = Field(default="zh", pattern=r"^(zh|zh-CN|zh-TW|en)$")


def _sse(event_type: str, data: object) -> str:
    """SSE 一行 — JSON 序列化 + data: 前缀 + \\n\\n 结束符。"""
    return f"data: {json.dumps({'type': event_type, **(data if isinstance(data, dict) else {'value': data})}, ensure_ascii=False)}\n\n"


@router.post("/ask")
async def ask(
    req: AskRequest,
    _scopes: set[str] = Depends(_ASK_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE stream: chunk events + sources event + done event。"""

    # 1. 解析 chat provider — 没配直接返 400(前端显 fallback),不进 stream
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    try:
        provider_cfg = resolve_chat_provider(current_user, profile)
    except NoChatProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "AI_NO_CHAT_PROVIDER", "message": str(exc)},
        )

    # 2. 索引可用性检查 — 不可用也直接 503,不进 stream
    docs_idx = get_docs_index(req.locale)
    if docs_idx.is_empty:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "AI_DOCS_INDEX_EMPTY",
                "message": f"docs index for lang={req.locale!r} not loaded; check server data dir",
            },
        )

    # 3. embed 用户问题
    try:
        qvec = await embed_query(req.query)
    except EmbeddingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "AI_EMBEDDING_UNAVAILABLE", "message": str(exc)},
        )

    # 4. 检索 top-K
    retrieved = docs_idx.search(qvec, k=_TOP_K)
    if not retrieved:
        # 检索 0 命中也走流程,让 LLM 答「文档没找到」(prompt 已约束)
        logger.info("ai.ask top-K empty user=%s query=%s", current_user.id, req.query[:50])

    # 5. 拼 prompt + stream chat
    messages = build_ask_messages(query=req.query, chunks=retrieved, lang=req.locale)
    sources = [
        {
            "doc_path": c.chunk.doc_path,
            "doc_title": c.chunk.doc_title,
            "section": c.chunk.section,
            "url": c.chunk.url,
        }
        for c in retrieved
    ]

    logger.info(
        "ai.ask user=%s lang=%s query=%s top_k=%d provider=%s",
        current_user.id, req.locale, req.query[:50], len(retrieved), provider_cfg.provider_id,
    )

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for delta in stream_chat_completion(config=provider_cfg, messages=messages):
                yield _sse("chunk", {"text": delta}).encode("utf-8")
            yield _sse("sources", {"items": sources}).encode("utf-8")
            yield _sse("done", {}).encode("utf-8")
        except ChatProviderError as exc:
            logger.warning("ai.ask provider error user=%s: %s", current_user.id, exc)
            yield _sse(
                "error",
                {"error_code": "AI_PROVIDER_ERROR", "message": str(exc)[:200]},
            ).encode("utf-8")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",  # nginx 别 buffer SSE
        },
    )
