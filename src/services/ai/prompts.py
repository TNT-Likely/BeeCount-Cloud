"""AI prompt 模板 — 文档 Q&A(A1)+ 记账提取(B2 截图 / B3 文本)。

设计文档:
- A1 文档 Q&A:`.docs/web-cmdk-ai-doc-search.md`
- B2 截图记账:`.docs/web-cmdk-ai-paste-screenshot.md`
- B3 文字记账:`.docs/web-cmdk-ai-paste-text.md`

通用规则:
- 文档没说的不要编;明确说"文档没找到",不要发挥(A1)
- 必须用 user locale 对应语言回答 / 输出(避免中英 mixing)
- LLM 输出 JSON 时强制 array(`tx_drafts: [...]`),前端不分单/多笔
"""
from __future__ import annotations

from datetime import datetime

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


# ────────────────────────────────────────────────────────────────────────
# B2 / B3 记账提取 — 截图 / 文字 → 1-N 笔 tx draft
# ────────────────────────────────────────────────────────────────────────

# B2 / B3 共用 schema(让 LLM 知道要输出什么)
_TX_DRAFTS_SCHEMA_ZH = """\
**输出格式必须严格遵守**:返回一个 JSON 对象,**最外层是 dict 不是 array**,
只有一个 key `tx_drafts`,值才是 array。

正确:`{"tx_drafts": [{...}, {...}]}`
**错误**(不要这样):`[{...}]`、`{"transactions": [...]}`、`{"items": [...]}`、
单笔 dict `{...}`。即使只识别到 1 笔也要用 array。识别到 0 笔返 `{"tx_drafts": []}`。

每笔交易包含字段:
  - `type`: "expense" | "income" | "transfer"
  - `amount`: 数字,绝对值(不带正负号)
  - `happened_at`: ISO 8601 datetime,根据原文/图片日期推断;没日期用 "{{NOW}}"
  - `category_name`: 从「可用类目」选,选不到用 ""(留给用户在前端选)
  - `account_name`: 从「可用账户」选,选不到用 "";仅 expense/income 有意义
  - `from_account_name` / `to_account_name`: 仅 transfer 用
  - `note`: ≤15 字商家名 / 商品名 / 简短描述
  - `currency`: 币种,必须是 3 位大写 ISO 4217 代码,**不要填货币符号,也不要填中文名**
    - 常见对应:美元/美金/$ → USD,日元/日圆/円 → JPY,欧元/欧/€ → EUR,
      英镑/£ → GBP,港币/港元 → HKD,新台币 → TWD,韩元 → KRW,泰铢 → THB
    - 与账本主币种相同时留 ""(主币种是 CNY 时,「花了 50 元」留 "")
    - 原文/图片出现任何外币说法(中文名、符号、代码都算)就必须填,别漏
    - 例:「花了 45 美元」→ "USD";「星巴克 $6.5」→ "USD";「1200 日元」→ "JPY"
  - `tags`: array,可空
  - `confidence`: "high" | "medium" | "low"
    - high: 金额 + 类型 + 时间 都明确从原始内容抠出来
    - medium: 某些字段是合理推断(类目)
    - low: 多个字段不确定,请用户核对
"""

_TX_DRAFTS_SCHEMA_EN = """\
**STRICT OUTPUT FORMAT**: Return a JSON object — **the top level MUST be a dict, NOT an array** —
with exactly one key `tx_drafts` whose value is an array.

Correct: `{"tx_drafts": [{...}, {...}]}`
**WRONG** (don't do): `[{...}]`, `{"transactions": [...]}`, `{"items": [...]}`,
or a single object `{...}`. Always use array even for a single tx. For 0 tx return `{"tx_drafts": []}`.

Each draft has fields:
  - `type`: "expense" | "income" | "transfer"
  - `amount`: number, absolute value (no signs)
  - `happened_at`: ISO 8601 datetime; infer from source; fallback to "{{NOW}}"
  - `category_name`: pick from available categories; "" if no match
  - `account_name`: pick from available accounts; "" if no match (for expense/income)
  - `from_account_name` / `to_account_name`: only for transfer
  - `note`: ≤15 chars merchant/item/short description
  - `currency`: MUST be a 3-letter uppercase ISO 4217 code — **never a currency
    symbol, never a currency name**
    - Mapping: dollar/dollars/$ → USD, yen/円 → JPY, euro/€ → EUR, pound/£ → GBP,
      HK dollar → HKD, NT dollar → TWD, won → KRW, baht → THB
    - Leave "" when it equals the ledger's base currency
    - Fill it whenever the source states a foreign currency in ANY form (name,
      symbol or code) — don't skip it
    - e.g. "spent 45 dollars" → "USD"; "Starbucks $6.5" → "USD"; "1200 yen" → "JPY"
  - `tags`: array, can be empty
  - `confidence`: "high" | "medium" | "low"
"""

# B2 截图 prompt
_PARSE_TX_IMAGE_ZH = """\
你是 BeeCount 记账助手。我会给你一张支付凭证截图(可能是支付宝/微信/信用卡推送/
银行账单截图),你需要提取所有交易记录。

当前时间:{NOW}
账本可用类目:{CATEGORIES}
账本可用账户:{ACCOUNTS}
{CURRENCY_HINT}

{SCHEMA}

要求:
1. 图片如果完全不像支付凭证(比如截了个聊天界面),返回 `{{"tx_drafts": []}}`
2. 多笔识别场景:信用卡账单 / 微信账单列表 / 一个月汇总图 — 提取所有清晰的条目
3. **不要编造**:看不清的字段用 "" 或 null,不要瞎填;金额看不清的整笔跳过

只输出 JSON,不要前后加任何解释文字。
"""

_PARSE_TX_IMAGE_EN = """\
You are BeeCount's bookkeeping assistant. I'll give you a payment receipt screenshot
(could be Alipay/WeChat/credit-card notification/bank statement). Extract every
transaction visible.

Current time: {NOW}
Available categories in this ledger: {CATEGORIES}
Available accounts in this ledger: {ACCOUNTS}
{CURRENCY_HINT}

{SCHEMA}

Rules:
1. If the image is clearly NOT a payment receipt (e.g. chat screenshot), return
   `{{"tx_drafts": []}}`.
2. Multi-tx: credit card statements / bill lists / monthly summary — extract every
   clear entry.
3. **Don't fabricate**: leave fields as "" or null when unclear; if amount is unclear
   skip that whole tx (don't include it).

Output JSON only, no prefix/suffix text.
"""

# B3 文本 prompt
_PARSE_TX_TEXT_ZH = """\
你是 BeeCount 记账助手。我会给你一段记账文本(可能是微信/支付宝/信用卡账单段落,
也可能是用户手写的待入账列表),你需要提取所有交易记录。

当前时间:{NOW}
账本可用类目:{CATEGORIES}
账本可用账户:{ACCOUNTS}
{CURRENCY_HINT}

文本:
{TEXT}

{SCHEMA}

要求:
1. 字段缺失处理:
   - 没明确日期 → 推断("昨天打车" → 昨天日期 + 推断时间)
   - 完全没日期信息 → 用 "{NOW}"
   - 没明确类目 → 从文本推断(美团 → 餐饮,滴滴 → 交通);推不出留 ""
2. 多笔识别:每行一笔 / 用 - * 1. 列表分隔的也是多笔
3. 文本里全是闲聊 / 不是账单 → 返 `{{"tx_drafts": []}}`
4. **不要编造金额**:看不清的金额直接跳过那笔(不要瞎填),宁缺毋滥

只输出 JSON,不要前后加任何解释文字。
"""

_PARSE_TX_TEXT_EN = """\
You are BeeCount's bookkeeping assistant. I'll give you a piece of text (could be a
WeChat/Alipay/credit-card statement excerpt, or a user's hand-written list of pending
transactions). Extract every transaction.

Current time: {NOW}
Available categories in this ledger: {CATEGORIES}
Available accounts in this ledger: {ACCOUNTS}
{CURRENCY_HINT}

Text:
{TEXT}

{SCHEMA}

Rules:
1. Field inference:
   - No explicit date → infer ("dinner yesterday" → yesterday's date + reasonable hour)
   - No date info at all → use "{NOW}"
   - No explicit category → infer (Starbucks → Coffee/Food); leave "" if unclear
2. Multi-tx: each line / list item is one tx
3. Plain chitchat / not a bill → return `{{"tx_drafts": []}}`
4. **Don't fabricate amounts**: skip the whole tx if amount unclear

Output JSON only, no prefix/suffix text.
"""


def _format_categories_hint(categories: list[str]) -> str:
    """把类目列表格式化成 LLM 友好的字符串。空列表返回 "(无,请用户在前端选)"。"""
    if not categories:
        return "(none — leave category_name empty for user to pick)"
    return ", ".join(c for c in categories if c)


def _format_accounts_hint(
    accounts: list[tuple[str, str | None]], ledger_currency: str,
) -> str:
    """账户清单。**只有币种 ≠ 账本主币种的账户才标注币种** —— 单币种用户看到的
    字符串与加多币种支持之前逐字相同(零噪声、零回归)。"""
    if not accounts:
        return "(none — leave account_name empty for user to pick)"
    base = (ledger_currency or "").strip().upper()
    parts: list[str] = []
    for name, ccy in accounts:
        if not name:
            continue
        code = (ccy or "").strip().upper()
        parts.append(f"{name}({code})" if code and code != base else name)
    return ", ".join(parts)


def _format_currency_hint(
    accounts: list[tuple[str, str | None]], ledger_currency: str, *, is_zh: bool,
) -> str:
    """币种提示行。账本内只有一种币种时只报主币种(一行,极短);出现外币账户时
    额外列出可用币种。无论哪种情况都告诉 LLM「也可返回其它 ISO 代码」是多余的
    —— schema 里已经写了,这里只给上下文。"""
    base = (ledger_currency or "CNY").strip().upper()
    others = sorted(
        {(c or "").strip().upper() for _, c in accounts if (c or "").strip()} - {base, ""}
    )
    if is_zh:
        line = f"账本主币种:{base}"
        if others:
            line += f";账本内已有外币账户:{'、'.join(others)}"
        return line
    line = f"Ledger base currency: {base}"
    if others:
        line += f"; foreign-currency accounts present: {', '.join(others)}"
    return line


def build_parse_tx_image_messages(
    *,
    categories: list[str],
    accounts: list[tuple[str, str | None]],
    ledger_currency: str = "CNY",
    now: datetime,
    locale: str = "zh",
    image_data_url: str,
    custom_prompt_template: str | None = None,
) -> list[dict[str, object]]:
    """B2 截图记账 — 拼 OpenAI vision API messages。

    image_data_url: `data:image/jpeg;base64,...` 格式
    accounts: (名称, 币种) 列表;币种 ≠ 账本主币种时会在 prompt 里标注
    custom_prompt_template: 用户自定义 prompt(从 user.ai_config_json 来),为 None 则用 default
        —— 自定义模板不含 `{CURRENCY_HINT}` 时 `.format()` 直接忽略该 kwarg,老模板零影响
    """
    is_zh = (locale or "zh").lower().startswith("zh")
    template = custom_prompt_template or (_PARSE_TX_IMAGE_ZH if is_zh else _PARSE_TX_IMAGE_EN)
    schema = _TX_DRAFTS_SCHEMA_ZH if is_zh else _TX_DRAFTS_SCHEMA_EN
    prompt = template.format(
        NOW=now.isoformat(timespec="seconds"),
        CATEGORIES=_format_categories_hint(categories),
        ACCOUNTS=_format_accounts_hint(accounts, ledger_currency),
        CURRENCY_HINT=_format_currency_hint(accounts, ledger_currency, is_zh=is_zh),
        SCHEMA=schema,
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_url}},
                {"type": "text", "text": prompt},
            ],
        }
    ]


def build_parse_tx_text_messages(
    *,
    text: str,
    categories: list[str],
    accounts: list[tuple[str, str | None]],
    ledger_currency: str = "CNY",
    now: datetime,
    locale: str = "zh",
    custom_prompt_template: str | None = None,
) -> list[dict[str, object]]:
    """B3 文字记账 — 拼 OpenAI chat API messages。参数语义同 [build_parse_tx_image_messages]。"""
    is_zh = (locale or "zh").lower().startswith("zh")
    template = custom_prompt_template or (_PARSE_TX_TEXT_ZH if is_zh else _PARSE_TX_TEXT_EN)
    schema = _TX_DRAFTS_SCHEMA_ZH if is_zh else _TX_DRAFTS_SCHEMA_EN
    prompt = template.format(
        NOW=now.isoformat(timespec="seconds"),
        CATEGORIES=_format_categories_hint(categories),
        ACCOUNTS=_format_accounts_hint(accounts, ledger_currency),
        CURRENCY_HINT=_format_currency_hint(accounts, ledger_currency, is_zh=is_zh),
        TEXT=text,
        SCHEMA=schema,
    )
    return [{"role": "user", "content": prompt}]
