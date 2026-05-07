"""compute_stats —— 拿 ImportTransaction list + target ledger snapshot,算出
预览统计:总行数 / 时间跨度 / 金额合计 / 新建 vs 匹配 / dedup 跳过笔数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .schema import (
    ImportError,
    ImportTransaction,
    ParseWarning,
)


@dataclass
class CountByType:
    expense_count: int = 0
    expense_total: Decimal = Decimal("0")
    income_count: int = 0
    income_total: Decimal = Decimal("0")
    transfer_count: int = 0


@dataclass
class EntityDiff:
    new_names: list[str] = field(default_factory=list)
    matched_names: list[str] = field(default_factory=list)


@dataclass
class ImportStats:
    total_rows: int
    time_range_start: datetime | None
    time_range_end: datetime | None
    total_signed_amount: Decimal  # income - expense(transfer 不计入)
    by_type: CountByType
    accounts: EntityDiff
    categories: EntityDiff
    tags: EntityDiff
    skipped_dedup: int  # 按 dedup_strategy = skip_duplicates 时的预测数
    parse_errors: list[ImportError]
    parse_warnings: list[ParseWarning]

    def to_payload(self) -> dict:
        """前端展示用的 dict —— Decimal 转 str 避免 JSON 失真。"""
        return {
            "total_rows": self.total_rows,
            "time_range_start": self.time_range_start.isoformat()
            if self.time_range_start
            else None,
            "time_range_end": self.time_range_end.isoformat()
            if self.time_range_end
            else None,
            "total_signed_amount": str(self.total_signed_amount),
            "by_type": {
                "expense_count": self.by_type.expense_count,
                "expense_total": str(self.by_type.expense_total),
                "income_count": self.by_type.income_count,
                "income_total": str(self.by_type.income_total),
                "transfer_count": self.by_type.transfer_count,
            },
            "accounts": {
                "new_names": self.accounts.new_names,
                "matched_names": self.accounts.matched_names,
            },
            "categories": {
                "new_names": self.categories.new_names,
                "matched_names": self.categories.matched_names,
            },
            "tags": {
                "new_names": self.tags.new_names,
                "matched_names": self.tags.matched_names,
            },
            "skipped_dedup": self.skipped_dedup,
            "parse_errors": [
                {
                    "code": e.code,
                    "row_number": e.row_number,
                    "message": e.message,
                    "field_name": e.field_name,
                }
                for e in self.parse_errors[:50]  # 限长避免炸 payload
            ],
            "parse_errors_total": len(self.parse_errors),
            "parse_warnings": [
                {
                    "code": w.code,
                    "row_number": w.row_number,
                    "message": w.message,
                }
                for w in self.parse_warnings[:50]
            ],
            "parse_warnings_total": len(self.parse_warnings),
        }


def compute_stats(
    *,
    txs: list[ImportTransaction],
    parse_errors: list[ImportError],
    parse_warnings: list[ParseWarning],
    existing_account_names: set[str],
    existing_category_names: set[tuple[str, str]],  # (name, kind)
    existing_tag_names: set[str],
    existing_dedup_keys: set[tuple[str, str, str]],  # (type, amount-str, happened_at-iso)
    extra_tag_names: list[str] | None = None,
) -> ImportStats:
    by_type = CountByType()
    time_min: datetime | None = None
    time_max: datetime | None = None
    total_signed = Decimal("0")

    new_accounts: set[str] = set()
    matched_accounts: set[str] = set()
    new_categories_keyed: set[tuple[str, str]] = set()  # (name, kind)
    matched_categories_keyed: set[tuple[str, str]] = set()
    new_tags: set[str] = set()
    matched_tags: set[str] = set()
    skipped_dedup = 0

    # extra tags(auto-import / 文件名 tag)— stats 上算成"新建"如果不存在
    extras = list(extra_tag_names or [])

    for tx in txs:
        # type buckets
        if tx.tx_type == "expense":
            by_type.expense_count += 1
            by_type.expense_total += tx.amount
            total_signed -= tx.amount
        elif tx.tx_type == "income":
            by_type.income_count += 1
            by_type.income_total += tx.amount
            total_signed += tx.amount
        else:
            by_type.transfer_count += 1
        # time range
        if time_min is None or tx.happened_at < time_min:
            time_min = tx.happened_at
        if time_max is None or tx.happened_at > time_max:
            time_max = tx.happened_at
        # accounts
        for name in (tx.account_name, tx.from_account_name, tx.to_account_name):
            if not name:
                continue
            if name in existing_account_names:
                matched_accounts.add(name)
            else:
                new_accounts.add(name)
        # categories(name + kind 维度)
        if tx.category_name and tx.tx_type != "transfer":
            key = (tx.category_name, tx.tx_type)
            if key in existing_category_names:
                matched_categories_keyed.add(key)
            else:
                new_categories_keyed.add(key)
        if tx.parent_category_name and tx.tx_type != "transfer":
            key = (tx.parent_category_name, tx.tx_type)
            if key in existing_category_names:
                matched_categories_keyed.add(key)
            else:
                new_categories_keyed.add(key)
        # tags
        for tag in tx.tag_names:
            if tag in existing_tag_names:
                matched_tags.add(tag)
            else:
                new_tags.add(tag)
        # dedup 预扫
        dedup_key = (
            tx.tx_type,
            f"{tx.amount}",
            tx.happened_at.isoformat(),
        )
        if dedup_key in existing_dedup_keys:
            skipped_dedup += 1

    # extra tag names(auto-import / file-stamp)
    for tag in extras:
        if tag in existing_tag_names:
            matched_tags.add(tag)
        else:
            new_tags.add(tag)

    return ImportStats(
        total_rows=len(txs),
        time_range_start=time_min,
        time_range_end=time_max,
        total_signed_amount=total_signed,
        by_type=by_type,
        accounts=EntityDiff(
            new_names=sorted(new_accounts),
            matched_names=sorted(matched_accounts),
        ),
        categories=EntityDiff(
            new_names=sorted({n for n, _ in new_categories_keyed}),
            matched_names=sorted({n for n, _ in matched_categories_keyed}),
        ),
        tags=EntityDiff(
            new_names=sorted(new_tags),
            matched_names=sorted(matched_tags),
        ),
        skipped_dedup=skipped_dedup,
        parse_errors=parse_errors,
        parse_warnings=parse_warnings,
    )


def build_existing_sets(snapshot: dict) -> tuple[set[str], set[tuple[str, str]], set[str], set[tuple[str, str, str]]]:
    """从 ledger snapshot 提取 existing names / dedup keys。

    返回 (account_names, category_keys, tag_names, dedup_keys)。
    """
    account_names: set[str] = set()
    for acc in snapshot.get("accounts", []) or []:
        if isinstance(acc, dict):
            n = (acc.get("name") or "").strip()
            if n:
                account_names.add(n)
    category_keys: set[tuple[str, str]] = set()
    for cat in snapshot.get("categories", []) or []:
        if isinstance(cat, dict):
            n = (cat.get("name") or "").strip()
            k = (cat.get("kind") or "").strip()
            if n and k:
                category_keys.add((n, k))
    tag_names: set[str] = set()
    for tag in snapshot.get("tags", []) or []:
        if isinstance(tag, dict):
            n = (tag.get("name") or "").strip()
            if n:
                tag_names.add(n)
    dedup_keys: set[tuple[str, str, str]] = set()
    for tx in snapshot.get("items", []) or []:
        if not isinstance(tx, dict):
            continue
        t = (tx.get("type") or "").strip()
        amt = tx.get("amount")
        when = (tx.get("happenedAt") or "").strip()
        if not t or amt is None or not when:
            continue
        dedup_keys.add((t, f"{amt}", when))
    return account_names, category_keys, tag_names, dedup_keys
