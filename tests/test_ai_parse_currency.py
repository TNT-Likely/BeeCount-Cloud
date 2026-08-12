"""智能记账多币种(.docs/multi-currency-ai)— draft 币种解析 + prompt 上下文单测。

单币种用户的**零回归**是本文件最重要的断言:prompt 字符串与账户清单在
「所有账户都是账本主币种」时必须与加多币种之前逐字相同。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.routers.ai.parse_tx_image import _norm_currency, _normalize_drafts
from src.services.ai.prompts import (
    _format_accounts_hint,
    _format_currency_hint,
    build_parse_tx_text_messages,
)

# ── _norm_currency ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("USD", "USD"),
        ("usd", "USD"),
        ("  jpy  ", "JPY"),
        ("美元", ""),      # server 不做中文别名映射(§3.2:交给 prompt 直出 ISO)
        ("$", "USD"),      # 唯一例外:实测 LLM 常把「45 美元」的 currency 回成 "$"
        (" $ ", "USD"),
        ("¥", ""),         # 真歧义(CNY/JPY 都写裸 ¥)→ 退回账本主币种
        ("US", ""),        # 长度不对
        ("USDD", ""),
        ("U$D", ""),
        ("", ""),
        (None, ""),
        (123, ""),         # 非字符串
    ],
)
def test_norm_currency(raw, expected):
    assert _norm_currency(raw) == expected


# ── _normalize_drafts 白名单放行 ──────────────────────────────────────────


def test_drafts_currency_passthrough_uppercased():
    out = _normalize_drafts({"tx_drafts": [{"amount": 45, "currency": "usd"}]})
    assert out[0]["currency"] == "USD"


def test_drafts_currency_invalid_becomes_empty_not_dropped():
    """非法币种不能让整笔 draft 消失 —— 降级成空串走账本主币种。"""
    out = _normalize_drafts({"tx_drafts": [{"amount": 45, "currency": "美元"}]})
    assert len(out) == 1
    assert out[0]["currency"] == ""
    assert out[0]["amount"] == 45.0


def test_drafts_without_currency_key_defaults_empty():
    """回归锁:老 LLM 输出(无 currency 键)照常工作。"""
    out = _normalize_drafts({"tx_drafts": [{"amount": 10, "type": "income"}]})
    assert out[0]["currency"] == ""
    assert out[0]["type"] == "income"


def test_drafts_currency_does_not_disturb_other_fields():
    out = _normalize_drafts(
        {
            "tx_drafts": [
                {
                    "amount": -30,
                    "type": "expense",
                    "currency": "JPY",
                    "category_name": "餐饮",
                    "account_name": "现金",
                    "note": "拉面",
                    "tags": ["旅行"],
                    "confidence": "high",
                }
            ]
        }
    )
    d = out[0]
    assert (d["amount"], d["type"], d["currency"]) == (30.0, "expense", "JPY")
    assert (d["category_name"], d["account_name"], d["note"]) == ("餐饮", "现金", "拉面")
    assert d["tags"] == ["旅行"] and d["confidence"] == "high"


# ── 账户清单:单币种零噪声 ────────────────────────────────────────────────


def test_accounts_hint_single_currency_has_no_annotation():
    """单币种账本:输出与加多币种之前逐字相同。"""
    hint = _format_accounts_hint([("微信", "CNY"), ("支付宝", "CNY")], "CNY")
    assert hint == "微信, 支付宝"


def test_accounts_hint_annotates_only_foreign():
    hint = _format_accounts_hint([("微信", "CNY"), ("Chase", "USD")], "CNY")
    assert hint == "微信, Chase(USD)"


def test_accounts_hint_handles_missing_currency():
    hint = _format_accounts_hint([("旧账户", None)], "CNY")
    assert hint == "旧账户"


def test_accounts_hint_empty():
    assert "none" in _format_accounts_hint([], "CNY")


# ── 币种提示行 ────────────────────────────────────────────────────────────


def test_currency_hint_single_currency_is_one_short_line():
    assert _format_currency_hint([("微信", "CNY")], "CNY", is_zh=True) == "账本主币种:CNY"


def test_currency_hint_lists_foreign_accounts():
    hint = _format_currency_hint([("微信", "CNY"), ("Chase", "USD")], "CNY", is_zh=True)
    assert "USD" in hint and hint.startswith("账本主币种:CNY")


def test_currency_hint_en():
    hint = _format_currency_hint([("Chase", "USD")], "CNY", is_zh=False)
    assert hint.startswith("Ledger base currency: CNY") and "USD" in hint


# ── 自定义 prompt 兼容(A7/A8)────────────────────────────────────────────


def test_custom_template_without_currency_placeholder_still_works():
    """自定义模板不含 {CURRENCY_HINT} 时,.format() 忽略多余 kwarg,不报错。

    注意与 App 的差别(A8):Cloud 的 `{SCHEMA}` 是**独立注入块**、不在用户可编辑
    的模板正文里,所以自定义模板用户照样拿得到 currency 字段说明 —— 只是少了
    「账本主币种是什么」这行上下文。App 那边模板是整段替换,才需要 A7 的补丁。
    """
    content = build_parse_tx_text_messages(
        text="午饭 35",
        categories=["餐饮"],
        accounts=[("微信", "CNY")],
        ledger_currency="CNY",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
        custom_prompt_template="只提取金额:{TEXT}\n{SCHEMA}",
    )[0]["content"]
    assert "午饭 35" in content
    assert "账本主币种:CNY" not in content   # 上下文行没了
    assert "`currency`" in content            # 但 schema 里的字段说明还在


def test_mcp_create_transaction_exposes_currency_param():
    """回归锁(A9):`write_tools.create_transaction` 一直有 currency 参数,但
    `@mcp.tool()` 的签名没暴露 → 对 LLM 是死参数,永远传不进来。"""
    import inspect

    from src.mcp import server as mcp_server
    from src.mcp.tools import write_tools

    assert "currency" in inspect.signature(write_tools.create_transaction).parameters
    fn = getattr(mcp_server.create_transaction, "fn", mcp_server.create_transaction)
    assert "currency" in inspect.signature(fn).parameters


def test_default_template_includes_currency_hint_and_schema_field():
    msgs = build_parse_tx_text_messages(
        text="在东京吃拉面 1200 日元",
        categories=["餐饮"],
        accounts=[("微信", "CNY")],
        ledger_currency="CNY",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    content = msgs[0]["content"]
    assert "账本主币种:CNY" in content
    assert "`currency`" in content
