# BeeCount Cloud —— AI 助手/新人阅读指南

本文件给 AI 编码助手(Claude Code / Copilot 等)和第一次进本仓的人类开发者
一个快速定位,告诉你**改什么在哪里改**、**绕不过去的契约**、**哪类修改
最容易出 bug**。

## 改代码之前必读

**如果要改跟 mobile ↔ server 或 web ↔ server 同步相关的任何逻辑**,
先读:

### [docs/SYNC_ARCHITECTURE.md](./docs/SYNC_ARCHITECTURE.md)

里面有:
- 核心路由目录与职责(`routers/sync/` `routers/write/` `routers/read/` +
  `sync_applier.py` + `ws.py`)
- 4 条核心数据流(mobile→web / web→mobile / mobile 首次同步 / web 读)
- **契约部分**(最容易踩坑):
  - user-global vs ledger-scoped 实体的 `ledger_id` 通道区分
  - LWW 冲突决胜规则
  - rename cascade 在 push / write 两条路径上的实现
  - 增量 push 的 merge 字段语义
  - change_id 单调性
  - `lock_ledger_for_materialize` 锁粒度
- debug 清单 + 修改前自检清单

**这块代码历史上出过几次难复现的 bug**(2026-04 修过两次 ledger_id
误用 + budget import path 错误),根因都是"有隐式契约但没在契约点强制"。
动之前花 5 分钟读完 `SYNC_ARCHITECTURE.md` 省几小时 debug。

## 代码约定(server 端)

### 路由组织

每个 HTTP API 组是 `src/routers/<group>/` 包形式,结构:

```
<group>/
  __init__.py     聚合 router,main.py 的 import 不变
  _shared.py      共享 imports / helpers / 常量 / router 实例
                  __all__ 显式列表(wildcard 默认不带下划线名字)
  <entity>.py     按资源拆分的 endpoint 文件,3 个 HTTP 方法(POST/PATCH/DELETE)
                  或按逻辑分组的 GET
```

修改某个 endpoint → 进对应 entity 文件,修改跨 endpoint 的共享逻辑 →
改 `_shared.py`。不要把业务加回到 `__init__.py`。

### 分 snapshot / projection / event log

同步层有三种存储形态,**不要混用**:

- `sync_changes`(事件流):append-only,`change_id` 自增,pull 增量同步
  的源头。永远只插入,从不 UPDATE。
- `read_*_projection`(5 张 denorm 表):读路径唯一权威源。LWW / rename
  cascade 落盘到这里。
- `ledger_snapshot`(JSON blob):方案 B 之后基本不写,`/sync/full` 按需
  从 projection 懒构建。**新代码不要再主动写 ledger_snapshot。**

### 新增 entity

如果要加一种新的 sync 实体(比如 "recurring_transaction"):

1. 新建 `read_*_projection` 表 + alembic migration
2. `src/projection.py` 加 upsert_* / delete_* / rename_cascade_* (如需)
3. `src/sync_applier.py` 登记 `_MERGE_SPECS` + `_UPSERT_DISPATCH` +
   `_DELETE_DISPATCH` 三张表
4. `src/routers/write/<entity>.py` 加 POST/PATCH/DELETE endpoints
5. `src/routers/read/ledgers.py` 或 `workspace.py` 加读端点
6. 补 pytest(`tests/test_projection_consistency.py` 已有
   mixed-entities 模板可参考)

### 测试

- `pytest tests/` 全过才能合代码
- 多账本场景至少有一个测试覆盖(一个 sync_id 在多个账本的 projection 里
  同时出现,dedup 行为)
- 添加新 entity 必须添加一条 `test_mobile_push_<entity>_partial_update_keeps_existing_fields`
  风格的 merge 契约测试 —— 防 2026-04 踩过的"漏 merge 某字段"类 bug

### 日志

- 同步决策点用 `logger.info("sync.push.accept entity=...")` 结构化日志
- 错误 path 用 `logger.exception` 带上 entity_type / action / sync_id /
  payload,方便 /sync/push 500 时定位到具体哪条 change 炸的
- 服务端有 admin 日志面板(web header 的 📜 按钮,admin 可见),默认筛
  ERROR 级别

## Frontend

Mobile 端(Flutter)和 Web 端(React)各自有仓,各自有 CLAUDE.md:

- Mobile: `../BeeCount/CLAUDE.md`
- Web: 前端源码在 `frontend/apps/web/`,README 见 `frontend/README.md`
  (如有)

跟服务端同步相关的 mobile 契约(`ChangeTracker.recordUserGlobalChange` /
`recordLedgerChange`)在 mobile 仓 CLAUDE.md 里。Server 端的契约在上面
链的 `docs/SYNC_ARCHITECTURE.md` 里。

## 工具

- `python -m pytest tests/` 跑全部服务端测试
- 本地 dev server:`uvicorn src.main:app --reload`
- 本地 DB 默认 SQLite:`beecount.db`(仓根),可用 `sqlite3` CLI 直接查
- 生产部署见 `docs/DEPLOYMENT.md`
