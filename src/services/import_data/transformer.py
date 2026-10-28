"""apply_mapping —— 拿 ParsedRow + ImportFieldMapping → ImportTransaction list。

每行错误**单独收集**,不抛 — 让 caller 决定怎么展示。execute 阶段如果有错
则触发整体 rollback。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

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

# 多账户"账户:金额"之间的分隔符,以及键值分隔符
_LEG_SPLIT = re.compile(r"[;；]+")
_LEG_PAIR = re.compile(r"[:：]")


def _to_float_or_none(raw: str) -> Decimal | None:
    """剥离货币符号后解析为 Decimal;失败返回 None。"""
    cleaned = _CURRENCY_CHARS.sub("", raw.strip())
    cleaned = cleaned.replace("−", "-")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _parse_discount(raw: str | None) -> dict[str, Any] | None:
    """优惠列 → discount dict。兼容 JSON 对象 或 纯数字(优惠金额)。无则 None。"""
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(d, dict):
            return None
        amt = d.get("discountAmount")
        if amt is None:
            amt = d.get("value")
        if amt is None:
            return None
        try:
            float(amt)
        except (TypeError, ValueError):
            return None
        return {
            "mode": d.get("mode", "amount"),
            "discountAmount": float(amt),
            "couponName": d.get("couponName"),
            "originalAmount": d.get("originalAmount"),
            "finalAmount": d.get("finalAmount"),
        }
    dec = _to_float_or_none(s)
    if dec is None:
        return None
    return {"mode": "amount", "discountAmount": float(abs(dec))}


def _parse_transfer_fee(raw: str | None) -> dict[str, Any] | None:
    """手续费列 → transfer_fee dict。兼容 JSON 对象 或 纯数字。无则 None。"""
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(d, dict):
            return None
        amt = d.get("amount")
        if amt is None:
            return None
        try:
            float(amt)
        except (TypeError, ValueError):
            return None
        return {"mode": d.get("mode", "amount"), "amount": float(amt)}
    dec = _to_float_or_none(s)
    if dec is None:
        return None
    return {"mode": "amount", "amount": float(abs(dec))}


def _parse_payment_legs(raw: str | None) -> list[dict[str, Any]] | None:
    """多账户列 → payment_legs list。兼容 JSON 数组 或 '账户:金额; 账户:金额'。"""
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if s.startswith("["):
        try:
            arr = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(arr, list) or not arr:
            return None
        legs: list[dict[str, Any]] = []
        for leg in arr:
            if not isinstance(leg, dict):
                continue
            name = leg.get("accountName") or leg.get("account_name") or leg.get("name")
            amt = leg.get("amount")
            if not name or amt is None:
                continue
            try:
                legs.append({"accountName": str(name), "amount": float(amt)})
            except (TypeError, ValueError):
                continue
        return legs or None
    legs = []
    for piece in _LEG_SPLIT.split(s):
        piece = piece.strip()
        if not piece:
            continue
        m = _LEG_PAIR.split(piece, maxsplit=1)
        if len(m) != 2:
            continue
        name = m[0].strip()
        amt = _to_float_or_none(m[1])
        if not name or amt is None or amt <= 0:
            continue
        legs.append({"accountName": name, "amount": float(amt)})
    return legs or None


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
    dt = _parse_datetime(raw_dt, mapping.datetime_format, mapping.tz_offset_minutes)
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

    # v30 多币种:币种列(可选,值须像 ISO code 才采纳,脏值回退 None → 本位币)
    raw_currency = opt(mapping.currency)
    currency_code = (
        raw_currency.upper()
        if raw_currency and re.fullmatch(r"[A-Za-z]{3,8}", raw_currency)
        else None
    )

    # v0021 扩展字段(优惠 / 手续费 / 多账户):按类型归属解析 —— discount /
    # payment_legs 仅 expense 有效,transfer_fee 仅 transfer 有效;其余类型置 None。
    discount = _parse_discount(opt(mapping.discount)) if tx_type == "expense" else None
    transfer_fee = (
        _parse_transfer_fee(opt(mapping.transfer_fee)) if tx_type == "transfer" else None
    )
    payment_legs = (
        _parse_payment_legs(opt(mapping.payment_legs)) if tx_type == "expense" else None
    )

    return ImportTransaction(
        tx_type=tx_type,
        amount=amount,
        happened_at=dt,
        currency_code=currency_code,
        note=opt(mapping.note),
        category_name=tx_category_name,
        parent_category_name=tx_parent_name,
        account_name=opt(mapping.account_name),
        from_account_name=opt(mapping.from_account_name),
        to_account_name=opt(mapping.to_account_name),
        tag_names=tag_names,
        discount=discount,
        transfer_fee=transfer_fee,
        payment_legs=payment_legs,
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


def _localize_naive(dt: datetime, tz_offset_minutes: int | None) -> datetime:
    """把 strptime/fromisoformat 出来的 naive datetime 转成 aware UTC。

    CSV 里的时间是用户【本地墙钟】(无时区信息)。tz_offset_minutes 是客户端传来的
    本地相对 UTC 的分钟偏移(东为正,UTC+8 = 480),据此把墙钟换算成 UTC 存储,
    避免被下游"naive 一律当 UTC"的序列化逻辑整体偏移(issue #314:之前直接
    replace(tzinfo=utc),把 23:16 本地当成 23:16 UTC,客户端 toLocal 后晚 8 小时)。

    - 已带时区(strptime %z / fromisoformat 带偏移)→ 原样保留。
    - tz_offset_minutes 为 None(老客户端未传)→ 退回旧行为:当作 UTC,不破坏兼容。
    """
    if dt.tzinfo is not None:
        return dt
    if tz_offset_minutes is None:
        return dt.replace(tzinfo=timezone.utc)
    local_tz = timezone(timedelta(minutes=tz_offset_minutes))
    return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)


def _parse_datetime(
    raw: str, fmt: str | None, tz_offset_minutes: int | None = None
) -> datetime | None:
    s = raw.strip()
    if not s:
        return None
    if fmt:
        try:
            return _localize_naive(datetime.strptime(s, fmt), tz_offset_minutes)
        except ValueError:
            return None
    # auto try
    for f in _DATETIME_CANDIDATES:
        try:
            return _localize_naive(datetime.strptime(s, f), tz_offset_minutes)
        except ValueError:
            continue
    # 最后兜底:fromisoformat 容忍多种 ISO 变体
    try:
        s2 = s.replace("Z", "+00:00")
        return _localize_naive(datetime.fromisoformat(s2), tz_offset_minutes)
    except ValueError:
        return None


class _RowError(Exception):
    def __init__(self, code: str, field_name: str | None, message: str):
        super().__init__(message)
        self.code = code
        self.field_name = field_name
        self.message = message
