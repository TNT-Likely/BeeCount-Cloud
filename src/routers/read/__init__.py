"""read router 入口(包形式替换原 read.py)。

main.py 的 `from .routers import read` + `app.include_router(read.router, ...)`
不用改。router 实例定义在 _shared.py,子模块 `from ._shared import *` 拿到
同一个对象,装饰器挂在同一个 router 上。

按资源组拆分:
  - ledgers    账本维度读
  - workspace  跨账本聚合读
  - summary    单独小端点

改某条具体端点 → 对应子模块;改共享查询 helper / 字段映射 → _shared.py。
"""
from ._shared import router  # noqa: F401

# 导入子模块触发 @router 装饰器注册。
from . import ledgers, summary, workspace  # noqa: E402,F401

__all__ = ['router']
