"""AI 子路由聚合。

第一期只有 A1 文档 Q&A(/ask)。后续 B1 / B2 加 /parse-tx, /parse-tx-image 时
也挂在这下面。

设计:.docs/web-cmdk-ai-doc-search.md
"""
from fastapi import APIRouter

from . import ask

router = APIRouter()
router.include_router(ask.router)

__all__ = ["router"]
