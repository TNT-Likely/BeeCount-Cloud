"""In-process ASGI HTTP client for MCP write tools.

MCP write tools 走 HTTP self-call 到 `/api/v1/write/*`,这样能完整复用
FastAPI dep tree、idempotency、sync_change 登记、WebSocket 推送等逻辑,
跟 web / mobile 走完全相同的代码路径。

实现:`httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app))`
直接在进程内挂 ASGI 应用,**不出 TCP**,延迟近似函数调用。

late-binds 到 `src.main:app` —— 因为 main.py 在 startup 时已经把所有 router
都挂上;在 mcp tool 实际调用时(server 已经在跑),app 一定就绪。模块 import
时**不能**碰 main.app,否则会触发循环 import。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def get_internal_client() -> httpx.AsyncClient:
    """返回一个进程级单例的 httpx.AsyncClient,挂载在 FastAPI ASGI app 上。

    第一次调用时构造;之后复用同一个 client(httpx 内部 keep-alive 池)。
    线程不安全(单 event loop 模型),并发 await 是 OK 的。
    """
    global _client
    if _client is None:
        from .main import app  # late import,绕过循环 import + 等 app 就绪

        _client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mcp-internal.invalid",
            timeout=httpx.Timeout(30.0, read=120.0),
        )
        logger.info("mcp: internal ASGI client initialized")
    return _client


async def close_internal_client() -> None:
    """app shutdown hook 用 — 释放 keep-alive 连接。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
