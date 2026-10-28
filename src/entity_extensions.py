"""定期存款 / 归物 / 交易退款(0021/0022)的 payload 归一化、业务校验与读侧派生计算。

这三个实体不走 snapshot(snapshot 只有 items/accounts/categories/tags/budgets),
所以没有 snapshot_mutator 那样的 create/update 对偶函数;本模块承担同职责:

- ``*_to_camel``:web write API 的 snake_case payload → snapshot 事件流的
  camelCase payload(跟 mobile serializer 对齐)。
- ``validate_*``:业务规则校验 + 派生字段补全(到期日按起息日+期限推算)。
  不合法抛 ValueError,上层 write 路径转 400。
- ``time_deposit_derived``:读侧按"今天"现算预期利息/剩余天数/是否到期,
  不落库(避免后台任务刷状态)。

日期口径:业务上是"日"粒度,payload 里统一 ISO8601 字符串(00:00:00+00:00),
projection 落库时 ``_parse_date`` 再归一。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

# web write API(snake_case)→ 事件流 payload(camelCase)。只列真实字段,
# base_change_id / __actor_* 之类的控制字段由调用方先剥掉。
_TIME_DEPOSIT_FIELD_MAP: dict[str, str] = {
    "principal": "principal",
    "currency": "currency",
    "annual_rate": "annualRate",
    "term_months": "termMonths",
    "start_date": "startDate",
    "maturity_date": "maturityDate",
    "status": "status",
    "account_id": "accountId",
    "account_name": "accountName",
    "auto_renew": "autoRenew",
    "interest_mode": "interestMode",
    "note": "note",
    "withdrawn_at": "withdrawnAt",
    "actual_interest": "actualInterest",
    # 到息计划(0028,多笔):数组整体作为一个字段,内部 key 保持 date/amount/note。
    "payout_plans": "payoutPlans",
}

_ITEM_RETURN_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "original_tx_id": "originalTxId",
    "original_amount": "originalAmount",
    "refund_amount": "refundAmount",
    "happened_at": "happenedAt",
    "reason": "reason",
    "status": "status",
    "account_id": "accountId",
    "account_name": "accountName",
    "category_id": "categoryId",
    "category_name": "categoryName",
    "note": "note",
}

# 归物 · 物品持有管理(0029)。跟 _ITEM_RETURN_FIELD_MAP(旧的物品归还/退款
# 流程)是两套东西:这套记录「我持有的物品」——购买、成本摊薄、保修、处置。
_ITEM_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "purchase_date": "purchaseDate",
    "purchase_amount": "purchaseAmount",
    "warranty_until": "warrantyUntil",
    "status": "status",
    "disposal_type": "disposalType",
    "disposal_date": "disposalDate",
    "disposal_amount": "disposalAmount",
    "category_id": "categoryId",
    "category_name": "categoryName",
    "account_id": "accountId",
    "account_name": "accountName",
    "note": "note",
}

_REFUND_FIELD_MAP: dict[str, str] = {
    "original_tx_id": "originalTxId",
    "original_amount": "originalAmount",
    "amount": "amount",
    "currency": "currency",
    "account_id": "accountId",
    "account_name": "accountName",
    "happened_at": "happenedAt",
    "reason": "reason",
    "status": "status",
    "note": "note",
    # 退款分摊(0025):mode + 分别退款腿。
    "refund_mode": "refundMode",
    "refund_legs": "refundLegs",
}

# 账本内自定义成员(0024):不是 LedgerMember(登录用户),只是账本自己维护的
# 人名,用于支出账单的人头分摊。
_MEMBER_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "note": "note",
    "sort_order": "sortOrder",
    # 图标 / 关联记账人(成员档案):展示与统计归属用,不参与金额口径。
    "icon": "icon",
    "linked_user_id": "linkedUserId",
}

_TIME_DEPOSIT_STATUSES = {"holding", "matured", "withdrawn"}
# 成员档案(图标 / 关联记账人)字段长度上限,跟投影列宽一致。
MEMBER_ICON_MAX_LEN = 64
MEMBER_LINKED_USER_MAX_LEN = 36
_ITEM_RETURN_STATUSES = {"pending", "returned", "refunded", "cancelled"}
# 归物(0029):in_use=仍持有(每天还在摊成本);disposed=已处置(二手卖出/报废),
# 处置日之后天数定格,不再累计。
_ITEM_STATUSES = {"in_use", "disposed"}
# 处置方式:sold=二手卖出(有收入,抵减成本);scrapped=报废(无收入)。
_ITEM_DISPOSAL_TYPES = {"sold", "scrapped"}
_REFUND_STATUSES = {"pending", "received", "cancelled"}
_REFUND_MODES = {"unified", "split"}
# 金额容差(浮点累计误差),跟 snapshot_mutator._validate_payment_legs 同款。
_REFUND_LEG_TOLERANCE = 0.011
_INTEREST_MODES = {"simple", "compound"}


def _to_camel(payload: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    """snake_case → camelCase;剥掉控制字段(base_change_id / __actor_*)。"""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key.startswith("__") or key == "base_change_id":
            continue
        mapped = field_map.get(key)
        if mapped is not None:
            out[mapped] = value
    return out


def time_deposit_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    return _to_camel(payload, _TIME_DEPOSIT_FIELD_MAP)


def item_return_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    return _to_camel(payload, _ITEM_RETURN_FIELD_MAP)


def item_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    """归物(0029 物品持有)snake_case → camelCase。"""
    return _to_camel(payload, _ITEM_FIELD_MAP)


def refund_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    return _to_camel(payload, _REFUND_FIELD_MAP)


def member_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    return _to_camel(payload, _MEMBER_FIELD_MAP)


def parse_date_value(raw: Any) -> datetime | None:
    """日期入参 → 当天 00:00 UTC 的 aware datetime。无法解析返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                d = date.fromisoformat(text[:10])
            except ValueError:
                return None
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def parse_datetime_value(raw: Any) -> datetime | None:
    """日期时间入参 → 保留时分秒的 aware datetime;无法解析返回 None。

    与 parse_date_value 的唯一区别:**不把时间清零**(退款需要精确到分钟)。
    吃 'YYYY-MM-DD' / ISO datetime / date / datetime,date-only 时时间补 00:00。
    """

    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                d = date.fromisoformat(text[:10])
            except ValueError:
                return None
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _add_months(dt: datetime, months: int) -> datetime:
    """日期 + N 个月(日对日,月底钳位:1/31 + 1 月 = 2/28)。"""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    # 目标月天数钳位
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    last_day = (next_month - datetime(year, month, 1, tzinfo=timezone.utc)).days
    day = min(dt.day, last_day)
    # 保留时分秒:起息日已支持精确到分钟,到期日 = 起息日 + 期限,时间应跟随起息日。
    return dt.replace(year=year, month=month, day=day)


def _as_float(raw: Any, default: float = 0.0) -> float:
    try:
        if raw is None:
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _normalize_payout_plans(raw: Any) -> list[dict[str, Any]]:
    """到息计划(0028)规范化:None / 非列表 → [];脏项丢弃;按到息日升序。

    每项保留 ``{"date": "YYYY-MM-DD", "amount": float, "note": str | None}``。
    date 不可解析或 amount 非正的行直接丢掉 —— 计划是展示型数据,宁可少一条
    也不存一条解释不了的。缺键(update 未传)由调用方判断,不进这里。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        dt = parse_date_value(item.get("date"))
        if dt is None:
            continue
        amount = _as_float(item.get("amount"))
        if amount <= 0:
            continue
        note = item.get("note")
        out.append(
            {
                "date": dt.date().isoformat(),
                "amount": amount,
                "note": str(note).strip() if note else None,
            }
        )
    out.sort(key=lambda plan: plan["date"])
    return out


def validate_time_deposit(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """定期存款业务校验 + 派生补全。入参/出参都是 camelCase payload。

    规则:
    - principal > 0(create 必填,update 缺键保留现值)
    - annualRate >= 0;termMonths 1..600
    - startDate 必填且可解析;maturityDate 缺省按 startDate + termMonths 推算
    - status ∈ {holding, matured, withdrawn};interestMode ∈ {simple, compound}
    - status='withdrawn' 时 withdrawnAt 缺省补今天
    """
    out = dict(payload)
    principal = _as_float(out.get("principal"))
    if principal <= 0:
        raise ValueError("write validation failed: deposit principal must be positive")
    out["principal"] = principal

    rate = _as_float(out.get("annualRate"))
    if rate < 0:
        raise ValueError("write validation failed: annualRate must not be negative")
    out["annualRate"] = rate

    term_raw = out.get("termMonths")
    try:
        term = int(term_raw) if term_raw is not None else 12
    except (TypeError, ValueError):
        raise ValueError("write validation failed: termMonths must be an integer") from None
    if not 1 <= term <= 600:
        raise ValueError("write validation failed: termMonths must be 1..600")
    out["termMonths"] = term

    # 起息日精确到分钟(与退款同一口径),用 parse_datetime_value 而非清零时间的 parse_date_value。
    start_dt = parse_datetime_value(out.get("startDate"))
    if start_dt is None:
        raise ValueError("write validation failed: startDate is required (YYYY-MM-DD)")
    out["startDate"] = _iso(start_dt)

    # 0031:termMonths 是存款期限的权威来源。create 必带 termMonths;update 只要用户
    # 改了期限就带。因此只要 payload 提供 termMonths,就按 start + termMonths 重算到期日
    # —— 之前仅在 maturityDate 缺失时才推算,而客户端编辑总会把旧 maturityDate 一起发上来,
    # 导致「改期限 → 到期日不动 → 预期利息(读侧按 maturity-start 计算)不变」的 bug。
    if term_raw is not None:
        out["maturityDate"] = _iso(_add_months(start_dt, term))
    else:
        # 极旧客户端不传 termMonths 时,才尊重其显式 maturityDate(缺则推算)。
        maturity_dt = parse_datetime_value(out.get("maturityDate"))
        if maturity_dt is None:
            maturity_dt = _add_months(start_dt, term)
        out["maturityDate"] = _iso(maturity_dt)

    status = str(out.get("status") or "holding").strip().lower()
    if status not in _TIME_DEPOSIT_STATUSES:
        raise ValueError("write validation failed: invalid deposit status")
    out["status"] = status

    interest_mode = str(out.get("interestMode") or "simple").strip().lower()
    if interest_mode not in _INTEREST_MODES:
        raise ValueError("write validation failed: invalid interestMode")
    out["interestMode"] = interest_mode

    if out.get("autoRenew") is None:
        out["autoRenew"] = False
    out["autoRenew"] = bool(out.get("autoRenew"))

    withdrawn_dt = parse_date_value(out.get("withdrawnAt"))
    if status == "withdrawn" and withdrawn_dt is None:
        withdrawn_dt = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    out["withdrawnAt"] = _iso(withdrawn_dt) if withdrawn_dt else None

    actual_interest = out.get("actualInterest")
    out["actualInterest"] = (
        _as_float(actual_interest) if actual_interest is not None else None
    )

    currency = str(out.get("currency") or "CNY").strip().upper() or "CNY"
    out["currency"] = currency[:16]

    # 到息计划(0028,多笔):显式传 [] / null = 清空;缺键(update 未传)不动,
    # 由写路径 merge_with_existing 保留现值。
    if "payoutPlans" in out:
        out["payoutPlans"] = _normalize_payout_plans(out.get("payoutPlans"))

    return out


def validate_item_return(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """归物业务校验。入参/出参都是 camelCase payload。"""
    out = dict(payload)
    name = str(out.get("name") or "").strip()
    if not name:
        raise ValueError("write validation failed: item return name is required")
    out["name"] = name[:255]

    refund = _as_float(out.get("refundAmount"))
    if refund < 0:
        raise ValueError("write validation failed: refundAmount must not be negative")
    out["refundAmount"] = refund

    original_amount = out.get("originalAmount")
    out["originalAmount"] = (
        _as_float(original_amount) if original_amount is not None else None
    )

    happened_dt = parse_date_value(out.get("happenedAt"))
    if happened_dt is None:
        raise ValueError("write validation failed: happenedAt is required (YYYY-MM-DD)")
    out["happenedAt"] = _iso(happened_dt)

    status = str(out.get("status") or "pending").strip().lower()
    if status not in _ITEM_RETURN_STATUSES:
        raise ValueError("write validation failed: invalid item return status")
    out["status"] = status
    return out


def validate_item(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """归物(0029 物品持有)业务校验 + 派生补全。入参/出参都是 camelCase。

    规则:
    - name 必填(create 必填;update 缺键保留现值)
    - purchaseDate 必填(YYYY-MM-DD);purchaseAmount >= 0(赠品可为 0)
    - warrantyUntil 可选 —— 到期只是**过保**,物品仍存活,不碰 status
    - status: in_use | disposed
      · disposed 必须带 disposalType(sold / scrapped,缺则按 scrapped 处理)
      · disposed 缺 disposalDate 时按今天;处置日不得早于购买日
      · in_use 会清掉 disposal_* 三个字段,避免从 disposed 改回来时留残值
    - disposalAmount 仅 sold 有意义(二手卖出收入,抵减成本),>= 0;
      scrapped 一律归零 —— 报废没有回收款。
    """
    out = dict(payload)

    if "name" in out or creating:
        name = str(out.get("name") or "").strip()
        if not name:
            raise ValueError("write validation failed: item name is required")
        out["name"] = name[:255]

    if "purchaseAmount" in out or creating:
        amount = _as_float(out.get("purchaseAmount"))
        if amount < 0:
            raise ValueError("write validation failed: purchaseAmount must not be negative")
        out["purchaseAmount"] = amount

    if "purchaseDate" in out or creating:
        purchase_dt = parse_date_value(out.get("purchaseDate"))
        if purchase_dt is None:
            raise ValueError("write validation failed: purchaseDate is required (YYYY-MM-DD)")
        out["purchaseDate"] = _iso(purchase_dt)

    if "warrantyUntil" in out:
        warranty_dt = parse_date_value(out.get("warrantyUntil"))
        out["warrantyUntil"] = _iso(warranty_dt) if warranty_dt is not None else None

    status_raw = out.get("status")
    if status_raw is not None or creating:
        status = str(status_raw or "in_use").strip().lower()
        if status not in _ITEM_STATUSES:
            raise ValueError("write validation failed: invalid item status")
        out["status"] = status

    if out.get("status") == "disposed" and ("status" in out or "disposalType" in out or creating):
        dtype = str(out.get("disposalType") or "scrapped").strip().lower()
        if dtype not in _ITEM_DISPOSAL_TYPES:
            raise ValueError("write validation failed: invalid disposalType")
        out["disposalType"] = dtype

        disposal_dt = parse_date_value(out.get("disposalDate")) or datetime.now(timezone.utc)
        purchase_dt = parse_date_value(out.get("purchaseDate"))
        if purchase_dt is not None and disposal_dt < purchase_dt:
            raise ValueError(
                "write validation failed: disposalDate must not be earlier than purchaseDate"
            )
        out["disposalDate"] = _iso(disposal_dt)

        if dtype == "sold":
            income = _as_float(out.get("disposalAmount"))
            if income < 0:
                raise ValueError("write validation failed: disposalAmount must not be negative")
            out["disposalAmount"] = income
        else:
            # 报废无回收款。
            out["disposalAmount"] = 0.0
    elif out.get("status") == "in_use" and "status" in out:
        out["disposalType"] = None
        out["disposalDate"] = None
        out["disposalAmount"] = None
    return out


def validate_refund(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """交易退款业务校验(纯函数部分)。入参/出参都是 camelCase payload。

    覆盖的规则:
    - originalTxId 必填(退款必须挂靠原始支出交易)
    - amount > 0(不允许 0 金额退款)
    - happenedAt 必填且可解析
    - status ∈ {pending, received, cancelled}
    - currency 缺省 CNY;originalAmount 可空(原交易已删时的历史快照)

    「原单存在且是支出」「同单累计不超原单」两条需要查库,由 web write 路径的
    _make_refund_validator(routers/write/refunds.py)在调用本函数前后补上。
    """
    out = dict(payload)

    original_tx_id = str(out.get("originalTxId") or "").strip()
    if not original_tx_id:
        raise ValueError("write validation failed: originalTxId is required")
    out["originalTxId"] = original_tx_id[:255]

    amount = _as_float(out.get("amount"))
    if amount <= 0:
        raise ValueError("write validation failed: refund amount must be positive")
    out["amount"] = amount

    original_amount = out.get("originalAmount")
    out["originalAmount"] = (
        _as_float(original_amount) if original_amount is not None else None
    )

    happened_dt = parse_datetime_value(out.get("happenedAt"))
    if happened_dt is None:
        raise ValueError("write validation failed: happenedAt is required (ISO datetime)")
    out["happenedAt"] = _iso(happened_dt)

    status = str(out.get("status") or "pending").strip().lower()
    if status not in _REFUND_STATUSES:
        raise ValueError("write validation failed: invalid refund status")
    out["status"] = status

    currency = str(out.get("currency") or "CNY").strip().upper() or "CNY"
    out["currency"] = currency[:16]

    # 退款分摊模式(0025):'unified'(默认) | 'split'(分别退款,按腿拆分到多卡)。
    refund_mode = str(out.get("refundMode") or "unified").strip().lower()
    if refund_mode not in _REFUND_MODES:
        refund_mode = "unified"
    out["refundMode"] = refund_mode

    raw_legs = out.get("refundLegs")
    if refund_mode == "split":
        if not isinstance(raw_legs, list) or not raw_legs:
            raise ValueError(
                "write validation failed: split refund requires non-empty refundLegs"
            )
        parsed_legs: list[dict[str, Any]] = []
        leg_total = 0.0
        for leg in raw_legs:
            if not isinstance(leg, dict):
                raise ValueError("write validation failed: invalid refund leg")
            account_id = str(leg.get("accountId") or "").strip()
            if not account_id:
                raise ValueError(
                    "write validation failed: refund leg accountId is required"
                )
            leg_amount = _as_float(leg.get("amount"))
            if leg_amount <= 0:
                raise ValueError(
                    "write validation failed: refund leg amount must be positive"
                )
            leg_name = str(leg.get("accountName") or "").strip() or None
            parsed_legs.append(
                {
                    "accountId": account_id[:255],
                    "accountName": (leg_name[:2000] if leg_name else None),
                    "amount": leg_amount,
                }
            )
            leg_total += leg_amount
        if abs(leg_total - amount) > _REFUND_LEG_TOLERANCE:
            raise ValueError(
                "write validation failed: sum(refundLegs) "
                f"{leg_total:.2f} must equal refund amount {amount:.2f}"
            )
        out["refundLegs"] = parsed_legs
    else:
        # unified 模式:忽略腿,确保干净(退到顶层 accountId)。
        out["refundLegs"] = None
    return out


# --------------------------------------------------------------------------- #
# 账本内自定义成员(0024)                                                      #
# --------------------------------------------------------------------------- #

def validate_member(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """账本内自定义成员校验。

    只管纯字段规则(名字必填、长度、排序值);重名 / 存在性校验要查库,
    放在 write 路由的闭包里做(同 refunds 的分层)。
    """
    out = dict(payload)

    name = str(out.get("name") or "").strip()
    if not name:
        raise ValueError("write validation failed: member name is required")
    # 名字是 UI 上直接展示的短文本,截断到 64 足够(投影列是 Text)。
    out["name"] = name[:64]

    note = out.get("note")
    out["note"] = str(note).strip()[:2000] if note is not None else None

    raw_order = out.get("sortOrder")
    order = _as_int(raw_order) if raw_order is not None else 0
    out["sortOrder"] = order

    # 图标 key / 关联记账人:都是「空串等于没设」的可选短文本,统一归一成
    # None(投影列可空),避免前端传 "" 落成一个空字符串列值。
    out["icon"] = _clean_optional_str(out.get("icon"), MEMBER_ICON_MAX_LEN)
    out["linkedUserId"] = _clean_optional_str(
        out.get("linkedUserId"), MEMBER_LINKED_USER_MAX_LEN
    )
    return out


def _clean_optional_str(raw: Any, limit: int) -> str | None:
    """可选短文本归一:None / 空串 → None(表示"未设置"),否则截断到 limit。"""
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:limit] or None


def _as_int(raw: Any, default: int = 0) -> int:
    """宽松转 int(前端可能传字符串 / float),失败回落 default。"""
    try:
        if isinstance(raw, bool):
            return default
        return int(float(raw))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 借款 / 报销(0023)                                                            #
# --------------------------------------------------------------------------- #

_LOAN_FIELD_MAP: dict[str, str] = {
    "direction": "direction",
    "counterparty": "counterparty",
    "principal": "principal",
    "currency": "currency",
    "interest_rate": "interestRate",
    "started_at": "startedAt",
    "due_at": "dueAt",
    "repaid_amount": "repaidAmount",
    "status": "status",
    "account_id": "accountId",
    "account_name": "accountName",
    "note": "note",
}

_REIMBURSEMENT_FIELD_MAP: dict[str, str] = {
    "title": "title",
    "payer": "payer",
    "reimburser": "reimburser",
    "amount": "amount",
    "currency": "currency",
    "category_id": "categoryId",
    "category_name": "categoryName",
    "happened_at": "happenedAt",
    "status": "status",
    "account_id": "accountId",
    "account_name": "accountName",
    "related_tx_id": "relatedTxId",
    "note": "note",
}

_LOAN_DIRECTIONS = {"borrowed", "lent"}
_LOAN_STATUSES = {"active", "repaid", "cancelled"}
_REIMBURSEMENT_STATUSES = {"pending", "reimbursed", "cancelled"}


def loan_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    """loan 的 snake→camel 映射(web write API → 事件流 payload)。

    仅重命名键,不补 date/派生字段 —— 那些由 validate_loan 处理。
    日期列统一在 validate_loan 转成 ISO 串(当天 00:00 UTC),跟其它实体一致。
    """
    return _to_camel(payload, _LOAN_FIELD_MAP)


def reimbursement_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    return _to_camel(payload, _REIMBURSEMENT_FIELD_MAP)


def validate_loan(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """借款业务校验(纯函数部分)。入参/出参都是 camelCase payload。

    覆盖:
    - direction ∈ {borrowed, lent}(缺省 borrowed)
    - counterparty 必填(对方名称)
    - principal > 0
    - startedAt 必填且可解析
    - dueAt 可空;若提供必须晚于/等于 startedAt
    - repaid_amount >= 0
    - status ∈ {active, repaid, cancelled}
    - currency 缺省 CNY

    「已还金额 > 本金」之类需要查库的强一致校验不在此做(web 写路径按需补)。
    """
    out = dict(payload)

    direction = str(out.get("direction") or "borrowed").strip().lower()
    if direction not in _LOAN_DIRECTIONS:
        direction = "borrowed"
    out["direction"] = direction

    counterparty = str(out.get("counterparty") or "").strip()
    if not counterparty:
        raise ValueError("write validation failed: counterparty is required")
    out["counterparty"] = counterparty[:500]

    principal = _as_float(out.get("principal"))
    if principal <= 0:
        raise ValueError("write validation failed: principal must be positive")
    out["principal"] = principal

    interest_rate = _as_float(out.get("interestRate"))
    if interest_rate < 0:
        interest_rate = 0.0
    out["interestRate"] = interest_rate

    started_dt = parse_date_value(out.get("startedAt"))
    if started_dt is None:
        raise ValueError("write validation failed: startedAt is required (YYYY-MM-DD)")
    out["startedAt"] = _iso(started_dt)

    due_raw = out.get("dueAt")
    due_dt = parse_date_value(due_raw) if due_raw else None
    if due_dt is not None and due_dt < started_dt:
        raise ValueError("write validation failed: dueAt must not be before startedAt")
    out["dueAt"] = _iso(due_dt) if due_dt is not None else None

    repaid = _as_float(out.get("repaidAmount"))
    if repaid < 0:
        repaid = 0.0
    out["repaidAmount"] = repaid

    status = str(out.get("status") or "active").strip().lower()
    if status not in _LOAN_STATUSES:
        raise ValueError("write validation failed: invalid loan status")
    out["status"] = status

    currency = str(out.get("currency") or "CNY").strip().upper() or "CNY"
    out["currency"] = currency[:16]

    # 登记还款(0032):明细列表(若存在)。只做类型清洗,业务强一致校验(金额上限 /
    # 重复 id)在还款写端点做。这里确保它始终是 list[dict],不会污染其它字段。
    repayments = payload.get("repayments")
    if not isinstance(repayments, list):
        repayments = []
    out["repayments"] = [r for r in repayments if isinstance(r, dict)]
    return out


def validate_reimbursement(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """报销业务校验(纯函数部分)。入参/出参都是 camelCase payload。

    覆盖:
    - title 必填(报销事由)
    - amount > 0
    - happenedAt 必填且可解析
    - status ∈ {pending, reimbursed, cancelled}
    - currency 缺省 CNY;payer / reimburser / relatedTxId 可空
    """
    out = dict(payload)

    title = str(out.get("title") or "").strip()
    if not title:
        raise ValueError("write validation failed: title is required")
    out["title"] = title[:500]

    amount = _as_float(out.get("amount"))
    if amount <= 0:
        raise ValueError("write validation failed: amount must be positive")
    out["amount"] = amount

    happened_dt = parse_date_value(out.get("happenedAt"))
    if happened_dt is None:
        raise ValueError("write validation failed: happenedAt is required (YYYY-MM-DD)")
    out["happenedAt"] = _iso(happened_dt)

    status = str(out.get("status") or "pending").strip().lower()
    if status not in _REIMBURSEMENT_STATUSES:
        raise ValueError("write validation failed: invalid reimbursement status")
    out["status"] = status

    currency = str(out.get("currency") or "CNY").strip().upper() or "CNY"
    out["currency"] = currency[:16]

    related = out.get("relatedTxId")
    out["relatedTxId"] = str(related).strip()[:255] if related else None
    return out


def loan_derived(
    *,
    principal: float,
    repaid_amount: float,
    due_at: datetime | None,
    status: str,
    today: datetime | None = None,
) -> dict[str, Any]:
    """读侧派生:还款进度 / 距到期天数 / 是否逾期。不落库,按今天现算。"""
    now = today or datetime.now(timezone.utc)
    due = due_at if (due_at is None or due_at.tzinfo) else due_at.replace(tzinfo=timezone.utc)
    days_to_due = (due - now).days if due is not None else None
    is_overdue = (
        due is not None
        and status == "active"
        and now > due
    )
    princ = principal if principal > 0 else 0.0
    ratio = min(repaid_amount / princ, 1.0) if princ > 0 else 0.0
    return {
        "repay_progress": round(ratio, 4),
        "days_to_due": days_to_due,
        "is_overdue": bool(is_overdue),
    }


def time_deposit_derived(
    *,
    principal: float,
    annual_rate: float,
    start_date: datetime,
    maturity_date: datetime,
    status: str,
    actual_interest: float | None,
    interest_mode: str = "simple",
    today: datetime | None = None,
) -> dict[str, Any]:
    """读侧派生:预期利息 / 距到期天数 / 是否到期。

    - withdrawn:expected_interest = actual_interest(实得,缺省 0),
      days_remaining = 0,is_matured = True
    - 存期内/已到期未支取:按利率现算
      simple  = principal * rate * days / 365
      compound = principal * ((1+rate) ** (days/365) - 1)
    - days_remaining 负数 = 已过期未支取
    """
    now = today or datetime.now(timezone.utc)
    start = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
    maturity = (
        maturity_date if maturity_date.tzinfo
        else maturity_date.replace(tzinfo=timezone.utc)
    )
    is_matured = status == "withdrawn" or now >= maturity
    days_remaining = 0 if status == "withdrawn" else (maturity - now).days
    if status == "withdrawn":
        expected = _as_float(actual_interest)
    else:
        term_days = max((maturity - start).days, 0)
        if interest_mode == "compound" and annual_rate > 0:
            expected = principal * ((1.0 + annual_rate) ** (term_days / 365.0) - 1.0)
        else:
            expected = principal * annual_rate * term_days / 365.0
    return {
        "expected_interest": round(expected, 2),
        "days_remaining": days_remaining,
        "is_matured": bool(is_matured),
    }


def time_deposit_payout_plans_derived(
    plans: list[dict[str, Any]] | None,
    *,
    today: datetime | None = None,
) -> dict[str, Any]:
    """到息计划(0028)派生:逐条 is_due + 金额合计 + 下次到息日。

    - ``is_due``:到息日 <= 今天(只比日期,不看时刻)—— 与 is_matured 同为
      按「今天」派生,不落库,避免后台任务刷状态;
    - ``total``:计划金额合计(展示用;计划不参与余额与收支统计);
    - ``next_payout_date``:最早的一条尚未到息的日期;全部已到息/无计划 → None。

    坏数据(非 dict / 空 date / 非正数金额)整条跳过,不让它污染展示。
    """
    today_iso = (today or datetime.now(timezone.utc)).date().isoformat()
    items: list[dict[str, Any]] = []
    total = 0.0
    next_date: str | None = None
    for plan in plans or []:
        if not isinstance(plan, dict):
            continue
        date_str = str(plan.get("date") or "").strip()
        if not date_str:
            continue
        amount = _as_float(plan.get("amount"))
        if amount <= 0:
            continue
        is_due = date_str <= today_iso
        note = plan.get("note")
        items.append(
            {
                "date": date_str,
                "amount": amount,
                "note": str(note) if note else None,
                "is_due": is_due,
            }
        )
        total += amount
        if not is_due and (next_date is None or date_str < next_date):
            next_date = date_str
    return {
        "plans": items,
        "total": round(total, 2),
        "next_payout_date": next_date,
    }


def item_derived(
    *,
    purchase_date: datetime,
    purchase_amount: float,
    warranty_until: datetime | None,
    status: str,
    disposal_type: str | None,
    disposal_date: datetime | None,
    disposal_amount: float | None,
    linked_expense: float = 0.0,
    linked_income: float = 0.0,
    today: datetime | None = None,
) -> dict[str, Any]:
    """归物(0029)读侧派生:持有天数 / 净成本 / 日均成本 / 保修状态。

    口径:
    - ``held_days`` 持有天数**含头**(买当天算第 1 天),最小 1 —— 今天买的东西
      持有 1 天而不是 0 天,否则日均成本会除零。
      · in_use:今天 - 购买日 + 1(随时间增长)
      · disposed:**定格**在处置日(in_use→disposed 那天之后天数不再涨)
    - ``total_cost`` 净成本 = 购买金额 + 关联交易支出 - 关联交易收入
      - 二手卖出收入。可以为负(卖出价高于总投入 = 这东西没花钱还赚了),
      如实反映不钳 0。关联交易收入来自挂到本物的**收入**交易(如回收款、
      折价补偿、保修理赔入账等),同样抵减成本。
    - ``daily_cost`` = 净成本 / 持有天数。
    - ``warranty_status``: 'none'(没记保修) | 'active'(保修中)
      | 'expired'(已过保)。**过保不等于处置** —— 过保后物品照样存活、
      照样摊成本,这正是「到期自动过保但仍存活」的语义。
    - ``warranty_days_remaining``:保修剩余天数(expired 时是负的过期天数)。
    """
    now = today or datetime.now(timezone.utc)
    start = purchase_date if purchase_date.tzinfo else purchase_date.replace(tzinfo=timezone.utc)

    is_disposed = status == "disposed"
    if is_disposed:
        end = disposal_date or now
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    else:
        end = now
    held_days = max((end.date() - start.date()).days + 1, 1)

    linked = _as_float(linked_expense)
    linked_income = _as_float(linked_income)
    sold_income = _as_float(disposal_amount) if disposal_type == "sold" else 0.0
    total_cost = _as_float(purchase_amount) + linked - linked_income - sold_income
    daily_cost = total_cost / held_days if held_days > 0 else total_cost

    if warranty_until is None:
        warranty_status = "none"
        warranty_days_remaining: int | None = None
    else:
        warranty = (
            warranty_until if warranty_until.tzinfo
            else warranty_until.replace(tzinfo=timezone.utc)
        )
        warranty_days_remaining = (warranty.date() - now.date()).days
        warranty_status = "active" if warranty_days_remaining >= 0 else "expired"

    return {
        "held_days": held_days,
        "total_cost": round(total_cost, 2),
        "daily_cost": round(daily_cost, 2),
        "linked_expense": round(linked, 2),
        "linked_income": round(linked_income, 2),
        "warranty_status": warranty_status,
        "warranty_days_remaining": warranty_days_remaining,
        "is_disposed": bool(is_disposed),
    }
