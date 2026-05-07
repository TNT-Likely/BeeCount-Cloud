"""内存 token cache —— 跟 image_cache 同模式,单 user 同时只能有一个 active token。

key = `import_<uuid>`,value = (user_id, ImportData, ImportFieldMapping, target_ledger_id, dedup_strategy, expires_at)。

server 重启就丢 — 用户重传一次即可。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .schema import ImportData, ImportFieldMapping

logger = logging.getLogger(__name__)

_TTL_SECONDS = 30 * 60


@dataclass
class _Entry:
    user_id: str
    data: ImportData
    mapping: ImportFieldMapping
    target_ledger_id: str | None
    dedup_strategy: str
    auto_tag_names: list[str]
    created_at: float


# 防御并发:upload / preview / execute 都可能同时碰到这张表
_lock = threading.Lock()
_TOKENS: dict[str, _Entry] = {}
_USER_ACTIVE: dict[str, str] = {}  # user_id → token(单 user 一个 active)


def _now() -> float:
    return time.monotonic()


def _purge_expired() -> None:
    cutoff = _now() - _TTL_SECONDS
    expired = [k for k, v in _TOKENS.items() if v.created_at < cutoff]
    for k in expired:
        entry = _TOKENS.pop(k, None)
        if entry is not None and _USER_ACTIVE.get(entry.user_id) == k:
            _USER_ACTIVE.pop(entry.user_id, None)


def save_token_data(
    *,
    user_id: str,
    data: ImportData,
    mapping: ImportFieldMapping,
    target_ledger_id: str | None,
    dedup_strategy: str,
    auto_tag_names: list[str] | None = None,
) -> tuple[str, datetime]:
    """新建 token —— 同 user 已有 token 自动失效。返回 (token, expires_at)。"""
    with _lock:
        _purge_expired()
        # 取消旧 active token
        old = _USER_ACTIVE.get(user_id)
        if old is not None:
            _TOKENS.pop(old, None)
            logger.info("import.cache.evict user=%s old_token=%s", user_id, old)

        token = f"imp_{uuid4().hex[:24]}"
        entry = _Entry(
            user_id=user_id,
            data=data,
            mapping=mapping,
            target_ledger_id=target_ledger_id,
            dedup_strategy=dedup_strategy,
            auto_tag_names=list(auto_tag_names or []),
            created_at=_now(),
        )
        _TOKENS[token] = entry
        _USER_ACTIVE[user_id] = token
        expires_at = datetime.now(tz=timezone.utc).replace(microsecond=0)
        # 用 wall-clock 给前端展示 expires_at;内存 TTL 仍按 monotonic 算
        from datetime import timedelta as _td
        expires_at = expires_at + _td(seconds=_TTL_SECONDS)
        return token, expires_at


def get_token_data(*, token: str, user_id: str) -> _Entry | None:
    """读取 token —— 校验属于该 user_id,并刷新 last-access(防止用户 preview
    阶段长时间停留被清掉)。"""
    with _lock:
        _purge_expired()
        entry = _TOKENS.get(token)
        if entry is None:
            return None
        if entry.user_id != user_id:
            return None
        # 不重置 created_at(否则用户停留 N 小时永不过期)。30min 是从 upload 起算
        return entry


def update_token(
    *,
    token: str,
    user_id: str,
    mapping: ImportFieldMapping | None = None,
    target_ledger_id: str | None = None,
    dedup_strategy: str | None = None,
    auto_tag_names: list[str] | None = None,
) -> _Entry | None:
    """preview / execute 阶段会改 mapping / target_ledger_id 等,这里 update。
    入参 None = 保持不变(注意:auto_tag_names 用空列表覆盖空值是合法的,所以
    单独区分)。"""
    with _lock:
        entry = _TOKENS.get(token)
        if entry is None or entry.user_id != user_id:
            return None
        if mapping is not None:
            entry.mapping = mapping
        if target_ledger_id is not None:
            entry.target_ledger_id = target_ledger_id
        if dedup_strategy is not None:
            entry.dedup_strategy = dedup_strategy
        if auto_tag_names is not None:
            entry.auto_tag_names = list(auto_tag_names)
        return entry


def consume_token(*, token: str, user_id: str) -> _Entry | None:
    """execute 完成后调,从 cache 删掉 token —— 防止重复执行。"""
    with _lock:
        entry = _TOKENS.pop(token, None)
        if entry is None or entry.user_id != user_id:
            # 不属于 user 还塞回去,避免误删别人的
            if entry is not None:
                _TOKENS[token] = entry
            return None
        if _USER_ACTIVE.get(entry.user_id) == token:
            _USER_ACTIVE.pop(entry.user_id, None)
        return entry


def cancel_token(*, token: str, user_id: str) -> bool:
    """用户主动取消。"""
    with _lock:
        entry = _TOKENS.get(token)
        if entry is None or entry.user_id != user_id:
            return False
        _TOKENS.pop(token, None)
        if _USER_ACTIVE.get(entry.user_id) == token:
            _USER_ACTIVE.pop(entry.user_id, None)
        return True


def clear_all() -> None:
    """测试用 —— 清空所有 token。"""
    with _lock:
        _TOKENS.clear()
        _USER_ACTIVE.clear()
