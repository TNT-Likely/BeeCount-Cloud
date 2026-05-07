"""通用 CSV 解析 —— fuzzy match 列名,推断不准时返回 None,留给前端 UI 让
用户手动映射。
"""
from __future__ import annotations

import re

from ..schema import ImportFieldMapping


# 全集 alias 表 —— 合并了原 alipay / wechat / generic 三套规则。
# 注意 tx_type 的 pattern 限制为整列名匹配,否则会吃掉"交易类型"(实为
# wechat 的 category 列)。category_name pattern 则容许子串(因为分类列
# 命名很灵活)。匹配是按 fields 顺序执行,先到先得 + 不重复占用。
_PATTERNS: dict[str, re.Pattern[str]] = {
    # 类型(收支):整列名严格匹配,避免吃 "交易类型"(wechat category)
    "tx_type": re.compile(r"^(类型|type|kind|收[/／]?支|收支)$", re.I),
    # 金额:含 alipay 的"金额(元)"
    "amount": re.compile(r"(金额|amount|amt|价格|price|总金额|发生额|sum|total)", re.I),
    # 时间:含 alipay 的"交易创建时间"/ wechat 的"交易时间"
    "happened_at": re.compile(
        r"(交易时间|交易创建时间|创建时间|发生时间|happened|时间|日期|date|when)",
        re.I,
    ),
    # 一级分类:含 alipay 的"类别"/ wechat 的"交易类型"/ 通用的"分类 / 商品类目"
    # 注意:wechat 的"交易类型"列其实是 type 的细分(如"商品消费"),实际作分类
    # 比 type 更合理 — 跟 mobile 行为对齐
    "category_name": re.compile(
        r"(分类|交易类型|类别|商品类目|主类目|顶级分类|父类|category|cat$)",
        re.I,
    ),
    "subcategory_name": re.compile(r"(二级分类|子分类|子类目|subcategory|sub.?cat)", re.I),
    # 账户:含 alipay 的"收/付款方式"/ wechat 的"支付方式"
    "account_name": re.compile(
        r"(账户|账号|account|支付方式|付款方式|收[/／]?付款方式|来源|出处)",
        re.I,
    ),
    "from_account_name": re.compile(r"(转出|from.?account|source.?account|出账)", re.I),
    "to_account_name": re.compile(r"(转入|to.?account|dest.?account|target.?account|入账)", re.I),
    # 备注:含 alipay 的"商品说明" + wechat 的"商品/商家"
    "note": re.compile(
        r"(商品说明|商品|商家|对方|交易对方|备注|note|description|说明|memo)",
        re.I,
    ),
    "tags": re.compile(r"(标签|tag|label)", re.I),
}


def _match(headers: list[str], pattern: re.Pattern[str], taken: set[str] | None = None) -> str | None:
    """匹配第一个符合且未被占用的列。`taken` 跨字段共享,避免一列被多个字段抢。"""
    for h in headers:
        if taken and h in taken:
            continue
        if pattern.search(h or ""):
            return h
    return None


class GenericParser:
    name = "generic"

    def sniff(self, sample_lower: str) -> bool:
        # generic 是 fallback,sniff 永远 False(让其它 parser 优先)。
        return False

    def find_header_row(self, rows: list[list[str]]) -> int:
        """列数一致性启发:在前 30 行里找到 >=3 列且后续 ≥ 5 行列数一致的行。"""
        if not rows:
            return -1
        max_check = min(30, len(rows))
        for i in range(max_check):
            cand_cols = len(rows[i])
            if cand_cols < 3:
                continue
            check_end = min(i + 10, len(rows))
            consistent = sum(
                1 for j in range(i + 1, check_end) if len(rows[j]) == cand_cols
            )
            if consistent >= 5:
                return i
        return 0  # 兜底从第 0 行

    def suggest_mapping(self, headers: list[str]) -> ImportFieldMapping:
        # 找 header 时跳过空字符串(避免空列名 → 空 string regex 误匹)
        non_empty = [h for h in headers if (h or "").strip()]
        # 按优先级顺序占用列,一列只服务一个字段。tx_type / amount / happened_at
        # 是必填先抢;categorz / account 等放后面。
        taken: set[str] = set()
        def grab(field: str) -> str | None:
            m = _match(non_empty, _PATTERNS[field], taken=taken)
            if m:
                taken.add(m)
            return m

        return ImportFieldMapping(
            tx_type=grab("tx_type"),
            amount=grab("amount"),
            happened_at=grab("happened_at"),
            category_name=grab("category_name"),
            subcategory_name=grab("subcategory_name"),
            account_name=grab("account_name"),
            from_account_name=grab("from_account_name"),
            to_account_name=grab("to_account_name"),
            note=grab("note"),
            tags=[t for t in [grab("tags")] if t],
            datetime_format=None,
            strip_currency_symbols=True,
            expense_is_negative=False,
        )
