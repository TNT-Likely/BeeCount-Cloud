"""BeeCount Cloud 服务端版本号。

单点定义,供 FastAPI app metadata / `/version` 公开端点 /发版 tag 等复用。
mobile + web 都会在 UI 上展示这个数值,方便用户一眼判断 server 跑的哪版。

版本号读取优先级:
1. `APP_VERSION` 环境变量(Docker 镜像构建时由 CI 注入,`ARG VERSION` → `ENV APP_VERSION`)
2. Fallback:当前仓库开发期的基线版本(本地跑 uvicorn 时显示,不会误导)
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

# CI 推 tag 时通过 --build-arg VERSION=x.y.z 传入,Dockerfile 写进 ENV。
# 本地 `uvicorn` 运行时没这个 env,走下面 fallback。
_FALLBACK_VERSION = "1.0.0"
_DEFAULT_MIN_APP_VERSION = "4.0.0"
_DEFAULT_RECOMMENDED_APP_VERSION = "4.1.0"

__version__ = (os.environ.get("APP_VERSION") or _FALLBACK_VERSION).strip()
MIN_APP_VERSION = (os.environ.get("MIN_APP_VERSION") or _DEFAULT_MIN_APP_VERSION).strip()
RECOMMENDED_APP_VERSION = (
    os.environ.get("RECOMMENDED_APP_VERSION") or _DEFAULT_RECOMMENDED_APP_VERSION
).strip()

APP_NAME = "BeeCount Cloud"
APP_VERSION_HEADER = "X-BeeCount-App-Version"

_SEMVER_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:$|[+\-\s(])", re.IGNORECASE)


def parse_semver(raw: str | None) -> tuple[int, int, int] | None:
    """Parse leading X.Y.Z from strings like '4.1.0', '4.1.0+42', 'v4.1.0 (42)'."""
    if not raw or not isinstance(raw, str):
        return None
    match = _SEMVER_RE.match(raw)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_version_below(current: str | None, required: str | None) -> bool:
    """Return True only when both versions parse cleanly and current < required."""
    cur = parse_semver(current)
    req = parse_semver(required)
    if cur is None or req is None:
        return False
    return cur < req


def extract_client_app_version(headers: Mapping[str, str]) -> str | None:
    """Read X-BeeCount-App-Version header trimmed to Device.app_version length."""
    value = headers.get(APP_VERSION_HEADER) or headers.get(APP_VERSION_HEADER.lower())
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:64]
