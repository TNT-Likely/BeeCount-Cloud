"""Provider client — 解析 user.ai_config_json 拿 chat provider + 调用 OpenAI-compatible API。

跟 mobile lib/services/ai/ai_provider_config.dart 的 schema 对齐:

    ai_config = {
        "providers": [
            {
                "id": "zhipu_glm",
                "apiKey": "sk-xxx",
                "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                "textModel": "glm-4-flash",
                "visionModel": "glm-4v-flash",
                ...
            }
        ],
        "binding": {
            "textProviderId": "zhipu_glm",
            "visionProviderId": "zhipu_glm",
            ...
        }
    }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from ...config import get_settings
from ...models import User, UserProfile

logger = logging.getLogger(__name__)


# Embedding(server-side,跟 build 时同步配置) ──────────────────────────────


class EmbeddingNotConfiguredError(RuntimeError):
    """Server 没配 EMBEDDING_API_KEY — A1 endpoint 直接 503,管理员必须配。"""


async def embed_query(query: str) -> list[float]:
    """server-side 把用户问题 embed 成向量。用 server 持有的 key,不消耗用户配额。

    配置走 src/config.py Settings(读 .env 文件)。部署者在 .env 里设
    `EMBEDDING_API_KEY=...`,无需启动时传 env var。
    """
    settings = get_settings()
    if not settings.embedding_api_key:
        raise EmbeddingNotConfiguredError(
            "EMBEDDING_API_KEY 未配置;请在 .env 文件或环境变量设置 SiliconFlow / OpenAI key"
        )
    async with httpx.AsyncClient(
        timeout=settings.embedding_timeout,
        verify=settings.ai_http_verify_ssl,
    ) as client:
        resp = await client.post(
            f"{settings.embedding_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            json={"model": settings.embedding_model, "input": query},
        )
        resp.raise_for_status()
        data = resp.json()
    embedding = data["data"][0]["embedding"]
    if not isinstance(embedding, list):
        raise RuntimeError("embedding API 返回 shape 异常")
    return [float(x) for x in embedding]


# Chat provider 解析 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChatProviderConfig:
    """从 user.ai_config_json 解析出的 chat 配置。"""

    provider_id: str
    base_url: str
    api_key: str
    model: str           # textModel
    name: str | None = None


class ChatProviderError(RuntimeError):
    """通用 provider 调用失败。"""


class NoChatProviderError(ChatProviderError):
    """用户没配 / binding 找不到 — 前端显示 fallback 提示。"""


def resolve_chat_provider(user: User, profile: UserProfile | None) -> ChatProviderConfig:
    """从 user profile 拿 text provider 配置;没配 / 不完整时 raise NoChatProviderError。"""
    if profile is None or not profile.ai_config_json:
        raise NoChatProviderError("user has no ai_config")

    try:
        cfg = json.loads(profile.ai_config_json)
    except (ValueError, TypeError) as exc:
        raise NoChatProviderError(f"ai_config_json invalid JSON: {exc}") from exc

    if not isinstance(cfg, dict):
        raise NoChatProviderError("ai_config not a dict")

    binding = cfg.get("binding") if isinstance(cfg.get("binding"), dict) else {}
    text_provider_id = binding.get("textProviderId")
    if not text_provider_id:
        raise NoChatProviderError("textProviderId not bound")

    providers = cfg.get("providers") if isinstance(cfg.get("providers"), list) else []
    matched: dict[str, Any] | None = None
    for p in providers:
        if isinstance(p, dict) and p.get("id") == text_provider_id:
            matched = p
            break
    if matched is None:
        raise NoChatProviderError(f"provider {text_provider_id!r} not found")

    api_key = matched.get("apiKey") or ""
    base_url = matched.get("baseUrl") or ""
    text_model = matched.get("textModel") or ""

    if not api_key:
        raise NoChatProviderError(f"provider {text_provider_id!r} apiKey empty")
    if not base_url:
        raise NoChatProviderError(f"provider {text_provider_id!r} baseUrl empty")
    if not text_model:
        raise NoChatProviderError(f"provider {text_provider_id!r} textModel empty")

    return ChatProviderConfig(
        provider_id=text_provider_id,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=text_model,
        name=matched.get("name"),
    )


# Streaming chat ────────────────────────────────────────────────────────────


async def stream_chat_completion(
    *,
    config: ChatProviderConfig,
    messages: list[dict[str, str]],
    timeout: float = 30.0,
) -> AsyncIterator[str]:
    """调 provider /chat/completions stream=true,yield 增量 content。

    OpenAI-compatible API:GLM / OpenAI / DeepSeek / 智谱 / SiliconFlow 都走同一套。
    SSE 解析:每行 `data: {...}`,看 choices[0].delta.content。`data: [DONE]` 结束。

    出错抛 ChatProviderError(不细分:对前端来说就是「AI 服务出错,请重试 / 检查 key」)。
    """
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,  # 低 temperature → 答案更稳,文档 QA 不需要创造性
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    url = f"{config.base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=get_settings().ai_http_verify_ssl,
        ) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise ChatProviderError(
                        f"provider {config.provider_id} returned {resp.status_code}: {body[:200]}"
                    )
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:"):].strip()
                    if payload_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload_str)
                    except (ValueError, TypeError):
                        logger.warning("ai.chat malformed SSE chunk: %s", payload_str[:80])
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
    except httpx.HTTPError as exc:
        raise ChatProviderError(f"network error: {exc}") from exc
