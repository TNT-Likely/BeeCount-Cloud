"""BeeCount Cloud 服务端版本号。

单点定义,供 FastAPI app metadata / `/version` 公开端点 /发版 tag 等复用。
mobile + web 都会在 UI 上展示这个数值,方便用户一眼判断 server 跑的哪版。
"""

from __future__ import annotations

__version__ = "0.1.0"

APP_NAME = "BeeCount Cloud"
