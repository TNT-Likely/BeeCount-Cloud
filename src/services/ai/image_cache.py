"""B2 截图记账的临时图片缓存。

流程:
1. user 粘贴截图 → 前端上传到 /ai/parse-tx-image
2. server 调 LLM 解析 + **同时缓存原 image bytes**(关联返一个 image_id)
3. 前端拿到 tx_drafts + image_id,渲染 confirm UI
4. user 编辑 + confirm → POST /write/transactions/batch?image_id=xxx
5. server 从缓存取 image bytes → 转正式 attachment → 关联到所有 tx

缓存 TTL 30 分钟(够用户编辑 + 不堆积内存)。

实现:in-memory dict,简单够用。多 worker 部署场景下 image_id 跟 worker 绑定,
batch save 必须打到同一 worker — 但当前 BeeCount-Platform 单 worker 跑 uvicorn,
这个限制不存在。规模上来了再换 Redis / 文件系统。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from threading import Lock


_TTL_SECONDS = 30 * 60  # 30 分钟


@dataclass(frozen=True)
class CachedImage:
    image_bytes: bytes
    mime_type: str
    user_id: str
    expires_at: float


_cache: dict[str, CachedImage] = {}
_lock = Lock()


def store_image(*, image_bytes: bytes, mime_type: str, user_id: str) -> str:
    """缓存 image bytes,返回随机 image_id。"""
    image_id = uuid.uuid4().hex
    with _lock:
        _evict_expired_locked()
        _cache[image_id] = CachedImage(
            image_bytes=image_bytes,
            mime_type=mime_type,
            user_id=user_id,
            expires_at=time.monotonic() + _TTL_SECONDS,
        )
    return image_id


def consume_image(*, image_id: str, user_id: str) -> CachedImage | None:
    """取出 image(并删除,一次性)。user_id 不匹配返 None,防越权。"""
    with _lock:
        _evict_expired_locked()
        cached = _cache.get(image_id)
        if cached is None:
            return None
        if cached.user_id != user_id:
            return None  # 不属于当前 user,拒绝
        del _cache[image_id]
        return cached


def peek_image(*, image_id: str, user_id: str) -> CachedImage | None:
    """看一眼但不删 — 没消费场景(未来加重新识别按钮可用)。"""
    with _lock:
        _evict_expired_locked()
        cached = _cache.get(image_id)
        if cached is None or cached.user_id != user_id:
            return None
        return cached


def _evict_expired_locked() -> None:
    """删过期 entry。调用时必须已持锁。"""
    now = time.monotonic()
    expired = [k for k, v in _cache.items() if v.expires_at < now]
    for k in expired:
        del _cache[k]


def clear_cache() -> None:
    """测试用 — 清空缓存。"""
    with _lock:
        _cache.clear()
