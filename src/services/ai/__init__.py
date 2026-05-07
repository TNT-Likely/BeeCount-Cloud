"""AI services — RAG 文档检索 + LLM provider 抽象。

子模块:
- docs_index: 启动时 load sqlite 索引到内存,提供 cosine 检索
- provider_client: 从 user.ai_config_json 取 chat provider 配置 + 调 OpenAI-compatible API
- prompts: 拼 RAG prompt(system + chunks + question)

设计文档:.docs/web-cmdk-ai-doc-search.md
"""
from .docs_index import DocsIndex, get_docs_index, reset_docs_index_cache
from .provider_client import (
    ChatProviderConfig,
    ChatProviderError,
    JsonParseFailedError,
    NoChatProviderError,
    NoVisionProviderError,
    call_chat_json,
    embed_query,
    get_user_custom_prompt,
    resolve_chat_provider,
    resolve_vision_provider,
    stream_chat_completion,
)
from .prompts import (
    build_ask_messages,
    build_parse_tx_image_messages,
    build_parse_tx_text_messages,
)

__all__ = [
    "DocsIndex",
    "get_docs_index",
    "reset_docs_index_cache",
    "ChatProviderConfig",
    "ChatProviderError",
    "JsonParseFailedError",
    "NoChatProviderError",
    "NoVisionProviderError",
    "call_chat_json",
    "embed_query",
    "get_user_custom_prompt",
    "resolve_chat_provider",
    "resolve_vision_provider",
    "stream_chat_completion",
    "build_ask_messages",
    "build_parse_tx_image_messages",
    "build_parse_tx_text_messages",
]
