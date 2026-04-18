"""Process-local LRU cache for parsed ledger snapshots.

背景:每个 `/read/*` 端点都要 `_get_latest_snapshot(ledger_id)` → SELECT 一
行 `sync_changes.payload_json`(一个大账本可能 2-3MB)→ `json.loads` 出
~10k 条 items 的 dict。本机测 10k tx 账本 parse 一次 30-50ms,一次 home
dashboard 5-6 个 `/read/*` 并发跑 → 200-400ms 累积。

缓存策略:
- 进程内 dict,key = ledger_id(UUID str),value = (change_id, parsed dict)
- 读:先 SELECT 一下 `MAX(change_id)` 看最新快照 change_id
  - 命中(cached change_id == latest)→ 返回缓存 dict,跳过 payload 读 + parse
  - 不命中 → 读 payload + parse + put
- 写:调 `invalidate(ledger_id)` 丢掉该 ledger 条目
- 跨进程:uvicorn 多 worker 各有独立缓存,但命中判断基于 change_id,陈旧
  缓存被拦住,不会返错数据。只是 worker A 先写、worker B 旧缓存失效的代价
  是 B 下次读再 parse 一次,无风险。

没上 Redis 是因为单进程已经把 10k tx 账本的读延迟从 ~50ms 降到 ~1ms,
满足自部署 + 小规模 SaaS。真要多 worker 共享再换 Redis 也是 drop-in。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

# 用 OrderedDict 做手工 LRU;上限默认 128 个 ledger 的 snapshot。
# 单个 ledger 3MB,满容量约 ~400MB,足够。大 SaaS 部署可以调环境变量或换 Redis。
_CAPACITY = 128


class _SnapshotCache:
    def __init__(self, capacity: int = _CAPACITY) -> None:
        self._capacity = capacity
        self._store: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, ledger_id: str, expected_change_id: int) -> dict[str, Any] | None:
        """返回 snapshot dict 当且仅当缓存里有且 change_id 匹配。
        其他情况(没命中 / change_id 过期)都返 None,调用方自己 fetch。"""
        with self._lock:
            hit = self._store.get(ledger_id)
            if hit is None:
                return None
            cached_change_id, snapshot = hit
            if cached_change_id != expected_change_id:
                # 陈旧 —— 删掉,调用方会重新拉最新版本 put 回来
                self._store.pop(ledger_id, None)
                return None
            # LRU:移到末尾
            self._store.move_to_end(ledger_id)
            return snapshot

    def put(self, ledger_id: str, change_id: int, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._store[ledger_id] = (change_id, snapshot)
            self._store.move_to_end(ledger_id)
            while len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def invalidate(self, ledger_id: str) -> None:
        with self._lock:
            self._store.pop(ledger_id, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


_instance = _SnapshotCache()


def get(ledger_id: str, expected_change_id: int) -> dict[str, Any] | None:
    return _instance.get(ledger_id, expected_change_id)


def put(ledger_id: str, change_id: int, snapshot: dict[str, Any]) -> None:
    _instance.put(ledger_id, change_id, snapshot)


def invalidate(ledger_id: str) -> None:
    _instance.invalidate(ledger_id)


def size() -> int:
    return _instance.size()
