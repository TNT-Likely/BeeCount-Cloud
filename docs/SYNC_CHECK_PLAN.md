# BeeCount Cloud v2 — 同步可靠性验证计划

## 目的

单设备 + web 的双向同步已经跑通。但还有三类场景没有系统性验证：
1. **离线恢复** — 一方（mobile 或 web）断网期间写入的变更，恢复后能不能追上
2. **多设备并发** — 两台手机 + web 三方写同一数据时，最终一致性能否保证
3. **旧备份路径隔离** — Cloud v2 的改造有没有污染原来 iCloud / WebDAV / S3 / Supabase 这几条文件备份通道

本文给每类场景定好可执行的验证步骤 + 观察点，用完可以直接对着跑。

---

## 架构摘要（源码已核对，下列引用行号来自 2026-04-18 master）

**Mobile 写入路径**
- `AppMode.local` + `CloudBackendType.beecountCloud`：`LocalRepository` 注入 `ChangeTracker`（`lib/providers/database_providers.dart:35-40`），写 Drift 同时记 `local_changes`
- 其它 backend 类型（icloud/webdav/s3/supabase）：`tracker = null`（line 36 三元表达式），**根本不记 local_changes**，`SyncEngine` 也不会被构造（`sync_providers.dart:148`），整条新路径对旧用户完全 dead code
- `SyncEngine._push()` 只拉 `pushed_at IS NULL` 的行 POST `/sync/push`；收到 WS `sync_change` 触发 `_pull()`，`onAutoPullCompleted` 把各 refresh tick +1（`sync_providers.dart:166-185`）

**Web 写入路径 —— 和 mobile 不是同一套协议**
- Web 用 `/write/ledgers/{id}/transactions` 等**个体 REST 写**（`frontend/packages/api-client/src/write.ts`），payload 带 `base_change_id`
- 服务端 `src/routers/write.py:293-328` 的 `_commit_write()`：`strict_base_change_id` 默认 OFF（`src/config.py:45`），所以 web 的 `base_change_id` 被**忽略**，写入直接针对 LATEST snapshot，靠 `_emit_entity_diffs` 里的 LWW 判冲突
- **Web `state/sync-client.ts` 是 pull-only**（133 行全文没有一个 `POST` / `PATCH`）——所以 web 并不走 `/sync/push` 那条批量 push 协议
- 每 30s `startPoller` 调 `drainPull` 兜底，外加 `useSyncSocket` 的指数退避重连（`hooks/useSyncSocket.ts:53,98`）

**服务端冲突策略（mobile + web 两条入口共享）**
- `src/routers/sync.py:292-295` **客户端时钟钳位**：`incoming_updated_at = min(raw, server_now + 5s)`
- `sync.py:307-317` **LWW + device_id tie-break**：`(updated_at, device_id)` 字典序比较
- web `/write/*` 在 `write.py:_emit_entity_diffs` 做类似的 entity 级 LWW
- 成功后：`sync.py:393-409`（mobile push）和 `write.py:425,809,940`（web write）都广播 WS `sync_change`

**关键差异**
| 项 | Mobile | Web |
|---|---|---|
| 写 endpoint | `/sync/push`（批量） | `/write/*`（个体） |
| 离线队列 | `local_changes` 表 Drift 持久化 | **无**（查看本章 "A2 现状" 再做） |
| 冲突解决 | `(updated_at, device_id)` LWW | 同，共享 sync_changes 表 |
| 实时接收 | `SyncEngine.startListeningRealtime` | `useSyncSocket` + 30s poll |

**旧备份路径隔离（已 grep 确认）**
- `SyncEngine` 只出现在：`lib/cloud/sync/`、`lib/providers/sync_providers.dart`、`lib/app.dart`、`lib/pages/main/mine_page.dart`、`lib/services/billing/post_processor.dart`
- `flutter_cloud_sync_icloud` / `_webdav` / `_s3` / `_supabase` 四个包的 `lib/` **零引用** `BeeCountCloudProvider` 或 `SyncEngine`
- 反向也验证过：`beecount_cloud_provider.dart` 不 import 四个旧 provider

---

## 场景 A — 单端离线恢复

### A1. Mobile 离线写入，恢复后 push 追上

**前置**：mobile + web 同账号都在线，数据一致。

**步骤**：
1. mobile 开飞行模式
2. mobile 连续新建 3 笔 tx、改 1 个分类名、删 1 个账户
3. web 保持在线不动，观察不到任何变化（预期）
4. mobile 关飞行模式
5. 观察 mobile 日志 `[SyncEngine] push: 推送 N 条变更`
6. web 在 2 秒内（WS 路径）应看到 5 条变更同步完成

**预期**：
- web 上出现 3 笔新 tx、分类名更新、账户消失
- `sync_changes` 表增加 5 条（SQL: `SELECT count(*) FROM sync_changes WHERE user_id=<u> AND created_at > <离线开始时间>`）
- mobile `local_changes` 表里对应的 5 行 `pushed_at IS NOT NULL`

**观察点 & 日志**：
- mobile：`lib/cloud/sync/sync_engine.dart:_push` 返回的 `pushed` 计数 ≥ 5
- server：`/sync/push` accepted=5、rejected=0、conflict_count=0
- 如果看到 `accepted<5` 说明 client 发的 payload 有 entity 在 server 上已经比本地"更新"——对 LWW 来说合理，但需要记录具体是哪一条（检查 `conflict_samples` 数组）

### A2（舍弃）— Web 离线写入

**为什么舍弃**：Web 触达不到 server 的场景（不管是自己断网、还是 server 宕机），**症状完全一样** —— `/write/*` 请求直接 fail，toast 报错，表单不丢。A3 server 重启期间 web 写一遍就覆盖了这条验证路径。

**不放 checklist，但保留为"已知限制记录"**：
- Web 端没有离线写队列（`sync-client.ts` 133 行全是 pull，无 push 路径）
- 任何 `/write/*` 失败就是当场抛错 + 显示在 UI 上，不会静默丢
- 恢复网络后用户需**手动重试提交**
- 是否补 IndexedDB / service worker 队列是 roadmap 话题

### A3. Server 重启（兼测 web 写失败 UX）

**步骤**：
1. mobile、web 在线，数据一致
2. `make dev-api` 停掉服务端
3. **Web 端尝试写 2 笔 tx** → 预期：`/write/*` 直接失败，每次都弹错误 toast，表单不丢、不静默成功
4. **Mobile 端写 2 笔 tx** → 预期：本地 Drift 写入成功（ChangeTracker 记 local_changes），UI 正常显示，后台 push 持续失败但不打扰用户
5. 10 分钟后重启服务端
6. 两端都会重连 WS（`useSyncSocket` + mobile 端指数退避）
7. 观察：
   - Mobile 借"ws_connected → auto sync"把 2 条 local_changes push 上去
   - Web 再次写入恢复正常（不再报错）
   - 双方 UI 数据一致：mobile 两条新 tx 出现在 web 上

**预期**：
- 最终 server `sync_changes` 增 2 条（只来自 mobile —— web 那 2 笔因全程无队列，确实丢掉了，用户看到错误提示后自己决定重输）
- 双端 UI 和 server 一致

**观察点**：
- Server 启动头几秒会有 mobile push 的 spike（可看 `sync_push_total` metric）
- Web 端 4 次写请求应是 4 次 error toast，零静默成功
- 如果 web 表单清空了 / UI 看起来成功了 → 是 bug（"假成功"）
- 如果 mobile 那 2 条 push 丢了 → 是 bug（`local_changes.pushed_at` 没正确 nullable 或 auto sync 没触发）

### A4. Mobile 离线 + Web 在线写，mobile 恢复后拉下来

**这是 A1 的对偶场景**：A1 测的是 mobile 离线 → 恢复后 **push**；这里测 mobile 离线期间 web 一直写，mobile 恢复后能不能 **pull** 拿到全部 backlog。

**前置**：mobile + web 都在线数据一致。

**步骤**：
1. mobile 开飞行模式
2. web 新建 5 笔 tx、改 1 个分类名、删 1 个账户
3. mobile 保持飞行模式 ~5 分钟（模拟真实的"手机在口袋里"场景）
4. mobile 关飞行模式 → **不手动操作任何东西**
5. 观察 mobile 日志：
   - `SyncEngine: auto sync 触发 (reason=ws_connected|connectivity_restored)` — 新加的 auto sync 起作用
   - `sync/pull since=<old_cursor>` 返回 7 条变更
   - `pull: 应用 7 条远程变更`
6. mobile UI 2-3 秒内显示：5 笔新 tx、分类新名字、账户消失

**预期**：
- 最终 mobile Drift 和 server snapshot / web UI 完全一致
- 如果 mobile 飞行模式时也有写入 → 本测试变成 A1 + A4 叠加，双向都应过

**观察点 / 日志**：
- mobile 的 `provider.pullChanges` 发的 `since=<cursor>`：cursor 应该是离线前最后一条 sync_change 的 id
- server `/sync/pull` 响应里 `changes.length=7, has_more=false`
- 如果 `changes` 为空但 server 数据库明明有 7 条 → 说明 cursor 不对（客户端没正确 persist 或上次 pull 没写 cursor），是 bug
- 特别注意 `_applyRemoteChange`（`sync_engine.dart:_pull` 内）返回 false 的条数 —— "echo 过滤"命中的行属正常，但全 0 要怀疑

### A5（舍弃）— Web 离线 / mobile 在线写 / web 恢复后 pull

**为什么舍弃**：这条路径的实质是"web 端 WS 重连 + drainPull 按 cursor 拉"。日常使用里每次开 / 切 / 刷新标签都会跑一遍，不需要专门压测。真要坏了日常用就能发现。

Mobile 那边（A4）不能省——mobile 作为会长时间离线的客户端，offline queue + Drift 本地持久化 + cursor 的交互比 web 复杂得多，风险集中在那边。

---

## 场景 B — 多设备并发

### B1. 两 mobile + web，非冲突写入

**前置**：3 端都在线同账号。

**步骤**：
1. Phone A 新建 tx1（金额 10）
2. Phone B 新建 tx2（金额 20）
3. Web 新建 tx3（金额 30）
4. 三个操作尽量同时（±1 秒内）

**预期**：3 端最终都看到 3 笔 tx，server `sync_changes` 增加 3 条，没有 conflict。

**观察点**：
- server accepted=3, conflict_count=0
- `server_cursor` 在三端的 `drainPull` 返回值里相同

### B2. 两 mobile 同时改同一条 tx — /sync/push LWW

**前置**：3 端都在线，已经有一条 tx1。

**步骤**：
1. Phone A 把 tx1 金额改成 100
2. Phone B 同一秒内把 tx1 金额改成 200（操作间隔 < 1s）
3. Phone A 先到 server、Phone B 后到（网络快慢难控，可重复几次观察两种到达顺序）

**预期**：
- `sync.py:307-317` 按 `(updated_at, device_id)` 字典序选赢家
- `updated_at` 更晚的赢，**到达顺序不影响结果**
- 两 Phone + web 最终一致

**观察点**：
- server `/sync/push` 响应里的 `accepted` / `rejected` / `conflict_count`
- rejected 的那次：`conflict_samples` 里含 `reason: lww_rejected_older_change`
- 如果两次 `updated_at` 毫秒级相同，`device_id` 字符串字典序较大的赢（行为可复现）

### B2b. Web 和 mobile 同时改同一条 tx — 跨协议冲突

**这是 B2 的变体，测两套协议交汇**：

**步骤**：
1. Phone A 改 tx1 金额 = 100（走 `/sync/push`）
2. Web 1 秒内改 tx1 金额 = 200（走 `/write/ledgers/.../transactions/...` PATCH）
3. 两条路径都最终落到 `sync_changes` 表

**预期**：
- 谁后到 / `updated_at` 更晚谁赢
- Web 的 `base_change_id` 被忽略（strict 默认 OFF），不会 409
- 两端 UI 和 server snapshot 最终一致

**观察点**：
- server 日志里两条路径都广播 WS，两个客户端都应收到两次 `sync_change`
- 如果 web 端 "刚刚改了" 立刻被 mobile 覆盖（看起来"白改了"）是**预期行为**，不是 bug

### B3. 两 mobile 时钟偏移

**这是 `src/routers/sync.py:292-295` 那条防线要抵御的场景。**

**步骤**：
1. Phone A 系统时钟故意调前 1 小时（模拟用户错乱的本地时钟）
2. Phone A 改 tx1 金额为 777
3. 正常 Web 改 tx1 金额为 999
4. 哪边后改？

**预期**：
- Phone A 的 `updated_at` 被 server clamp 到 `server_now + 5s`，**不再自动压倒** web 的更新
- Web 的 999 赢

**观察点**：server 端日志如果没有 `clamp hit` 日志，需要临时 log 一下 `raw_updated_at > max_allowed` 的次数来确认 clamp 生效了。

### B4. 竞争下的标签 / 分类改名

**步骤**：
1. Phone A 把 "餐饮" 标签改名 "餐饮1"
2. Phone B 把同一标签改名 "餐饮2"
3. 两次 push 几乎同时

**预期**：
- LWW 最终选一个赢家（B 在后，赢）
- 两端及 web 上的 `tags` 表都显示 "餐饮2"
- **关键**：所有关联该标签的 tx，展示名也跟着变
- 这依赖 `src/routers/read.py:list_workspace_transactions` 里按 tag syncId 反查当前 tag name 的逻辑（早先那轮 P1.1 做过），而不是 tx 里存的 comma-string 静态名。

**观察点**：
- web 交易列表里旧"餐饮"的 tx 显示是不是 "餐饮2"
- 如果还是 "餐饮1"、"餐饮"，说明：(a) cascade rewrite 没跑；(b) read 路径没按 id 反查。两条都跑过了，但值得压一次。

### B5. 账本级别的 2 端并发

**步骤**：
1. Phone A 删账本 L1
2. Phone B 在同一 L1 下新建 tx（不知道 A 要删）

**预期 / 已知行为**：
- 如果 A 的删除先到 → L1 被软删，B 的新 tx push 会被 server 怎么处理？
- 建议检查 `get_accessible_ledger_by_external_id` 对软删账本的返回值：要么 404 拒绝，要么自动 reactivate。文档化当前行为。

---

## 场景 C — 旧备份路径隔离验证

### C1. 证实新旧代码完全分流（已核对 ✅）

**分流点 A**：`lib/providers/database_providers.dart:35-40` —— `ChangeTracker` 只在 `beecountCloud` 类型下注入，其它 backend 下 tracker 为 null，所有写入**不进** `local_changes` 表。

**分流点 B**：`lib/providers/sync_providers.dart:148` —— `SyncEngine` 只在 `beecountCloud` 分支构造，其它分支走 `TransactionsSyncManager` / `LocalOnlySyncService`。

**已做过的 grep 验证**（源码核对结果）：
- `SyncEngine` 共 6 个出现点，全在：`lib/cloud/sync/` / `providers/sync_providers.dart` / `lib/app.dart` / `lib/pages/main/mine_page.dart` / `lib/services/billing/post_processor.dart`
- `flutter_cloud_sync_icloud|_webdav|_s3|_supabase` 四个包的 `lib/` 零引用 `BeeCountCloudProvider` / `SyncEngine`
- `beecount_cloud_provider.dart` 不反向 import 四个旧 provider

**本地二次确认命令**（任何时候都可重跑）：
```bash
# Mobile 侧隔离验证
grep -rln 'SyncEngine\|BeeCountCloudProvider' \
  packages/flutter_cloud_sync_icloud packages/flutter_cloud_sync_webdav \
  packages/flutter_cloud_sync_s3 packages/flutter_cloud_sync_supabase
# 输出应为空
```

### C2. iCloud 模式回归测试

**前置**：iOS 真机 / 模拟器，切到 `AppMode.local` + 云端类型 iCloud。

**步骤**：
1. 登录 iCloud
2. 在旧流程导出一份快照到 iCloud Drive
3. 换台设备 / 清 app data 后重登
4. 从 iCloud 恢复快照

**预期**：
- 整个流程不触碰 `/sync/push`、`BeeCountCloudProvider`、`SyncEngine`
- 日志里没有 `[SyncEngine]` 前缀
- 成功恢复数据 = 旧路径没坏

**要验证的 diff 点**：
- `lib/services/cloud/` 目录：应该还是老实现，没被 sync v2 影响
- `lib/cloud/transactions_sync_manager.dart`：快照上传 / 下载入口没动
- 包 `flutter_cloud_sync_supabase` / `flutter_cloud_sync_webdav` / `flutter_cloud_sync_s3`：packages 目录独立，依赖关系没被反向污染
  （`grep -r 'flutter_cloud_sync/'` 的结果里不应该出现这几个 package 的 import）

### C3. WebDAV / S3 / Supabase 三条路径的烟囱测试

对每个后端跑一次：
1. 切到该后端
2. 新建一笔 tx
3. 老的 `TransactionsSyncManager` 应该把整库快照上传
4. 切到别的设备 → 从该后端恢复 → 数据一致

**观察点**：
- 服务端 `sync_changes` 表不增长（用的不是 BeeCount Cloud）
- 上传的文件落在对应云 bucket / WebDAV 目录 / Supabase storage
- 日志里前缀不是 `[SyncEngine]`，而是 `[TransactionsSyncManager]` 或对应 provider 名

### C4. 在同一用户下混用两种模式

**这是最容易踩雷的场景**。

**步骤**：
1. 用 BeeCount Cloud 登录，写了一批 tx（已经在服务端 `sync_changes` 里了）
2. 切到 iCloud 模式，导出快照（快照应该包含 Drift 里所有 tx，不管它们来自哪个 provider）
3. 清 app data
4. 登回 BeeCount Cloud

**预期**：
- 新登录会把 server 上的 `sync_changes` 全拉下来 → Drift 恢复
- iCloud 里的快照也能正常导入（且不会与 server 冲突，因为 Drift 级别的重复 syncId 会被 `local_changes` upsert 处理）

**风险点**：
- 如果 iCloud 快照里带有旧 syncId，导入后又触发 `_push` 把"已经在 server 存在的同 syncId" 再推一次——根据 LWW 规则应该被 reject，但值得看 server 是否记 audit log。

---

## 检查清单（执行时打勾）

| 类别 | ID | 场景 | 优先级 | 通过 |
|------|----|------|--------|------|
| 离线 | A1 | Mobile 离线 + 5 变更 + 恢复 push | 🔴 P0 | ☐ |
| 离线 | ~~A2~~ | ~~Web 离线写入~~（A3 已覆盖 web 视角，舍弃） | — | — |
| 离线 | A3 | Server 重启恢复（含 web 写失败 UX） | 🟡 P1 | ☐ |
| 离线 | A4 | Mobile 离线 + web 写 → mobile 恢复 pull | 🔴 P0 | ☐ |
| 离线 | ~~A5~~ | ~~Web 离线 + mobile 写 → web 恢复 pull~~（日常覆盖，舍弃） | — | — |
| 并发 | B1 | 3 端各写 1 笔 tx | 🔴 P0 | ☐ |
| 并发 | B2 | 2 mobile 同时改同 tx，LWW 生效 | 🔴 P0 | ☐ |
| 并发 | B2b | Web + mobile 跨协议冲突 | 🔴 P0 | ☐ |
| 并发 | B3 | 时钟偏移 clamp 防御 | 🟢 P2 | ☐ |
| 并发 | B4 | 2 端同时改同 tag 名 + 级联 | 🟡 P1 | ☐ |
| 并发 | B5 | 账本删除 + 同账本下新增并发 | 🟢 P2 | ☐ |
| 隔离 | C1 | 新旧代码分流（已 grep ✅ 执行时复核） | 🔴 P0 | ☐ |
| 隔离 | C2 | iCloud 快照备份 / 恢复不坏 | 🔴 P0 | ☐ |
| 隔离 | C3 | WebDAV / S3 / Supabase 各跑一次 | 🟡 P1 | ☐ |
| 隔离 | C4 | 两种模式混用 | 🟢 P2 | ☐ |

---

## 执行前准备

1. 打开 `src/routers/sync.py:399-409` 那段 broadcast 的 `metrics.inc("beecount_sync_push_*")` 统计，跑之前记录基线
2. mobile 打开"开发者日志"，grep `SyncEngine` / `ChangeTracker` / `avatar_sync`
3. web DevTools Network 打开 "Preserve log"，方便回看 WS 消息
4. DB 端准备 SQL：
   ```sql
   SELECT change_id, entity_type, entity_sync_id, updated_at, updated_by_device_id
   FROM sync_changes
   WHERE user_id = '<user_id>' ORDER BY change_id DESC LIMIT 50;
   ```

## 已知事实 / 执行前确认

**已确认的限制（不是 bug，是设计取舍）**：
- A2：web 没有离线写队列 —— 离线时提交应当**明确报错**，不做 SW / IndexedDB 队列
- web 的 `base_change_id` 被忽略（`strict_base_change_id=False` 默认）—— 和 mobile 共享 LWW

**已确认的隔离性**：
- C1 的 grep 已在本文撰写时跑过，结果干净。未来任何引入 beecount_cloud 到旧 provider 包的 PR 都应该 block

**历史易错点（重点复测）**：
- B4 tag 改名级联 —— 历史上多次 bug，靠 `list_workspace_transactions` 的 id-based 反查解决，压一遍
- avatar 同步刚做过根因修复（`fix: 头像初次同步根因`），借这次跑通全链路日志确认

**需要注意的不可靠来源**：
- Mobile 系统时钟：测 B3 时确保系统时钟真的被调前（不是应用层模拟）
- 浏览器 network panel "Offline" 对 WebSocket 生效；service worker 可能还活着，偶尔缓存会绕过 Offline

---

## 报告格式

每条 checklist 验证完填：
- 测试时间 + 设备信息
- 通过 / 不通过
- 如果不通过：截图 + 日志片段 + 最短复现步骤
- 附录：`sync_changes` 相关行的 SQL 导出
