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
import re
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
    return _resolve_provider_by_kind(profile, kind="text")


class NoVisionProviderError(ChatProviderError):
    """用户没绑 vision 模型 — B2 截图记账专用 fallback。"""


def resolve_vision_provider(user: User, profile: UserProfile | None) -> ChatProviderConfig:
    """从 user profile 拿 vision provider 配置(用 visionProviderId + visionModel)。

    没配 → NoVisionProviderError(让前端跳官网 / 引导去 mobile 配)。
    """
    return _resolve_provider_by_kind(
        profile,
        kind="vision",
        not_found_exc=NoVisionProviderError,
    )


def _resolve_provider_by_kind(
    profile: UserProfile | None,
    *,
    kind: str,  # "text" | "vision"
    not_found_exc: type[ChatProviderError] = NoChatProviderError,
) -> ChatProviderConfig:
    """B2/B3 复用的 provider 解析 — 跟 resolve_chat_provider 同模式,只是
    binding 字段名 + provider 字段名按 kind 切。
    """
    if profile is None or not profile.ai_config_json:
        raise not_found_exc(f"user has no ai_config (kind={kind})")

    try:
        cfg = json.loads(profile.ai_config_json)
    except (ValueError, TypeError) as exc:
        raise not_found_exc(f"ai_config_json invalid JSON: {exc}") from exc

    if not isinstance(cfg, dict):
        raise not_found_exc("ai_config not a dict")

    binding_key = "textProviderId" if kind == "text" else "visionProviderId"
    model_key = "textModel" if kind == "text" else "visionModel"

    binding = cfg.get("binding") if isinstance(cfg.get("binding"), dict) else {}
    provider_id = binding.get(binding_key)
    if not provider_id:
        raise not_found_exc(f"{binding_key} not bound")

    providers = cfg.get("providers") if isinstance(cfg.get("providers"), list) else []
    matched: dict[str, Any] | None = None
    for p in providers:
        if isinstance(p, dict) and p.get("id") == provider_id:
            matched = p
            break
    if matched is None:
        raise not_found_exc(f"provider {provider_id!r} not found")

    api_key = matched.get("apiKey") or ""
    base_url = matched.get("baseUrl") or ""
    model = matched.get(model_key) or ""

    if not api_key:
        raise not_found_exc(f"provider {provider_id!r} apiKey empty")
    if not base_url:
        raise not_found_exc(f"provider {provider_id!r} baseUrl empty")
    if not model:
        raise not_found_exc(f"provider {provider_id!r} {model_key} empty")

    return ChatProviderConfig(
        provider_id=provider_id,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        name=matched.get("name"),
    )


def get_user_custom_prompt(profile: UserProfile | None, key: str) -> str | None:
    """从 user.ai_config_json 读自定义 prompt template。

    key 是 ai_config_json 里的字段名:
    - `parseTxImagePrompt` — B2 截图
    - `parseTxTextPrompt` — B3 文本
    没有就返 None,server 用 default。**第一期 web UI 不暴露编辑入口**,留给 mobile
    端同步过来的 hook(避免两端配冲突)。
    """
    if profile is None or not profile.ai_config_json:
        return None
    try:
        cfg = json.loads(profile.ai_config_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    val = cfg.get(key)
    if isinstance(val, str) and val.strip():
        return val
    return None


# JSON-mode chat call(非 streaming,B2/B3 用) ─────────────────────────────


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_FIRST_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json(raw: str) -> dict | list | None:
    """从 LLM 原始输出抽 JSON 值。

    抽取本身允许 dict / list / 用 ```json``` 代码块包裹 — 这是「LLM 输出文本
    格式」的鲁棒,跟 schema 验证是两件事。schema 严格性由 caller 的
    `_normalize_drafts` 强制(必须 `{"tx_drafts": [...]}`),不在 parser 兼容。
    """
    if not raw:
        return None
    # 1. 直接 try
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
    except (ValueError, TypeError):
        pass
    # 2. ```json ... ``` 代码块
    m = _JSON_BLOCK_RE.search(raw)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, (dict, list)):
                return parsed
        except (ValueError, TypeError):
            pass
    # 3. 第一个 { ... } 块兜底(主要给「LLM 输出含前后缀解释文字」)
    m = _FIRST_OBJECT_RE.search(raw)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, TypeError):
            pass
    return None


class JsonParseFailedError(ChatProviderError):
    """LLM 输出无法解析为 JSON,重试后仍失败。带上 raw_content 给排查。"""

    def __init__(self, message: str, *, raw_content: str = ""):
        super().__init__(message)
        self.raw_content = raw_content


# 自适应参数剥离 ────────────────────────────────────────────────────────────
#
# 不同模型对 OpenAI-compatible 参数有不同约束:推理模型(kimi-k2.5 / o1 / o3 /
# DeepSeek-R1)把 temperature 锁死成 1,拒绝其他值;部分模型不支持 response_format。
# 与其按模型/参数名写死兼容分支,不如「听上游报错动态适配」:上游因某个参数报错时
# 它会点名是哪个参数,我们照它说的把那个参数摘掉重发。

# 结构上必须保留的键;其余键(temperature / top_p / response_format / max_tokens …)
# 都是「可丢的可选参数」:被上游拒绝时摘掉重发。
_REQUIRED_KEYS = frozenset({"model", "messages", "stream"})
# 防止烂网关让我们无限摘参数空转
_MAX_PARAM_STRIPS = 3


def _rejected_param(payload: dict, status_code: int, body: str) -> str | None:
    """上游因「参数不合法」报错时,返回它点名的那个参数(且必须是我们发出去的可丢键)。

    优先结构化 error.param(OpenAI / o1 / o3 错误体里直接给 ``"param": "temperature"``);
    没有该字段(Moonshot 那种 ``invalid temperature: only 1 is allowed for this model``)
    就扫错误文案里点了我们发出去的哪个键。

    返回 None = 不是参数问题 / 点名的是必须键 / 没的可摘了 → 交给上层照常报错。
    """
    if status_code < 400:
        return None
    # 1) 结构化:{"error": {"param": "temperature", ...}}
    try:
        err = json.loads(body).get("error")
        if isinstance(err, dict):
            param = err.get("param")
            if isinstance(param, str) and param in payload and param not in _REQUIRED_KEYS:
                return param
    except (ValueError, TypeError, AttributeError):
        pass
    # 2) 文案兜底:错误信息点了我们发出去的哪个可丢键
    low = body.lower()
    for key in payload:
        if key not in _REQUIRED_KEYS and key.lower() in low:
            return key
    return None


async def _post_chat_adaptive(
    client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
) -> httpx.Response:
    """POST /chat/completions;若上游因某个可选参数报 4xx,摘掉它重发,最多 _MAX_PARAM_STRIPS 次。

    普通模型:参数都合法 → 一次成功,行为完全不变。
    推理模型(k2.5 / o1 / o3 / R1):温度等被锁 → 摘掉 → 用模型默认值,通过。

    只在「拿到响应且是参数类错误」时重试;超时 / 网络异常照常向上抛(由调用方处理)。
    """
    payload = dict(payload)  # 不改调用方的 dict
    resp = await client.post(url, headers=headers, json=payload)
    for _ in range(_MAX_PARAM_STRIPS):
        if resp.status_code < 400:
            return resp
        param = _rejected_param(payload, resp.status_code, resp.text)
        if param is None:
            return resp  # 不是参数问题 / 没的可摘 → 原样返回,交给上层报错
        logger.info(
            "ai.param_stripped param=%s model=%s status=%d",
            param, payload.get("model"), resp.status_code,
        )
        # 重建(而非 in-place pop):每次 POST 用独立 dict,不改已发出去的引用
        payload = {k: v for k, v in payload.items() if k != param}
        resp = await client.post(url, headers=headers, json=payload)
    return resp  # 摘到上限仍失败,返回最后一次让上层报错


async def call_chat_json(
    *,
    config: ChatProviderConfig,
    messages: list[dict[str, object]],
    timeout: float = 30.0,
    max_retries: int = 1,
) -> dict | list:
    """调 OpenAI-compatible /chat/completions(非 stream),返 JSON。

    重试策略:
    - attempt 0:带 `response_format={"type": "json_object"}`(部分 provider 支持,提高准确率)
    - attempt 1+:去掉 `response_format`(兼容不支持该参数的 provider,有些网关传了会卡死)
    - 都依赖 `_try_parse_json` 鲁棒抽 JSON(允许 markdown code block 包裹 / 前后缀文字)
    """
    import time

    last_exc: Exception | None = None
    url = f"{config.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        # attempt 0 带 response_format;重试(解析失败 / 超时)降 temperature 并去掉
        # response_format。参数被模型拒绝(如推理模型锁 temperature、不支持 response_format)
        # 由 _post_chat_adaptive 在单次调用内自适应摘除,不依赖 attempt 切换。
        temperature = 0.2 if attempt == 0 else 0.05
        payload: dict[str, object] = {
            "model": config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if attempt == 0:
            payload["response_format"] = {"type": "json_object"}

        t0 = time.monotonic()
        logger.info(
            "ai.call_chat_json provider=%s model=%s attempt=%d msgs=%d response_format=%s",
            config.provider_id, config.model, attempt + 1, len(messages),
            attempt == 0,
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                verify=get_settings().ai_http_verify_ssl,
            ) as client:
                resp = await _post_chat_adaptive(client, url, headers, payload)
            elapsed = time.monotonic() - t0
            logger.info(
                "ai.call_chat_json done attempt=%d status=%d elapsed=%.2fs body_len=%d",
                attempt + 1, resp.status_code, elapsed, len(resp.text),
            )
            if resp.status_code >= 400:
                # 参数类 4xx 已由 _post_chat_adaptive 摘参数重试过;到这说明不是参数问题
                raise ChatProviderError(
                    f"provider {config.provider_id} returned {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            parsed = _try_parse_json(content or "")
            if parsed is not None:
                return parsed
            last_exc = JsonParseFailedError(
                f"LLM did not return parseable JSON (attempt {attempt + 1}); "
                f"raw[:120]={content[:120]!r}",
                raw_content=content or "",
            )
            logger.warning(
                "ai.call_chat_json json parse failed attempt=%d raw=%s",
                attempt + 1, (content or "")[:300],
            )
        except httpx.TimeoutException as exc:
            elapsed = time.monotonic() - t0
            logger.warning(
                "ai.call_chat_json timeout attempt=%d elapsed=%.2fs err=%s",
                attempt + 1, elapsed, exc,
            )
            last_exc = ChatProviderError(
                f"provider {config.provider_id} timed out after {elapsed:.1f}s"
            )
            # timeout 也允许 retry(下一轮去掉 response_format,某些网关吞 json_object 卡死)
            if attempt < max_retries:
                continue
            raise last_exc from exc
        except httpx.HTTPError as exc:
            elapsed = time.monotonic() - t0
            logger.warning(
                "ai.call_chat_json http error attempt=%d elapsed=%.2fs err=%s",
                attempt + 1, elapsed, exc,
            )
            raise ChatProviderError(f"network error: {exc}") from exc
    # 所有重试都解析失败
    raise last_exc or JsonParseFailedError("unknown JSON parse failure")


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
            # 参数被模型拒绝(如推理模型锁 temperature)→ 摘掉重开一次流。
            attempt_payload = dict(payload)
            for _ in range(_MAX_PARAM_STRIPS + 1):
                async with client.stream("POST", url, headers=headers, json=attempt_payload) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        param = _rejected_param(attempt_payload, resp.status_code, body)
                        if param is None:
                            raise ChatProviderError(
                                f"provider {config.provider_id} returned {resp.status_code}: {body[:200]}"
                            )
                        logger.info(
                            "ai.stream_param_stripped param=%s model=%s status=%d",
                            param, attempt_payload.get("model"), resp.status_code,
                        )
                        attempt_payload = {
                            k: v for k, v in attempt_payload.items() if k != param
                        }
                        continue  # 摘掉该参数,重开流
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
                    return  # 流正常结束
            raise ChatProviderError(
                f"provider {config.provider_id} stream failed after stripping params"
            )
    except httpx.HTTPError as exc:
        raise ChatProviderError(f"network error: {exc}") from exc
