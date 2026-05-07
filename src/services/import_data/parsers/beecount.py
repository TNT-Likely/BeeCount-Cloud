"""BeeCount 自家格式解析 —— 跟 web 导出 / mobile 导出严格对齐。

格式见 .docs/web-csv-export-design.md §3 字段表。11 列本地化表头:

    type,category,subcategory,amount,account,from_account,to_account,note,time,tags,attachments

英文 / 中文 / 繁中 表头都识别。Type 列也按语言本地化(收入/支出/转账)。
"""
from __future__ import annotations

import re

from ..schema import ImportFieldMapping


# 表头同义词(按语言)— 跟 web export `_CSV_HEADERS_BY_LANG` 对齐
_HEADER_ALIASES = {
    "tx_type": {"type", "类型", "類型"},
    "category": {"category", "分类", "分類"},
    "subcategory": {"subcategory", "子分类", "子分類"},
    "amount": {"amount", "金额", "金額"},
    "account": {"account", "账户", "帳戶"},
    "from_account": {"from_account", "from account", "转出账户", "轉出帳戶"},
    "to_account": {"to_account", "to account", "转入账户", "轉入帳戶"},
    "note": {"note", "备注", "備註"},
    "time": {"time", "时间", "時間"},
    "tags": {"tags", "标签", "標籤"},
    "attachments": {"attachments", "附件"},
}


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _column_for(headers: list[str], aliases: set[str]) -> str | None:
    aliases_lower = {a.lower() for a in aliases}
    for h in headers:
        if _normalize(h) in aliases_lower:
            return h
    return None


class BeeCountParser:
    name = "beecount"

    def sniff(self, sample_lower: str) -> bool:
        """判断是否为 BeeCount 导出格式 —— 11 列里至少能识别 8 个。
        sample_lower 已经 .lower()。"""
        score = 0
        for aliases in _HEADER_ALIASES.values():
            for a in aliases:
                if a.lower() in sample_lower:
                    score += 1
                    break
        # 同时要求文件有"beecount-" 文件名暗示(可选)或 score >= 8 — 8 是
        # 11 列里大多数都能识别,可信度足够
        return score >= 8

    def find_header_row(self, rows: list[list[str]]) -> int:
        """BeeCount 导出 header 一定在第 0 行(没有顶部说明文字)。"""
        if not rows:
            return -1
        return 0

    def suggest_mapping(self, headers: list[str]) -> ImportFieldMapping:
        type_col = _column_for(headers, _HEADER_ALIASES["tx_type"])
        amount_col = _column_for(headers, _HEADER_ALIASES["amount"])
        time_col = _column_for(headers, _HEADER_ALIASES["time"])
        # 分类列 → mapping.category_name(一级分类 / 顶级 bucket);子分类列 →
        # mapping.subcategory_name(二级分类 / 具体 leaf)。transformer 在 §schema
        # 注释里说明合成规则。
        cat_col = _column_for(headers, _HEADER_ALIASES["category"])
        sub_col = _column_for(headers, _HEADER_ALIASES["subcategory"])
        return ImportFieldMapping(
            tx_type=type_col,
            amount=amount_col,
            happened_at=time_col,
            category_name=cat_col,
            subcategory_name=sub_col,
            account_name=_column_for(headers, _HEADER_ALIASES["account"]),
            from_account_name=_column_for(headers, _HEADER_ALIASES["from_account"]),
            to_account_name=_column_for(headers, _HEADER_ALIASES["to_account"]),
            note=_column_for(headers, _HEADER_ALIASES["note"]),
            tags=[t for t in [_column_for(headers, _HEADER_ALIASES["tags"])] if t],
            datetime_format=None,  # BeeCount 是 ISO-ish 格式,auto-try OK
            strip_currency_symbols=False,  # BeeCount 导出不含币种符号
            expense_is_negative=False,
        )
