"""apply_mapping —— 拿 ParsedRow + ImportFieldMapping → ImportTransaction list。

每行错误**单独收集**,不抛 — 让 caller 决定怎么展示。execute 阶段如果有错
则触发整体 rollback。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .schema import (
    ImportError,
    ImportFieldMapping,
    ImportTransaction,
    ParsedRow,
    ParseWarning,
)

logger = logging.getLogger(__name__)


# 收支类型标准化
_TYPE_EXPENSE = {"expense", "支出", "消费", "出", "-", "支"}
_TYPE_INCOME = {"income", "收入", "收", "+", "入"}
_TYPE_TRANSFER = {"transfer", "转账", "转出转入", "转"}

# 候选时间格式 —— auto 模式按顺序 try
_DATETIME_CANDIDATES = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
)

_CURRENCY_CHARS = re.compile(r"[¥￥$€£,\s]")
_TAG_SPLIT = re.compile(r"[,，;；、]")


def apply_mapping(
    *,
    rows: Iterable[ParsedRow],
    mapping: ImportFieldMapping,
) -> tuple[list[ImportTransaction], list[ImportError], list[ParseWarning]]:
    """逐行 transform,返回 (txs, errors, additional_warnings)。

    errors 非空 → caller 可拒绝执行(整体回滚契约)。
    warnings 给前端的统计 UI 展示。
    """
    txs: list[ImportTransaction] = []
    errors: list[ImportError] = []
    warnings: list[ParseWarning] = []

    if not mapping.required_complete:
        # 必填字段没全 map,所有行都失败,但只报一条 error 节省体积
        missing = []
        if not mapping.tx_type:
            missing.append("tx_type")
        if not mapping.amount:
            missing.append("amount")
        if not mapping.happened_at:
            missing.append("happened_at")
        if not mapping.category_name:
            missing.append("category_name")
        errors.append(
            ImportError(
                code="PARSE_MAPPING_INCOMPLETE",
                row_number=0,
                message=f"required mapping fields missing: {', '.join(missing)}",
            )
        )
        return txs, errors, warnings

    for row in rows:
        try:
            tx = _transform_row(row, mapping)
            if tx is not None:
                txs.append(tx)
        except _RowError as exc:
            errors.append(
                ImportError(
                    code=exc.code,
                    row_number=row.row_number,
                    message=exc.message,
                    raw_line=row.raw_line,
                    field_name=exc.field_name,
                )
            )

    return txs, errors, warnings


def _transform_row(row: ParsedRow, mapping: ImportFieldMapping) -> ImportTransaction | None:
    cells = row.cells

    # 1. tx_type
    raw_type = (cells.get(mapping.tx_type or "", "") or "").strip()
    tx_type = _parse_tx_type(raw_type, mapping.expense_is_negative, cells, mapping)
    if tx_type is None:
        raise _RowError("PARSE_INVALID_FIELD", "tx_type",
                        f"unrecognized tx_type {raw_type!r}")

    # 2. amount
    raw_amount = (cells.get(mapping.amount or "", "") or "").strip()
    if not raw_amount:
        raise _RowError("PARSE_MISSING_REQUIRED", "amount", "amount is empty")
    amount_dec, sign_implies_expense = _parse_amount(
        raw_amount, mapping.strip_currency_symbols
    )
    if mapping.expense_is_negative and sign_implies_expense:
        tx_type = "expense"
    amount = abs(amount_dec) if mapping.expense_is_negative else amount_dec
    if amount < 0 and not mapping.expense_is_negative:
        # 负值但用户没开 expense_is_negative —— 视为支出 + 取绝对值
        amount = -amount
        if tx_type != "transfer":
            tx_type = "expense"

    # 3. happened_at
    raw_dt = (cells.get(mapping.happened_at or "", "") or "").strip()
    if not raw_dt:
        raise _RowError("PARSE_MISSING_REQUIRED", "happened_at",
                        "happened_at is empty")
    dt = _parse_datetime(raw_dt, mapping.datetime_format)
    if dt is None:
        raise _RowError("PARSE_INVALID_FIELD", "happened_at",
                        f"could not parse datetime {raw_dt!r}")

    # 4. optional 字段
    def opt(col: str | None) -> str | None:
        if not col:
            return None
        v = (cells.get(col, "") or "").strip()
        return v or None

    # 5. tags 多列合并
    tag_names: list[str] = []
    for tag_col in mapping.tags:
        v = (cells.get(tag_col, "") or "").strip()
        if not v:
            continue
        for piece in _TAG_SPLIT.split(v):
            piece = piece.strip()
            if piece and piece not in tag_names:
                tag_names.append(piece)

    # 6. 分类 / 二级分类 → tx.category_name(leaf) + tx.parent_category_name(级 1)
    cat_value = opt(mapping.category_name)         # 一级分类(broad)
    sub_value = opt(mapping.subcategory_name)      # 二级分类(specific)
    if sub_value:
        tx_category_name = sub_value
        tx_parent_name = cat_value  # 一级作为 parent
    else:
        tx_category_name = cat_value
        tx_parent_name = None

    return ImportTransaction(
        tx_type=tx_type,
        amount=amount,
        happened_at=dt,
        note=opt(mapping.note),
        category_name=tx_category_name,
        parent_category_name=tx_parent_name,
        account_name=opt(mapping.account_name),
        from_account_name=opt(mapping.from_account_name),
        to_account_name=opt(mapping.to_account_name),
        tag_names=tag_names,
        source_row_number=row.row_number,
        source_raw_line=row.raw_line,
    )


def _parse_tx_type(
    raw: str,
    expense_is_negative: bool,
    cells: dict[str, str],
    mapping: ImportFieldMapping,
) -> str | None:
    if not raw:
        # 当用户开了 expense_is_negative 时,即使 type 字段空也允许 — 由 amount
        # 符号决定;但这里我们先返默认 expense,后面会按符号修正
        if expense_is_negative:
            return "expense"
        return None
    s = raw.strip().lower()
    if s in _TYPE_EXPENSE:
        return "expense"
    if s in _TYPE_INCOME:
        return "income"
    if s in _TYPE_TRANSFER:
        return "transfer"
    # fallback: 子串匹配
    for kw in ("支出", "expense", "消费"):
        if kw in s:
            return "expense"
    for kw in ("收入", "income"):
        if kw in s:
            return "income"
    for kw in ("转账", "transfer"):
        if kw in s:
            return "transfer"
    return None


def _parse_amount(raw: str, strip_currency: bool) -> tuple[Decimal, bool]:
    """返回 (amount Decimal, 是否本来是负数)。"""
    cleaned = raw.strip()
    is_negative = cleaned.startswith("-") or cleaned.startswith("−")
    if strip_currency:
        cleaned = _CURRENCY_CHARS.sub("", cleaned)
    cleaned = cleaned.replace("−", "-")
    try:
        d = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise _RowError("PARSE_INVALID_FIELD", "amount",
                        f"invalid amount {raw!r}: {exc}")
    return d, is_negative


def _parse_datetime(raw: str, fmt: str | None) -> datetime | None:
    s = raw.strip()
    if not s:
        return None
    if fmt:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    # auto try
    for f in _DATETIME_CANDIDATES:
        try:
            dt = datetime.strptime(s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # 最后兜底:fromisoformat 容忍多种 ISO 变体
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class _RowError(Exception):
    def __init__(self, code: str, field_name: str | None, message: str):
        super().__init__(message)
        self.code = code
        self.field_name = field_name
        self.message = message
