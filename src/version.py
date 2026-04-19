"""BeeCount Cloud 服务端版本号。

单点定义,供 FastAPI app metadata / `/version` 公开端点 /发版 tag 等复用。
mobile + web 都会在 UI 上展示这个数值,方便用户一眼判断 server 跑的哪版。

版本号读取优先级:
1. `APP_VERSION` 环境变量(Docker 镜像构建时由 CI 注入,`ARG VERSION` → `ENV APP_VERSION`)
2. Fallback:当前仓库开发期的基线版本(本地跑 uvicorn 时显示,不会误导)
"""

from __future__ import annotations

import os

# CI 推 tag 时通过 --build-arg VERSION=x.y.z 传入,Dockerfile 写进 ENV。
# 本地 `uvicorn` 运行时没这个 env,走下面 fallback。
_FALLBACK_VERSION = "1.0.0"
__version__ = (os.environ.get("APP_VERSION") or _FALLBACK_VERSION).strip()

APP_NAME = "BeeCount Cloud"
