"""POST /api/v1/ai/test-provider — 测试某个 AI 服务商配置可用性。

设计:.docs/web-ai-config-edit.md §2.4 / §3.6

跟 mobile `AIProviderFactory.validateXxxCapability` 行为对齐:用 server
中转,绕开 CORS + 复用现有 httpx;3 种 capability(text / vision / speech)
分别打一个最小 prompt / 测试图 / 静音 WAV 验证 key + base_url + model。

失败用 200 + body.success=false + error_code 模式 — 测试是诊断工具不是异常
路径,前端按 enum 出 i18n 友好提示,而不是抛 ApiError。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...config import get_settings
from ...deps import get_current_user
from ...models import User
from ...services.ai.test_samples import (
    TEST_JPEG_DATA_URL,
    TEST_WAV_BYTES,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────────── 请求 / 响应 schema ────────────────


class TestProviderProvider(BaseModel):
    """request 里嵌入的 provider 配置 — 跟 mobile AIServiceProviderConfig.toJson() 对齐。
    驼峰式字段,**不要** snake_case 化(client 直传 mobile 同款 shape)。
    """
    id: str | None = None
    name: str | None = None
    isBuiltIn: bool = False
    apiKey: str = ""
    baseUrl: str = ""
    textModel: str = ""
    visionModel: str = ""
    audioModel: str = ""


class TestProviderRequest(BaseModel):
    provider: TestProviderProvider
    capability: Literal["text", "vision", "speech"]


class TestProviderResponse(BaseModel):
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: int = 0
    preview: str = ""  # text/vision 返一段输出预览,speech 留空


# ──────────────── 速率限制(内存) ────────────────

# user_id → deque of timestamps(秒)。窗口外的元素自动出队。
_RATE_WINDOWS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_WINDOW_S = 60.0
_RATE_LIMIT_MAX = 30


def _check_rate_limit(user_id: str) -> bool:
    """单 user 60 秒内最多 30 次。返回 True 表示放行。"""
    now = time.monotonic()
    window = _RATE_WINDOWS[user_id]
    while window and now - window[0] > _RATE_LIMIT_WINDOW_S:
        window.popleft()
    if len(window) >= _RATE_LIMIT_MAX:
        return False
    window.append(now)
    return True


# ──────────────── 错误码 ────────────────


def _classify_error(status_code: int, body: str) -> str:
    """把上游 HTTP 错误归类到 frontend 认的 error_code。"""
    if status_code == 401 or status_code == 403:
        return "AI_TEST_AUTH"
    if status_code == 429:
        # 上游 LLM 限流,跟我们的 RATE_LIMITED 区分(那个是 server 自己限);
        # 但 frontend 文案可以共用一份 — 用户体感都是"等等再试"
        return "AI_TEST_RATE_LIMITED"
    body_lower = body.lower() if isinstance(body, str) else ""
    if status_code == 404:
        return "AI_TEST_MODEL_NOT_FOUND"
    if status_code in (400, 422):
        # 常见:model not found / model not supported
        if "model" in body_lower and ("not" in body_lower or "invalid" in body_lower or "support" in body_lower):
            return "AI_TEST_MODEL_NOT_FOUND"
        return "AI_TEST_UNKNOWN"
    return "AI_TEST_UNKNOWN"


# ──────────────── endpoint ────────────────


@router.post("/test-provider", response_model=TestProviderResponse)
async def test_provider(
    req: TestProviderRequest,
    current_user: User = Depends(get_current_user),
) -> TestProviderResponse:
    if not _check_rate_limit(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "AI_TEST_RATE_LIMITED"},
        )

    p = req.provider
    cap = req.capability

    # 校验必填字段:apiKey + baseUrl + 对应 capability 的 model
    if not p.apiKey or not p.baseUrl:
        return TestProviderResponse(
            success=False,
            error_code="AI_TEST_MISSING_FIELDS",
            error_message="apiKey or baseUrl empty",
        )

    model: str
    if cap == "text":
        model = p.textModel
    elif cap == "vision":
        model = p.visionModel
    else:
        model = p.audioModel
    if not model:
        return TestProviderResponse(
            success=False,
            error_code="AI_TEST_MISSING_FIELDS",
            error_message=f"{cap} model not configured",
        )

    base_url = p.baseUrl.rstrip("/")
    started = time.monotonic()
    try:
        if cap == "text":
            preview = await _test_text(base_url, p.apiKey, model)
        elif cap == "vision":
            preview = await _test_vision(base_url, p.apiKey, model)
        else:
            preview = await _test_speech(base_url, p.apiKey, model)
        latency = int((time.monotonic() - started) * 1000)
        logger.info(
            "ai.test_provider success user=%s capability=%s model=%s latency=%dms",
            current_user.id, cap, model, latency,
        )
        return TestProviderResponse(
            success=True,
            latency_ms=latency,
            preview=preview[:200] if preview else "",
        )
    except _UpstreamHTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        code = _classify_error(exc.status_code, exc.body)
        logger.warning(
            "ai.test_provider upstream error user=%s capability=%s status=%d code=%s body=%s",
            current_user.id, cap, exc.status_code, code, exc.body[:200],
        )
        return TestProviderResponse(
            success=False,
            error_code=code,
            error_message=f"{exc.status_code}: {exc.body[:200]}",
            latency_ms=latency,
        )
    except httpx.TimeoutException as exc:
        latency = int((time.monotonic() - started) * 1000)
        logger.warning("ai.test_provider timeout user=%s elapsed=%dms err=%s", current_user.id, latency, exc)
        return TestProviderResponse(
            success=False, error_code="AI_TEST_TIMEOUT",
            error_message=str(exc), latency_ms=latency,
        )
    except httpx.HTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        logger.warning("ai.test_provider network user=%s elapsed=%dms err=%s", current_user.id, latency, exc)
        return TestProviderResponse(
            success=False, error_code="AI_TEST_NETWORK",
            error_message=str(exc), latency_ms=latency,
        )
    except Exception as exc:  # noqa: BLE001 — diagnostic last-resort
        latency = int((time.monotonic() - started) * 1000)
        logger.exception("ai.test_provider unknown user=%s err=%s", current_user.id, exc)
        return TestProviderResponse(
            success=False, error_code="AI_TEST_UNKNOWN",
            error_message=str(exc), latency_ms=latency,
        )


# ──────────────── 内部 helpers ────────────────


class _UpstreamHTTPError(Exception):
    """上游 HTTP 4xx/5xx,带 status + body 给上层分类。"""

    def __init__(self, status_code: int, body: str):
        super().__init__(f"upstream {status_code}: {body[:120]}")
        self.status_code = status_code
        self.body = body


async def _test_text(base_url: str, api_key: str, model: str) -> str:
    """text capability:发个 'hi' 收第一段回复。"""
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(
        timeout=15.0,
        verify=get_settings().ai_http_verify_ssl,
    ) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        raise _UpstreamHTTPError(resp.status_code, resp.text)
    data = resp.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()


async def _test_vision(base_url: str, api_key: str, model: str) -> str:
    """vision capability:发 64×64 红色 JPEG + 'describe' prompt。"""
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": TEST_JPEG_DATA_URL}},
                ],
            }
        ],
        "max_tokens": 16,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(
        timeout=20.0,
        verify=get_settings().ai_http_verify_ssl,
    ) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        raise _UpstreamHTTPError(resp.status_code, resp.text)
    data = resp.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()


async def _test_speech(base_url: str, api_key: str, model: str) -> str:
    """speech capability:发 1 秒静音 WAV 到 /audio/transcriptions。
    静音返空也算成功(跟 mobile 行为一致)。
    """
    url = f"{base_url}/audio/transcriptions"
    files = {
        "file": ("silence.wav", TEST_WAV_BYTES, "audio/wav"),
    }
    data = {"model": model}
    async with httpx.AsyncClient(
        timeout=20.0,
        verify=get_settings().ai_http_verify_ssl,
    ) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=data,
        )
    if resp.status_code >= 400:
        raise _UpstreamHTTPError(resp.status_code, resp.text)
    body = resp.json()
    return (body.get("text") or "").strip()
