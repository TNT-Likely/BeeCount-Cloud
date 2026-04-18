"""In-memory log ring buffer for the BeeCount Cloud admin console.

进程内 ring buffer。挂在 root logger 上,所有 `logging.getLogger(__name__)`
产出的 log(sync / read / write 路由等) 都会进来,web 管理员可以通过
`GET /api/v1/admin/logs` 拉取。

设计取舍:
- 纯内存,重启清零。对 BeeCount Cloud(小规模自部署)够用,不引入落盘 /
  外部日志栈的运维负担。
- `collections.deque(maxlen=...)` 满了自动丢最老的,不会无限增长。
- 线程 / 异步安全:handler 的 `emit()` 在 Python logging 里已经被
  `self.lock` 保护,deque 本身也是原子的。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any


class RingBufferLogHandler(logging.Handler):
    """Logging handler that keeps the most recent N records in memory."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__(level=logging.DEBUG)
        self._capacity = max(100, int(capacity))
        self._buffer: deque[dict[str, Any]] = deque(maxlen=self._capacity)
        self._seq_lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging contract
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging blow up
            message = str(record.msg)

        if record.exc_info:
            try:
                message = f"{message}\n{self.formatter.formatException(record.exc_info) if self.formatter else ''}".rstrip()
            except Exception:  # noqa: BLE001
                pass

        with self._seq_lock:
            self._seq += 1
            seq = self._seq

        entry = {
            "seq": seq,
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        # 额外:如果 extra 里塞了 ledger / user 这种结构化字段,带出去
        for key in ("ledger_id", "user_id", "device_id", "entity_type", "tag"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        self._buffer.append(entry)

    def snapshot(
        self,
        *,
        limit: int = 200,
        level: str | None = None,
        q: str | None = None,
        since_seq: int | None = None,
        sources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a filtered list of recent log entries (newest last).

        `sources` 是 logger 名称前缀列表 —— 命中任意一条即匹配(OR)。例如
        `['src.routers.sync']` 只返回 sync 路由的 log,`['uvicorn']` 只看
        HTTP 请求日志。
        """
        level_rank = logging.getLevelName((level or "").upper()) if level else None
        # 当 level 不是合法值时 getLevelName 返回字符串 "Level N";此时忽略过滤
        if not isinstance(level_rank, int):
            level_rank = None

        qlow = (q or "").strip().lower() or None
        clamped_limit = max(1, min(int(limit or 200), self._capacity))
        source_prefixes = [s.strip() for s in (sources or []) if s and s.strip()]

        items = list(self._buffer)
        result: list[dict[str, Any]] = []
        for entry in items:
            if since_seq is not None and entry["seq"] <= since_seq:
                continue
            if level_rank is not None:
                entry_rank = logging.getLevelName(entry["level"])
                if not isinstance(entry_rank, int) or entry_rank < level_rank:
                    continue
            if source_prefixes:
                logger_name = str(entry.get("logger") or "")
                if not any(logger_name.startswith(pref) for pref in source_prefixes):
                    continue
            if qlow is not None:
                haystack = f"{entry['message']} {entry['logger']}".lower()
                if qlow not in haystack:
                    continue
            result.append(entry)

        return result[-clamped_limit:]

    @property
    def capacity(self) -> int:
        return self._capacity


_GLOBAL_HANDLER: RingBufferLogHandler | None = None


def install_ring_buffer(capacity: int = 1000) -> RingBufferLogHandler:
    """Attach a process-wide ring buffer handler to the root logger.

    调用多次是幂等的 —— 如果已经装过了,直接返回现有 handler。
    """
    global _GLOBAL_HANDLER
    if _GLOBAL_HANDLER is not None:
        return _GLOBAL_HANDLER

    handler = RingBufferLogHandler(capacity=capacity)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    # FastAPI / uvicorn 自己的 logger 默认 propagate=False,手动挂上去确保
    # 请求日志也能进到 ring buffer。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        try:
            logging.getLogger(name).addHandler(handler)
        except Exception:  # noqa: BLE001
            pass
    _GLOBAL_HANDLER = handler
    return handler


def get_ring_buffer() -> RingBufferLogHandler | None:
    return _GLOBAL_HANDLER
