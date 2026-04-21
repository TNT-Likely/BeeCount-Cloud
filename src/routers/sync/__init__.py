"""sync router 入口(包形式替换原 sync.py)。

main.py 的 `from .routers import sync` + `app.include_router(sync.router, ...)`
不用改。这个包自己持有 APIRouter 实例,各 endpoint 子模块在导入时把自己
的 @router.xxx 装饰到上面。
"""
# router 实例定义在 _shared.py,各 endpoint 子模块 `from ._shared import *`
# 拿到同一个对象 —— 装饰器注册在 sub-module 加载时发生。
from ._shared import router  # noqa: F401
from ...sync_applier import apply_change_to_projection, INDIVIDUAL_ENTITY_TYPES  # noqa: F401 — 兼容旧 import

# 导入子模块触发 @router 装饰器注册。顺序无关,所有 endpoint path 互不冲突。
from . import push, pull, full, ledgers  # noqa: E402,F401

__all__ = ['router', 'apply_change_to_projection', 'INDIVIDUAL_ENTITY_TYPES']
