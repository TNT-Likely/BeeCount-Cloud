"""POST /api/v1/ai/parse-tx-text — B3 文字记账。

设计:.docs/web-cmdk-ai-paste-text.md。

跟 parse-tx-image 同套流程,差别:
- 输入是 JSON body 的 text(不是 multipart image)
- 用 chat provider(textProviderId)而不是 vision
- 不需要缓存(没图)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user, require_any_scopes
from ...models import User, UserProfile
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE
from ...services.ai import (
    ChatProviderError,
    JsonParseFailedError,
    NoChatProviderError,
    build_parse_tx_text_messages,
    call_chat_json,
    get_user_custom_prompt,
    resolve_chat_provider,
)
from .parse_tx_image import SchemaInvalidError, _load_ledger_context, _normalize_drafts

logger = logging.getLogger(__name__)
router = APIRouter()

_PARSE_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE)

_MAX_TEXT_CHARS = 5000


class ParseTxTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    ledger_id: str | None = None
    locale: str = Field(default="zh", pattern=r"^(zh|zh-CN|zh-TW|en)$")


@router.post("/parse-tx-text")
async def parse_tx_text(
    req: ParseTxTextRequest,
    _scopes: set[str] = Depends(_PARSE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """同步返回 `{tx_drafts: [...]}`。"""

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    try:
        chat_cfg = resolve_chat_provider(current_user, profile)
    except NoChatProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "AI_NO_CHAT_PROVIDER", "message": str(exc)},
        )

    cats, accts = _load_ledger_context(db, req.ledger_id, current_user.id)
    logger.debug(
        "ai.parse_tx_text ledger_context cats=%d accts=%d sample_cats=%s",
        len(cats), len(accts), cats[:10],
    )

    custom = get_user_custom_prompt(profile, key="parseTxTextPrompt")
    messages = build_parse_tx_text_messages(
        text=req.text,
        categories=cats,
        accounts=accts,
        now=datetime.now(timezone.utc),
        locale=req.locale,
        custom_prompt_template=custom,
    )

    logger.info(
        "ai.parse_tx_text user=%s lang=%s text_len=%d provider=%s",
        current_user.id, req.locale, len(req.text), chat_cfg.provider_id,
    )

    try:
        result = await call_chat_json(config=chat_cfg, messages=messages)
    except JsonParseFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "AI_PARSE_FAILED",
                "message": str(exc)[:200],
                "raw": exc.raw_content[:1000],
            },
        )
    except ChatProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error_code": "AI_PROVIDER_ERROR", "message": str(exc)[:200]},
        )

    try:
        drafts = _normalize_drafts(result)
    except SchemaInvalidError as exc:
        import json as _json
        raw_dump = _json.dumps(result, ensure_ascii=False)[:1000]
        logger.warning(
            "ai.parse_tx_text schema invalid: %s; raw=%s",
            exc, raw_dump,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "AI_SCHEMA_INVALID",
                "message": str(exc),
                "raw": raw_dump,
            },
        )
    return {"tx_drafts": drafts}
