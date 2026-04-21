# BeeCount Cloud 同步架构与核心路由总览

本文档描述 BeeCount Cloud(server 端)的同步机制、核心路由分工、数据流
走向,以及最容易踩坑的几处契约。**改动 `src/routers/sync/` / `src/routers/write/`
/ `src/sync_applier.py` / `src/routers/ws.py` 之前请先读完"核心契约"一节。**

2026-04-21 一次 refactor 之后这些代码按路由拆成了包(`write/`, `read/`,
`sync/`),业务核心(`apply_change_to_projection`)进一步抽到 `src/sync_applier.py`。
本文档是拆分之后的地图。

---

## 1. 架构总览

三方:**Mobile App**(Flutter)、**Web 面板**(React)、**Server**(FastAPI + SQLite/Postgres)。

Server 端的存储分两层:

- **Event log**:`sync_changes` 表。不可变的增量事件流,每条写入顺序编号
  `change_id`。保留用于 pull 增量同步的源头。
- **Projection**:`read_tx_projection` / `read_account_projection` /
  `read_category_projection` / `read_tag_projection` / `read_budget_projection`
  五张 denormalized 表。读路径(`/read/*`)直接查这些,不用回 event log
  重放。写路径的 LWW / rename cascade 等规则都落在这里。

**历史上还有 `ledger_snapshot`**(JSON blob)作为聚合源,但方案 B
(projection-as-authority)之后:
- /sync/push 不再写 ledger_snapshot 行
- /read/* 全部从 projection 读
- /sync/full 按需从 projection 懒构建 snapshot(供 mobile 首次同步 / 重装)

这个过渡导致一些历史代码路径看着还在处理 snapshot,但生产上用不上。
新代码不应该再主动写 ledger_snapshot。

---

## 2. 目录总览

```
src/
  routers/
    sync/               ─ mobile ↔ server 双向增量同步的 HTTP 接口
      __init__.py        包入口,re-export router
      _shared.py         共享 imports + _to_utc / _max_cursor_for_ledgers + router 实例
      push.py            POST /sync/push         mobile 推送本地变更(批量)
      pull.py            GET  /sync/pull         按 cursor 增量拉取(mobile + web 掉线补)
      full.py            GET  /sync/full         按需构建并返回账本完整 snapshot
      ledgers.py         GET  /sync/ledgers      列出 caller 可访问账本元信息

    write/              ─ web 面板的写入接口
      __init__.py        包入口
      _shared.py         _commit_write 写入引擎 + idempotency + rename cascade + normalize 等
      ledgers.py         POST/PATCH/DELETE /ledgers
      transactions.py    POST/PATCH/DELETE /ledgers/{id}/transactions
      accounts.py        同上 /accounts
      categories.py      同上 /categories
      tags.py            同上 /tags
      # budget 暂时只走 /sync/push,没有 web /write 端点

    read/               ─ 读路径(mobile 和 web 都用)
      __init__.py        包入口
      _shared.py         ledger 可见性 / owner 回填 / snapshot_cache / 字段映射
      ledgers.py         /ledgers 及 /ledgers/{id}/* 账本维度读
      workspace.py       /workspace/* 跨账本聚合读
      summary.py         /summary 用户总览

    ws.py                WebSocket 路由:server → 客户端的实时推送

  sync_applier.py       ─ 纯业务:一条 SyncChange 怎么落到 projection
                         apply_change_to_projection 被 /sync/push 消费;
                         未来 backup restore / replay 也会用

  snapshot_builder.py   按需从 projection 构建 /sync/full 的 snapshot
  snapshot_cache.py     短 TTL 内存缓存 /sync/full 结果
  snapshot_mutator.py   /write/* 的 snapshot mutation helper(历史遗留,仍在使用)
  projection.py         projection 表的 upsert/delete/rename_cascade 低层 SQL
```

---

## 3. 数据流

下面四条是双向同步的核心路径。修代码前想清楚自己在动哪一条。

### 3.1 Mobile 写入 → Web 可见

```
用户在 mobile 改交易
  ├─ Flutter LocalRepository 写本地 SQLite
  └─ ChangeTracker 记一条 local_changes 行(待推)
                              │
                              ▼
  自动 sync 触发(app 生命周期 / 用户手动 / 周期性)
                              │
  POST /api/v1/sync/push   ◄──┘
  batch: [...changes]
                              │
  src/routers/sync/push.py    │
  ┌─────────────────────────▼──────────────────────────────┐
  │ 单个 change 处理:                                        │
  │   1. LWW 检查:比 (updated_at, device_id) 跟已有最新 change │
  │      - 输:跳过;平:idempotent replay;赢:继续            │
  │   2. INSERT 一条 sync_changes 行(自增 change_id)         │
  │   3. sync_applier.apply_change_to_projection(change)     │
  │      ├─ ledger: 更新 Ledger 表 name/currency             │
  │      ├─ delete: projection.delete_* + GC 孤立附件         │
  │      └─ upsert:                                          │
  │          a. rename cascade 探测(account/category/tag 改名)│
  │          b. _merge_with_existing 拉现有行把 None 字段补齐  │
  │          c. projection.upsert_* 写行(同事务)             │
  │ 整批单事务 commit                                        │
  └──────────────────────────────────────────────────────────┘
                              │
  commit 后 WebSocket 广播(src/routers/ws.py):
                              │
  web 端 SyncSocketContext 收到 sync_change 事件
                              │
  各 Page 的 useSyncRefresh 触发 /read/* 重拉
                              │
  用户看到新数据
```

### 3.2 Web 写入 → Mobile 可见

```
用户在 web 面板改交易
  │
  POST /api/v1/write/ledgers/{id}/transactions
  │
  src/routers/write/transactions.py
  │
  ┌─────────────────────────▼──────────────────────────────┐
  │ _commit_write(write/_shared.py)                         │
  │   1. 加载当前 snapshot(snapshot_mutator.ensure_snapshot_v2)│
  │   2. mutate lambda 改 snapshot 对应 array               │
  │   3. diff prev vs next → _diff_entity_list              │
  │      为每条 diff 写一行 sync_changes(tx / cascade 可批量) │
  │      同时 projection.upsert_* / delete_*                │
  │   4. idempotency key 写入(sync_push_idempotency 表)     │
  │   5. commit                                             │
  └──────────────────────────────────────────────────────────┘
                              │
  commit 后 WebSocket 广播
                              │
  mobile 端 useSyncSocket 收到 sync_change
                              │
  触发 SyncEngine._pull(ledgerId):
    GET /api/v1/sync/pull?since=<cursor>&device_id=<my-device>
                              │
  src/routers/sync/pull.py 返回 since 以来所有 change
    (过滤掉 updated_by_device_id = 自己 device 的)
                              │
  mobile 应用到本地 SQLite,UI 自动刷新
```

### 3.3 Mobile 首次同步或重装

```
mobile 登录 / 换设备 / 清缓存重装
  │
  GET /api/v1/sync/ledgers              列出可访问账本
  GET /api/v1/sync/full?path=<ledger>   每个账本拉完整 snapshot
                              │
  src/routers/sync/full.py
  │
  snapshot_builder.build_snapshot(db, ledger) ← 从 projection 现场构建
  snapshot_cache 内存缓存(短 TTL,减少重复构建开销)
                              │
  mobile 把 snapshot.items / accounts / categories / tags / budgets
  整份写到本地 SQLite(伴随本地 id ↔ sync_id 映射)
                              │
  以后 GET /sync/pull?since=<cursor> 增量拉
```

### 3.4 Web 读

```
Web Page mount / useSyncRefresh 触发
  │
  GET /api/v1/read/workspace/accounts (或别的 /read/* 端点)
  │
  src/routers/read/workspace.py
  │
  查 read_*_projection 表做 SQL 聚合 + 跨账本 dedup + owner 回填
  不经过 snapshot / sync_changes
  │
  返回 JSON
```

---

## 4. 核心契约(最容易踩坑的)

### 4.1 user-global 实体 vs ledger-scoped 实体

**Mobile 端** `local_changes.ledger_id` 的含义有两种(具体见 mobile 仓
`CLAUDE.md`):

| Entity | Scope | `local_changes.ledger_id` | Server `sync_changes.ledger_id` |
|---|---|---|---|
| account | user-global | **必须** `0`,走 globalChanges 通道 | 随 push 挂到具体账本下 |
| category | user-global | **必须** `0` | 同上 |
| tag | user-global | **必须** `0` | 同上 |
| transaction | ledger-scoped | 具体 ledger.id | 具体 ledger.id |
| budget | ledger-scoped | 具体 ledger.id | 具体 ledger.id |
| ledger | 自身 | 自己的 id | 自己的 id |

**Mobile 侧出错过一次**:`updateAccount` 历史上用 `ledgerId: account.ledgerId`
(不是 0),sync_engine 的 `_push()` 查询漏掉,rename 永远卡本地。2026-04
修复改成 `ledgerId: 0`,并加了 `ChangeTracker.recordUserGlobalChange` / 
`recordLedgerChange` 两个 API 强制分流(见 mobile 仓)。

**Server 侧**不区分这两种 scope —— sync_changes 表每条都绑一个具体 ledger_id,
即使 entity 是 user-global(push 时 mobile 挂在 active ledger 下)。Server
只按 entity_sync_id 做 projection 去重。

### 4.2 LWW 冲突决胜

`sync/push.py` 里每条进来的 change 先查现有最新同 `(entity_type, entity_sync_id)` 
的 sync_changes 行,比较 **(updated_at, device_id)** 两元组:

- 入侵 < 存在 → 拒绝,记 `sync.push.conflict` + AuditLog,不写新行
- 入侵 = 存在 → 幂等回放,跳过
- 入侵 > 存在 → 接受

`device_id` 作为 tiebreaker 是为了让两台服务器 / 重试调用得出确定性结果。

**时钟偏移防御**:入侵 `updated_at` 会被 clamp 到 `server_now + 5s`,防止
mobile 本地时钟错乱一直赢 LWW。

### 4.3 Idempotency key(仅 write path)

Web `/write/*` 的 `_commit_write` 接受客户端传来的 `Idempotency-Key` 头。
同 key + 同 payload hash 重复请求直接返回上次的响应,不重放写入。存储在
`sync_push_idempotency` 表,24 小时过期,每次写入顺手清一次过期条目。

mobile `/sync/push` 走 LWW + (updated_at, device_id) 的幂等,**不**用这个
idempotency key 机制。

### 4.4 rename cascade

account / category / tag 改名时,`ReadTxProjection` 里的 denormalized 列
(`account_name` / `category_name` / `tags_csv` 等)也要刷,不然 web 看
tx 列表的账户名永远是旧的。

- **/sync/push 路径**:`sync_applier._detect_and_run_rename_cascade` 在
  upsert *之前* 跑(upsert 之后 projection 里就只有新名,没法按旧名 match)。
- **/write/* 路径**:`write/_shared.py::_diff_entity_list` + `_collect_renames`
  处理。更进一步的优化是 `_tx_diff_only_cascade`:当 tx 的 diff 只在
  cascade 字段上(账户/分类改名触发的 denorm 更新),绕开 per-row projection
  upsert,改成一条 SQL UPDATE + 一条 SyncChange bulk insert,10k tx 的
  rename 从 10k 次 ON CONFLICT 降到 1 条 UPDATE。

### 4.5 增量 push 的字段 merge

Mobile `/sync/push` 发来的 payload 可能**只带改的字段**(比如只改 name,
其它字段是 None / 缺失)。不能直接 upsert,否则其它字段会被默认值覆盖。

`sync_applier.merge_with_existing()` 先查现有 projection 行,用旧值补齐
payload 缺失 / None 的字段,再 upsert。

**踩过一次坑**:老代码是 5 个 `_merge_with_existing_<entity>` 函数 copy-paste,
budget 那个函数体内的 `from .models import` 相对路径写错,/sync/push 带
budget 时 500 `ModuleNotFoundError`。现在合并成表驱动的 `_MERGE_SPECS` +
一个 `merge_with_existing`,新增 entity 只加一条 spec,不可能再漏 import
或复制粘贴错字段。

### 4.6 change_id 单调性

`sync_changes.change_id` 是 autoincrement,**严格全局递增**。

- mobile pull 按 `since=<last_cursor>` 拉:cursor 保存在客户端,server
  只返回 `change_id > cursor` 的行
- web 的 `Idempotency-Key` 失败时响应里会带 `latest_change_id`,客户端据此
  决定要不要 refetch

**不要手工 update change_id** 或改成非 autoincrement 的逻辑。

### 4.7 lock_ledger_for_materialize

`/sync/push` 和 `/write/*` 应用 projection 之前都调这个 SQLite advisory lock
按账本加锁,避免两个并发 push 同账本的 rename cascade 交错。

锁粒度是 ledger,不阻塞跨账本并发写。

---

## 5. WebSocket 实时推送

`src/routers/ws.py` 很薄,就是把 WS 连接丢到 `websocket_manager` 的 per-user
注册表里。server 这边不主动检测变化;每次 `_commit_write` 或 /sync/push
commit 完事之后,在 HTTP handler 里 explicitly 调 `websocket_manager.broadcast`
推 `{"type": "sync_change", ...}` 或 `{"type": "profile_change", ...}` 到该 user
的所有活跃连接。

客户端不用 WS 事件直接更新数据,只用它作为"去 pull 一下"的触发信号:
- Mobile: `useSyncSocket` → `_pull(ledgerId)` 增量拉 sync_changes
- Web: `SyncSocketContext` → `useSyncRefresh` 调各 Page 的 refetch

如果 WS 掉线没收到事件,还有 polling fallback(mobile 的 `startPoller` +
web 的 `SyncSocketProvider` 里的 poller)每 30s 主动拉一次补漏。

---

## 6. 常见 debug 路径

### "Mobile 改了数据,web 刷新看不到"

1. **server log 搜 `sync.push.accept`**:确认 push 真到了 server 并被接受
   - 如果有 `sync.push.conflict` 说明 LWW 拒了(通常是 device clock 问题)
   - 如果连 push 请求日志都没有,问题在 mobile 端(本地没推出去)
2. **查 sync_changes 表**:`SELECT * FROM sync_changes WHERE entity_sync_id = '<x>' ORDER BY change_id DESC`
3. **查 projection 表**:`SELECT * FROM read_account_projection WHERE sync_id = '<x>'`
4. **多账本场景特判**:一个 user-global 实体在多个账本的 projection 里都有
   行,现在 list_workspace_accounts 的 dedup 用 `source_change_id` 做
   tiebreaker(不是账本整体 change_id),应该没问题 —— 但新 API 加 dedup
   时要注意这个陷阱。

### "/sync/push 批量 500"

近期加了 try/except 包 apply_change_to_projection,炸哪条会打
`sync.push.apply_failed entity=... sync_id=... payload=...` 加完整 traceback。
直接看终端或管理员日志面板搜 request_id 即可定位。

### "web 写入返回 409 WRITE_CONFLICT"

客户端的 `base_change_id` 落后于 server。两种情况:
- 正常:用户在 web A 改交易同时 mobile 推了 change(多端并发)→ 客户端
  应该重 fetch 后重试
- 异常:`STRICT_BASE_CHANGE_ID` 环境变量打开了(默认关),导致严格比对
  latest change_id

### "mobile 看不到 web 刚改的数据"

1. WS 连接是否活跃?(检查 mobile log `[SyncEngine] WS connected`)
2. `/sync/pull?since=<cursor>` 手动调一下看返回啥
3. `sync_changes.updated_by_device_id`:注意 server 会过滤掉自己 device id
   的 change(避免自己 push 的再被 pull 回来)

---

## 7. 修改这块代码之前的自检清单

- [ ] 改的是哪条路由的逻辑?对应文件是?
  (写入:`routers/write/{entity}.py`;推送:`routers/sync/push.py`;等)
- [ ] 是改共享 helper 还是单 endpoint 行为?前者去 `_shared.py`,后者去对应 entity 文件。
- [ ] 牵涉到 user-global 实体(account/category/tag)吗?ledger_id=0 通道?
- [ ] 有没有新增 entity type?`_MERGE_SPECS` / `_UPSERT_DISPATCH` / 
  `_DELETE_DISPATCH`(在 `sync_applier.py`)三张表都登记了?
- [ ] 改了 payload 字段映射?对应的 spec 加了字段,并且 **mobile 端 /
  web 前端** 也理解这个新字段吗?
- [ ] 跑了 `pytest tests/` 吗?projection 一致性测试全绿?
- [ ] 多账本场景想过吗?单账本能跑不代表多账本不出问题。

---

## 8. 不在本文档范围

- Auth / JWT / refresh token 流程:见 `src/routers/auth.py` 和
  `src/security.py`
- Profile / 头像上传 / AI config 同步:`src/routers/profile.py`
- 管理员用户 / 设备 / 备份 / 健康:`src/routers/admin.py`
- Attachment 上传 / 下载 / 缩略图:`src/routers/attachments.py`

这些都不参与业务数据的 LWW 同步,逻辑相对独立。
