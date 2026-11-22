"""联系人 / 成员共用的「关联人资料」(Party Profile) 归一化与编解码。

一份资料是一个 JSON 对象，存进 ``contacts.profile_json`` /
``user_member_projection.profile_json``，结构统一::

    {
      "avatarFileId": "<attachment file id> | null",
      "contactMethods": [
        {"type": "phone|wechat|alipay|email|qq|other", "value": "...", "label": "..."}
      ],
      "bankCards": [
        {"bank": "...", "cardNo": "...", "holder": "...", "note": "..."}
      ]
    }

用 camelCase 是为了直接塞进 mobile-friendly 的 snapshot item，写入引擎不必
再做字段转换。旧的 ``phone / wechat / alipay / avatar_file_id`` 单值列继续
保留：写入时从 profile 同步首个值，读取时 profile 为空则用旧值合成，保证
历史数据与老客户端都不丢信息。
"""
from __future__ import annotations

import json
from typing import Any

CONTACT_METHOD_TYPES = frozenset(
    {"phone", "wechat", "alipay", "email", "qq", "other"}
)
MAX_CONTACT_METHODS = 30
MAX_BANK_CARDS = 30
_MAX_VALUE_LEN = 256
_MAX_SHORT_LEN = 128


def _clean(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def normalize_profile(value: Any) -> dict[str, Any] | None:
    """校验并清洗传入的 profile；全空时返回 None（不占存储）。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None

    avatar = _clean(value.get("avatarFileId"), _MAX_SHORT_LEN) or None

    methods: list[dict[str, str]] = []
    raw_methods = value.get("contactMethods")
    if isinstance(raw_methods, list):
        for row in raw_methods[:MAX_CONTACT_METHODS]:
            if not isinstance(row, dict):
                continue
            val = _clean(row.get("value"), _MAX_VALUE_LEN)
            if not val:
                continue
            mtype = _clean(row.get("type"), 32).lower() or "other"
            if mtype not in CONTACT_METHOD_TYPES:
                mtype = "other"
            methods.append({
                "type": mtype,
                "value": val,
                "label": _clean(row.get("label"), _MAX_SHORT_LEN),
            })

    cards: list[dict[str, str]] = []
    raw_cards = value.get("bankCards")
    if isinstance(raw_cards, list):
        for row in raw_cards[:MAX_BANK_CARDS]:
            if not isinstance(row, dict):
                continue
            item = {
                "bank": _clean(row.get("bank"), _MAX_SHORT_LEN),
                "cardNo": _clean(row.get("cardNo"), _MAX_VALUE_LEN),
                "holder": _clean(row.get("holder"), _MAX_SHORT_LEN),
                "note": _clean(row.get("note"), _MAX_SHORT_LEN),
            }
            if not any(item.values()):
                continue
            cards.append(item)

    if not avatar and not methods and not cards:
        return None
    return {"avatarFileId": avatar, "contactMethods": methods, "bankCards": cards}


def encode_profile(value: Any) -> str | None:
    profile = normalize_profile(value)
    if profile is None:
        return None
    return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))


def decode_profile(raw: Any) -> dict[str, Any] | None:
    """DB JSON 文本 / 已解析对象 → profile dict；脏数据返回 None。"""
    if not raw:
        return None
    if isinstance(raw, dict):
        return normalize_profile(raw)
    try:
        return normalize_profile(json.loads(raw))
    except (TypeError, ValueError):
        return None


def profile_with_legacy(
    raw: Any,
    *,
    phone: str | None = None,
    wechat: str | None = None,
    alipay: str | None = None,
    avatar_file_id: str | None = None,
) -> dict[str, Any] | None:
    """读取视图：profile_json 为空时用旧单值列合成一份资料。"""
    profile = decode_profile(raw)
    if profile is not None:
        return profile
    methods: list[dict[str, str]] = []
    for mtype, value in (("phone", phone), ("wechat", wechat), ("alipay", alipay)):
        if value and value.strip():
            methods.append({"type": mtype, "value": value.strip(), "label": ""})
    avatar = (avatar_file_id or "").strip() or None
    if not avatar and not methods:
        return None
    return {"avatarFileId": avatar, "contactMethods": methods, "bankCards": []}


def legacy_values(profile: dict[str, Any] | None) -> dict[str, str | None]:
    """写入时把 profile 首个同类型值同步回旧单值列（兼容旧客户端 / 搜索）。"""
    out: dict[str, str | None] = {
        "phone": None,
        "wechat": None,
        "alipay": None,
        "avatar_file_id": None,
    }
    if not profile:
        return out
    out["avatar_file_id"] = profile.get("avatarFileId") or None
    for row in profile.get("contactMethods") or []:
        mtype = row.get("type")
        if mtype in ("phone", "wechat", "alipay") and out[mtype] is None:
            out[mtype] = row.get("value") or None
    return out
