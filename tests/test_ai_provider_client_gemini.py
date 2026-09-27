"""Gemini 原生 apiFamily 分支单元测试。

覆盖 `_build_gemini_url` 的容错拼接、`_openai_messages_to_gemini` 的格式转换、
`_extract_gemini_text` 的响应解析,以及 `call_chat_json` 在 `api_family="gemini"`
时确实打 generateContent 而不是 `/chat/completions`。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest

from src.services.ai.provider_client import (
    ChatProviderConfig,
    ChatProviderError,
    _build_gemini_url,
    _extract_gemini_text,
    _openai_messages_to_gemini,
    call_chat_json,
)


# ──────────────── _build_gemini_url ────────────────


def test_build_gemini_url_default_path():
    url = _build_gemini_url("https://generativelanguage.googleapis.com/v1beta", "gemini-3.5-flash")
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    )


def test_build_gemini_url_no_path_defaults_v1beta():
    url = _build_gemini_url("https://generativelanguage.googleapis.com", "gemini-3.5-flash")
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    )


def test_build_gemini_url_strips_openai_suffix():
    """用户手滑贴了 openai-compat 端点 —— 容忍并纠正。"""
    url = _build_gemini_url(
        "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.5-flash"
    )
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    )


def test_build_gemini_url_strips_models_prefix_in_model_name():
    url = _build_gemini_url(
        "https://generativelanguage.googleapis.com/v1beta", "models/gemini-3.5-flash"
    )
    assert url.endswith("/models/gemini-3.5-flash:generateContent")


def test_build_gemini_url_stream_variant():
    url = _build_gemini_url(
        "https://generativelanguage.googleapis.com/v1beta", "gemini-3.5-flash", stream=True
    )
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash:streamGenerateContent?alt=sse"
    )


# ──────────────── _openai_messages_to_gemini ────────────────


def test_openai_messages_to_gemini_text_only():
    contents, system = _openai_messages_to_gemini(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert system == {"parts": [{"text": "You are helpful."}]}
    assert contents == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "hello"}]},
    ]


def test_openai_messages_to_gemini_vision_parts():
    contents, system = _openai_messages_to_gemini(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="}},
                ],
            }
        ]
    )
    assert system is None
    assert contents == [
        {
            "role": "user",
            "parts": [
                {"text": "describe"},
                {"inlineData": {"mimeType": "image/jpeg", "data": "ZmFrZQ=="}},
            ],
        }
    ]


# ──────────────── _extract_gemini_text ────────────────


def test_extract_gemini_text_happy_path():
    data = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"text": "Hi! "}, {"text": "How can I help?"}]},
            }
        ]
    }
    assert _extract_gemini_text(data) == "Hi! How can I help?"


def test_extract_gemini_text_skips_thought_parts():
    data = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"text": "thinking...", "thought": True}, {"text": "answer"}]},
            }
        ]
    }
    assert _extract_gemini_text(data) == "answer"


def test_extract_gemini_text_empty_parts_is_not_an_error():
    """静音音频转写 —— candidates 存在但没输出文本,不算错误(跟 mobile 行为一致)。"""
    data = {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}
    assert _extract_gemini_text(data) == ""


def test_extract_gemini_text_block_reason_raises():
    data = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(ChatProviderError):
        _extract_gemini_text(data)


def test_extract_gemini_text_no_candidates_raises():
    with pytest.raises(ChatProviderError):
        _extract_gemini_text({"candidates": []})


# ──────────────── call_chat_json 走 gemini 分支 ────────────────


def test_call_chat_json_gemini_hits_generate_content_endpoint():
    calls: list[dict] = []

    async def fake_post(self, url, headers=None, json=None, **_):
        calls.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"ok": true}'}]}}
                ]
            },
        )

    cfg = ChatProviderConfig(
        provider_id="p1",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="AIza-fake",
        model="gemini-3.5-flash",
        api_family="gemini",
    )
    with patch("httpx.AsyncClient.post", fake_post):
        result = asyncio.run(
            call_chat_json(config=cfg, messages=[{"role": "user", "content": "hi"}])
        )

    assert result == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["url"].endswith(":generateContent")
    assert calls[0]["headers"]["x-goog-api-key"] == "AIza-fake"
    assert "Authorization" not in calls[0]["headers"]
    assert calls[0]["json"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
