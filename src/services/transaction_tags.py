"""共享账本交易标签校验。

标签是 user-global 实体；共享账本内只有 Owner 的标签能作为交易标签。这个
模块把 Mobile sync 的 camelCase payload 和 Web write 的 snake_case payload
收敛到同一条规则：

- tag id 必须存在于账本 Owner 的 ``UserTagProjection``；
- 只有标签名时，只允许解析已有 Owner 标签，不能隐式创建 Editor 标签；
- id 与 name 同时存在时，以 Owner 标签 id 为准并回填规范名称。

个人账本保持旧协议行为，不在这里做限制。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ledger, LedgerMember, UserTagProjection


class SharedTransactionTagError(ValueError):
    """共享交易引用了非 Owner 标签。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        unknown_tag_ids: list[str] | None = None,
        unknown_tag_names: list[str] | None = None,
        ambiguous_tag_names: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.unknown_tag_ids = unknown_tag_ids or []
        self.unknown_tag_names = unknown_tag_names or []
        self.ambiguous_tag_names = ambiguous_tag_names or []

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "error_code": self.code,
            "message": self.message,
        }
        if self.unknown_tag_ids:
            detail["unknown_tag_ids"] = self.unknown_tag_ids
        if self.unknown_tag_names:
            detail["unknown_tag_names"] = self.unknown_tag_names
        if self.ambiguous_tag_names:
            detail["ambiguous_tag_names"] = self.ambiguous_tag_names
        return detail


def is_shared_ledger(db: Session, ledger: Ledger) -> bool:
    """账本存在 Owner 之外的成员时视为共享账本。"""
    member = db.scalar(
        select(LedgerMember.user_id)
        .where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id != ledger.user_id,
        )
        .limit(1)
    )
    return member is not None


def normalize_shared_transaction_tags(
    db: Session,
    *,
    ledger: Ledger,
    payload: dict[str, Any],
    tag_ids_key: str,
    tags_key: str = "tags",
    tags_as_list: bool,
    shared_ledger: bool | None = None,
    owner_tag_rows: list[UserTagProjection] | None = None,
) -> None:
    """原地校验并规范化共享交易 payload 的标签字段。

    ``tag_ids_key`` 用于兼容 Mobile 的 ``tagIds`` 与 Web 的 ``tag_ids``。
    ``tags_as_list`` 决定规范名称写回 list(Web mutator)还是 CSV(Mobile sync)。
    未共享的账本、以及不包含任何标签字段的 partial update，直接保持原样。
    """
    tags_present = tags_key in payload
    ids_present = tag_ids_key in payload
    if not tags_present and not ids_present:
        return
    if shared_ledger is None:
        shared_ledger = is_shared_ledger(db, ledger)
    if not shared_ledger:
        return

    names = _normalize_names(payload.get(tags_key))
    tag_ids = _normalize_ids(payload.get(tag_ids_key))

    if not names and not tag_ids:
        # 显式清空时把两列一起清掉，避免只清 name 或只清 id 后残留半套引用。
        payload[tags_key] = [] if tags_as_list else ""
        payload[tag_ids_key] = []
        return

    if tag_ids:
        rows = owner_tag_rows
        if rows is None:
            rows = list(
                db.scalars(
                    select(UserTagProjection).where(
                        UserTagProjection.user_id == ledger.user_id,
                        UserTagProjection.sync_id.in_(tag_ids),
                    )
                ).all()
            )
        by_id = {row.sync_id: row for row in rows}
        unknown_ids = [tag_id for tag_id in tag_ids if tag_id not in by_id]
        if unknown_ids:
            raise SharedTransactionTagError(
                "SHARED_TX_TAG_NOT_OWNER",
                "Shared transaction tags must belong to the ledger owner",
                unknown_tag_ids=unknown_ids,
            )
        canonical_names = [
            str(by_id[tag_id].name or "").strip() for tag_id in tag_ids
        ]
        if any(not name for name in canonical_names):
            invalid_ids = [
                tag_id
                for tag_id, name in zip(tag_ids, canonical_names, strict=True)
                if not name
            ]
            raise SharedTransactionTagError(
                "SHARED_TX_TAG_NOT_OWNER",
                "Shared transaction tags must reference named owner tags",
                unknown_tag_ids=invalid_ids,
            )
    else:
        by_name, unknown_names = resolve_owner_transaction_tag_names(
            db,
            ledger=ledger,
            names=names,
            owner_tag_rows=owner_tag_rows,
        )
        if unknown_names:
            raise SharedTransactionTagError(
                "SHARED_TX_TAG_NOT_OWNER",
                "Unknown shared transaction tags must be created by the ledger owner first",
                unknown_tag_names=unknown_names,
            )
        tag_ids = [by_name[name].sync_id for name in names]
        canonical_names = [str(by_name[name].name).strip() for name in names]

    payload[tag_ids_key] = tag_ids
    payload[tags_key] = canonical_names if tags_as_list else ",".join(canonical_names)


def resolve_owner_transaction_tag_names(
    db: Session,
    *,
    ledger: Ledger,
    names: list[str],
    owner_tag_rows: list[UserTagProjection] | None = None,
) -> tuple[dict[str, UserTagProjection], list[str]]:
    """按名称解析 Owner 标签，并拒绝同名多 ID 的歧义引用。

    返回 ``(name -> row, missing_names)``，由调用方决定缺失名称是允许 Owner
    创建，还是必须拒绝 Editor。歧义名称没有安全的 fallback：不同入口或不同
    数据库执行计划可能选择不同 ``sync_id``，因此统一要求调用方改用明确 ID。
    """
    wanted = _dedupe_non_empty(names)
    if not wanted:
        return {}, []

    rows = owner_tag_rows
    if rows is None:
        rows = list(
            db.scalars(
                select(UserTagProjection).where(
                    UserTagProjection.user_id == ledger.user_id,
                    UserTagProjection.name.in_(wanted),
                )
            ).all()
        )
    grouped: dict[str, list[UserTagProjection]] = {}
    for row in rows:
        name = str(row.name or "").strip()
        if name:
            grouped.setdefault(name, []).append(row)

    ambiguous_names = [name for name in wanted if len(grouped.get(name, [])) > 1]
    if ambiguous_names:
        raise SharedTransactionTagError(
            "SHARED_TX_TAG_AMBIGUOUS",
            "Ambiguous shared transaction tag names require explicit owner tag IDs",
            ambiguous_tag_names=ambiguous_names,
        )

    by_name = {name: grouped[name][0] for name in wanted if grouped.get(name)}
    missing_names = [name for name in wanted if name not in by_name]
    return by_name, missing_names


def load_owner_transaction_tags(
    db: Session,
    *,
    ledger: Ledger,
) -> list[UserTagProjection]:
    """一次载入 Owner 的标签，供一个同步批次内多笔交易复用。"""
    return list(
        db.scalars(
            select(UserTagProjection).where(
                UserTagProjection.user_id == ledger.user_id,
            )
        ).all()
    )


def _normalize_names(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        raise SharedTransactionTagError(
            "SHARED_TX_TAG_INVALID_FORMAT",
            "Shared transaction tags must be a string or list",
        )
    return _dedupe_non_empty(values)


def _normalize_ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SharedTransactionTagError(
            "SHARED_TX_TAG_INVALID_FORMAT",
            "Shared transaction tag IDs must be a list",
        )
    return _dedupe_non_empty(raw)


def _dedupe_non_empty(values: list[Any]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if value and value not in result:
            result.append(value)
    return result
