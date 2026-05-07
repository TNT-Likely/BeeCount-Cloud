"""write router 入口(包形式替换原 write.py)。

main.py 的 `from .routers import write` + `app.include_router(write.router, ...)`
不用改,因为这个包的 `router` 把各 entity 的子 router 全收敛了。

按路由拆分的动机:以前 write.py 1658 行,review "改了一个 endpoint 的
行为"要在一个大文件里找;现在 PATCH /ledgers 的改动一定在 ledgers.py,
git diff 就是"对哪个 entity 的哪条路由做了什么"。共享逻辑(snapshot
写入引擎 / idempotency / rename cascade / normalize)留在 _shared.py,
所有 entity 行为统一在这里修。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import accounts, budgets, categories, ledgers, tags, transactions, transactions_batch

router = APIRouter()
router.include_router(ledgers.router)
router.include_router(transactions.router)
router.include_router(transactions_batch.router)
router.include_router(accounts.router)
router.include_router(budgets.router)
router.include_router(categories.router)
router.include_router(tags.router)
