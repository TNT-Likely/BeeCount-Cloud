"""RAG prompt 拼接 — system + chunks + user question。

设计原则(见 .docs/web-cmdk-ai-doc-search.md §4.6.3):
- 文档没说的不要编;明确说"文档没找到",不要发挥
- 引用 source 由 server 端单独贴(末尾),system prompt 让 LLM 不要在答案里重复列引用
- 中英分别用对应语言系统提示
"""
from __future__ import annotations

from .docs_index import RetrievedChunk


_SYSTEM_ZH = """\
你是 BeeCount(蜜蜂记账)的助手,只基于下面提供的「相关文档」回答用户的问题。

规则:
1. **必须用中文回答**,即使相关文档是英文也要翻译成中文输出。
2. 文档里没明确说的,直接回答「文档里没找到相关说明」,不要编造、不要发挥。
3. 答案要简洁直接 — 步骤类问题给编号步骤;概念类问题用一两句话解释。
4. 不要在答案末尾列引用来源(系统会自动贴)。
5. 不要在中文输出里夹杂英文短语,除非是专有名词(如 PIN / 2FA)。
6. 如果用户问跟 BeeCount / 记账无关,就说「这个问题不在我能回答的范围内」。
"""

_SYSTEM_EN = """\
You are the assistant for BeeCount, a personal finance app. Answer ONLY based on the
"Relevant Docs" provided below.

Rules:
1. **You MUST answer in English**, even if the relevant docs are in Chinese — translate
   them to English in your reply.
2. If the docs don't clearly say something, answer "Sorry, the docs don't cover this"
   instead of making things up.
3. Be concise. Step-by-step for how-to questions; one or two sentences for concept questions.
4. Don't list source references at the end (the system appends them automatically).
5. Don't mix Chinese characters into English output unless quoting a UI label that exists
   only in Chinese.
6. If the user asks something unrelated to BeeCount / personal finance, reply "That's outside
   what I can answer".
"""


def build_ask_messages(
    *,
    query: str,
    chunks: list[RetrievedChunk],
    lang: str = "zh",
) -> list[dict[str, str]]:
    """拼出 OpenAI-compatible /chat/completions 的 messages 数组。"""
    system = _SYSTEM_ZH if lang.startswith("zh") else _SYSTEM_EN
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        # 给每段加上 doc 路径作为 anchor,LLM 看上下文更好
        header = f"### [{i}] {c.chunk.doc_title}"
        if c.chunk.section:
            header += f" — {c.chunk.section}"
        parts.append(f"{header}\n{c.chunk.content.strip()}")
    docs_block = "\n\n".join(parts) if parts else (
        "(没找到相关文档)" if lang.startswith("zh") else "(no relevant docs found)"
    )

    if lang.startswith("zh"):
        user_content = (
            f"## 相关文档\n\n{docs_block}\n\n## 用户问题\n\n{query}"
        )
    else:
        user_content = (
            f"## Relevant Docs\n\n{docs_block}\n\n## User Question\n\n{query}"
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
