# BeeCount Cloud 双向同步机制设计

## Context

自部署 BeeCount Cloud v2 需要支撑 mobile (Flutter) ↔ server (FastAPI) ↔ web (React) 三端双向同步。历史实现堆了多种路径（全量快照上传 / 名字级联 / Supabase 备份通道等），导致"mobile 改了 web 看不到 / web 改了 mobile 看不到 / 名字改了残留旧值 / 某些字段同步某些不同步"之类反复复现的 bug。

本文档定义**单一权威的双向同步协议**、对每一类"可同步单元"的处理规则、以及扩展新字段（如 mobile 外观设置）时的落地步骤。

---

## 同步单元分类

按"存储特征 + 变更频率 + 隐私级别"，所有可同步数据分为四类：

### T1 · Ledger-scoped 业务实体
- **举例**：transaction、account（保留 legacy ledgerId 兼容，事实上语义正变 user-scoped）、ledger 本身、预算 budget
- **特点**：属于某个具体账本，每个实体有稳定 `syncId`，增删改频繁
- **同步载体**：`sync_changes` 事件日志 + 物化 `ledger_snapshot`

### T2 · User-scoped 业务实体
- **举例**：category、tag、account（目标形态）、user dictionaries
- **特点**：属于用户、跨账本共享，同名实体全局唯一（在用户级别）
- **同步载体**：也是 `sync_changes` 事件日志。mobile 的 `LocalChanges` 用 `ledgerId=0` 作为"user-global"哨兵；`SyncEngine._push(ledger)` 在推送时顺带把 `ledgerId=0` 的未推变更捎走

### T3 · Profile / 偏好配置
- **举例**：用户头像、display_name、主题色、字号缩放、外观设置（亮/暗/跟随系统）、语言、AI 配置、自动记账开关
- **特点**：每用户一份；值稳定、变更不频繁；不需要事件日志，直接 last-write-wins
- **同步载体**：`UserProfile` 表 + `/api/v1/profile/*` REST 端点；推荐带 `*_version` 字段给客户端做幂等对账

### T4 · 二进制文件
- **举例**：交易附件、分类自定义图标、用户头像
- **特点**：体积大、一次写多次读、内容寻址（sha256 去重）
- **同步载体**：`/api/v1/attachments/*` 端点，存储在 `data/` 目录；引用方（tx / category / user profile）只存 `file_id` 和 `sha256`

---

## 协议：`sync_changes` 事件日志 + 物化 snapshot

### 写模型（append-only 事件日志）

所有 T1/T2 变更都落在 `sync_changes` 表：

```
(change_id: bigint autoincrement, ledger_id, entity_type, entity_sync_id,
 action: upsert|delete, payload_json, updated_at, updated_by_device_id,
 updated_by_user_id)
```

两个关键 invariants：

1. **每次 entity CRUD 写一行**（包括级联的二阶变更，比如 tag rename 顺带改 tx 引用 —— 那些 tx 各自再写一行）
2. **SyncChange 行不可变**。修正只靠写新行

### 读模型（物化 snapshot）

Web 的读路径成本敏感（分页、聚合、跨账本过滤），不适合每次重放事件日志。服务端维护一份 `ledger_snapshot`（也是 `sync_changes` 里的一种 `entity_type`，`action=upsert` 最新一行就是当前 snapshot）。写路径结束会调 `_materialize_individual_changes`：读最新 snapshot + change_id 在它之后的 per-entity 变更，merge 出新 snapshot，写回 `sync_changes`。

### 并发

- `pg_advisory_xact_lock(ledger_id)` 在 `_materialize_individual_changes` 和 `_commit_write` 入口首行获取，保证同一账本 write + materialize 串行
- LWW 决定胜者：比较 `(updated_at, device_id)` 元组字典序；device_id 破平局使两个设备独立跑得同样结果
- **客户端时钟钳制**：服务端在 `/sync/push` 接收时把 `incoming_updated_at` 钳到 `min(client, server_now + 5s)`，防止手机时钟快 N 分钟永远"赢"掉 web 正常写

### LWW + 级联 vs ID 反查（二选一对抗 name 漂移）

tx 指向 account / category / tag 的是**名字字符串**（历史原因，mobile 先行的方案）。于是任何 rename 必须在 **每一个引用位点** cascade 改写，否则读出来的 tx 显示老名字。

v2 的解法：**snapshot.items[i] 同时带 `*Name` 和 `*Id`（syncId）**。Server 读接口优先按 `syncId` 反查 snapshot.accounts / categories / tags 里**当前**的 name，id 查不到再 fallback 到 item 里存的 name。这样 rename 不再依赖每个 cascade 环节都到位 —— id 没变，read 永远拿最新名字。

### WS + polling 推到客户端

- `_commit_write` / `/sync/push` 写完 commit 后，通过 `WSManager.broadcast_to_user(owner_user_id)` 推 `{type: 'sync_change', ledgerId, serverCursor, serverTimestamp}`
- 客户端订阅 WS：心跳 25s，失去心跳 45s 重连；`visibilitychange=visible` 时立即 `pullTick()` 补差
- `/sync/pull?since=<cursor>` 兜底 30 秒轮询；cursor 存 localStorage per-user

---

## 协议：Profile / 偏好配置

### 表结构

`UserProfile` 表存所有偏好键值：
```
user_id, display_name, avatar_file_id, avatar_version,
theme_mode, primary_color, font_scale, language, ai_config_json,
auto_bill_enabled, ..., updated_at
```

**核心字段各自带 `*_version: int` 自增**。服务端写入 / 客户端写入都 +1。

### 读

`GET /api/v1/profile/me` 返回完整 profile 的 JSON，包括所有 `*_version`。

### 写

`PATCH /api/v1/profile/me` body 只带要改的字段 + 可选 `base_*_version`。服务端：
1. `SELECT ... FOR UPDATE` 对当前用户加行锁
2. 对每个 body 里出现的字段：若 `base_version` 提供且不等于当前 `version` → 409 + 返回当前值；客户端决定 merge 或覆盖
3. 否则：`version += 1`，写入

### 客户端同步流程

1. 登录 / app resume / WS 收到 `profile_changed` 事件 → 调 `GET /profile/me`
2. 对比服务端各 `*_version` vs 本地缓存的 `*_version`：不等则以服务端为准，写本地 prefs + 触发 UI 刷新
3. 用户改本地 → 乐观更新 UI + 调 `PATCH` → 成功后 bump 本地 `*_version`；失败 409 → 重取再合并

### WS 广播

服务端在 `PATCH /profile/me` 成功 commit 后 `ws_manager.broadcast_to_user(user_id, {type: 'profile_changed', fields: [<改了的字段名>]})`。客户端收到后只重拉 `/profile/me`。

---

## 协议：二进制文件

所有文件通过 `/api/v1/attachments` 端点走：
- `POST /attachments/upload` — multipart，返回 `{file_id, sha256, size}`；服务端按 `sha256` 去重，已存在就只链接不物理重传
- `GET /attachments/{file_id}` — 流式下载；带 `ETag: sha256`，客户端有缓存直接 304

引用端只存 `file_id` + `sha256`。引用数据本身走 T1/T2/T3 的协议同步，文件字节流在**客户端需要时**按需下载。

### 头像（特例）

头像 via `POST /profile/avatar` 封装：服务端内部走 `attachments.upload`，然后把 `avatar_file_id` + bump `avatar_version` 写回 `UserProfile`。客户端对齐 `avatar_version` 发现不同时，按 `GET /attachments/<fileId>` 拉字节，落盘到 `<AppDocs>/avatars/avatar_<ts>.jpg`，写 SharedPreferences。

---

## 同步事件总流水（参考）

### 场景 A：mobile 改交易

```
user taps save
  ↓
LocalRepository.updateTransaction (Drift)
  ↓
ChangeTracker.recordChange(entityType=transaction, ledgerId=L, action=update)
  ↓
PostProcessor.run / sync() debounced trigger
  ↓
SyncEngine._push(L) → 序列化 tx（含 accountId/categoryId/tagIds）+ ledgerId=0
  的 user-global 未推变更 → POST /sync/push
  ↓
server: advisory_lock → LWW accept → write SyncChange(s) → materialize → commit
  → ws.broadcast_to_user({type:'sync_change',ledgerId:L})
  ↓
web: useSyncSocket onEvent → refreshAllSections → fetchWorkspaceTransactions
  → list_workspace_transactions 按 id 反查实体实时 name → 渲染
```

### 场景 B：mobile 改头像

```
user picks image
  ↓
AvatarService.pickAndSaveAvatar → local file + SharedPreferences
  ↓
mine_page._syncAvatarToCloud → providerInstance.uploadMyAvatar(bytes, name)
  ↓
server: /profile/avatar → attachments.upload → UserProfile.avatar_file_id
  update + avatar_version += 1 → ws.broadcast_to_user({type:'profile_changed'})
  ↓
mine_page 本地已经立刻显示新图（乐观）
web: useSyncSocket profile_changed → fetchProfileMe → avatar_url + avatar_version
  → 显示新头像
```

### 场景 C：新设备首登（这份 doc 最重要的场景）

```
login success → cloud provider bootstrap
  ↓
sync_providers 内 fire-and-forget SyncEngine.syncMyProfile()
  ↓
GET /profile/me → avatar_url + avatar_version + display_name + theme 等
  ↓
对每项：local version != remote → pull (含下载 avatar 文件) + 写本地 + bump
  相应 refreshProvider（avatarRefreshProvider / themeModeProvider / ...）
  ↓
然后才走 currentLedgerId 的 SyncEngine.sync(ledgerId) ledger/交易同步
```

**设计要点**：profile 同步**不依赖任何 ledger**。历史上把头像同步塞进 `sync(ledgerId)` 内部，导致没 ledger 的新用户首登拿不到头像。v2 已分离。

---

## 扩展清单：新增一个"可同步字段"要改哪里？

假设要让 mobile 的"外观设置（亮/暗/跟随系统）"在 web 可见、也能从 web 反向覆盖。

### 后端

1. `src/models.py:UserProfile` 加 `theme_mode: str` + `theme_mode_version: int`
2. alembic 迁移加字段
3. `src/schemas.py:UserProfileOut` + `UserProfilePatchRequest` 加 `theme_mode` + `base_theme_mode_version`
4. `src/routers/profile.py:patch_profile_me` 加 `theme_mode` 的 LWW 检查分支，成功后 `ws_manager.broadcast_to_user(... fields:['theme_mode'])`

### Mobile

1. 本地持久化：已有（SharedPreferences 的 theme key）
2. `SyncEngine.syncMyProfile()` 扩展：比对 `theme_mode_version`，不同时覆盖本地、bump `themeModeProvider`
3. 用户在 UI 改主题 → 本地写 + `PATCH /profile/me body={theme_mode, base_theme_mode_version}`；失败 409 → `GET /profile/me` 重同步再 toast 一句"其它设备也改过，已合并"
4. `sync_providers.dart` 在 WS `profile_changed` 事件里把 `themeModeProvider` 一起 bump

### Web

1. `ProfilePanel` 加"外观设置"选项
2. 写：`PATCH /profile/me` 同样路径；失败 409 处理
3. WS onEvent profile_changed → refresh profile → 更新 theme context

### 验证清单

- [ ] mobile 改 → web 2 秒内看到（WS 路径）/ 30 秒内（polling 兜底）
- [ ] web 改 → mobile 下一次 resume / pull 时看到
- [ ] 两端同时改（极罕见）：后写的一方 409，重新 pull 后合并再 PATCH
- [ ] 首登新设备：profile.sync 在 ledger.sync 之前完成；主题 / 语言 / 头像都按服务端值落地

---

## 失败模式 & 兜底

| 故障 | 处理 |
|------|------|
| WS 断连 | 指数回退重连；`/sync/pull?since=cursor` + `/profile/me` 每 30s 轮询兜底 |
| Push 冲突（LWW 拒绝 / IntegrityError） | 客户端 `retryOnConflict` 最多 4 次，每次重取 `base_change_id` |
| Profile 乐观并发冲突 | 服务端返回 409 + 当前值；客户端刷新本地后，把用户本次编辑（如改了 theme 又改了 font_scale 只有一个冲突）的其它字段重新 PATCH |
| 文件上传中断 | sha256 去重 + 续传：客户端重新调 `POST /attachments/upload`；服务端见同一 sha256 直接返回 `{file_id, sha256, size}` 不重写盘 |
| 客户端时钟超前 | 服务端 `min(incoming, server_now + 5s)` 钳制 |
| 实体 id 查不到（tag 被删但 tx 还引用） | read 按 id fallback 到 item 里存的老 name；UI 不爆炸，只是显示历史名字 |
| profile 端点 500 | 不阻塞 app 启动；下一次 resume 再试；toast 兜底 |

---

## Roadmap — 主题色 / 偏好设置同步（T3 扩展清单示例）

**动机**：mobile 端"个性化 → 主题色"改完，web 不会跟着变；未来字号、语言、亮暗模式也是同一类。按本文 T3 配方新增字段的落地步骤如下 —— 复杂度**中等**，独立一两轮工作量可完成。

### 后端

1. `src/models.py:UserProfile` 新增字段：
   ```python
   primary_color: Mapped[str] = mapped_column(String(16), nullable=True)  # e.g. "#F59E0B"
   primary_color_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
   theme_mode: Mapped[str] = mapped_column(String(16), nullable=True)  # "light"|"dark"|"system"
   theme_mode_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
   font_scale: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
   font_scale_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
   ```
2. alembic 0014 加字段（默认值保证老用户不破）。
3. `src/schemas.py`：`UserProfileOut` 加上这些字段 + 各自 `*_version`。`UserProfilePatchRequest` 新增 `primary_color` / `theme_mode` / `font_scale` + `base_*_version` 对应字段。
4. `src/routers/profile.py:patch_profile_me`：每个字段独立 LWW 检查 —— 若请求带 `base_primary_color_version` 且不等于当前 → 409，返回当前值；否则 version +=1 写入。commit 后 `ws_manager.broadcast_to_user(user_id, {type: 'profile_changed', fields: [<写过的字段名>]})`。

### Mobile

1. 本地持久化已有（SharedPreferences `primary_color` / `theme_mode` / `font_scale`）。
2. 新增各自的 `remote_*_version` SP key，记录上一次从服务端拉到的版本号。
3. `SyncEngine.syncMyProfile()` 扩展：
   ```dart
   if (profile.primaryColorVersion != localPrimaryColorVersion) {
     await ThemePrefs.setPrimaryColor(profile.primaryColor);
     await ThemePrefs.setRemotePrimaryColorVersion(profile.primaryColorVersion);
     ref.read(primaryColorProvider.notifier).state = _hexToColor(profile.primaryColor);
   }
   // theme_mode / font_scale 同理
   ```
4. 用户在 UI 改颜色 → 本地写 + `PATCH /profile/me body={primary_color, base_primary_color_version}`；成功后 `local_version = server_returned_version`；409 → `syncMyProfile()` 拉一次合并，提示"其它设备也改过"。
5. WS `profile_changed` 事件 → `syncMyProfile()` 整体拉一次（幂等，按 version 去重）。

### Web

1. `packages/ui/src/theme` 支持 `primary_color` CSS 变量（目前 tokens 里 `--primary` 是 HSL tuple，改颜色要换 CSS 变量值）。用 `document.documentElement.style.setProperty('--primary', hexToHsl(color))`。
2. `AppPage` 里 profile 加载成功后，读 `profileMe.primary_color`，调 `applyPrimaryColor(profileMe.primary_color)`。
3. WS `profile_changed` → refreshProfile → 重新 `applyPrimaryColor`。
4. settings-profile 页面新增"主题色"色盘 —— 用户点击颜色 → `PATCH /profile/me`。

### 复杂度评估

| 部分 | 工作量 | 风险 |
|------|--------|------|
| 后端 schema + 端点 | 小 | 低（加字段 + 4 个字段级 LWW 分支）|
| Mobile syncMyProfile 扩展 | 小 | 低（已有骨架）|
| Web CSS 变量 hex→hsl 转换 + 实时应用 | 中 | 中（影响全局配色，需要全量回归视觉） |
| 冲突处理 UX（两端同时改） | 中 | 中（错误态提示） |

总计约 2–3 天工作量；分两轮推（后端+mobile 一轮，web+UX 一轮）。本轮不做。

---

## 当前状态快照（2026-04-17）

| 条目 | 状态 |
|------|------|
| T1 tx / account / category / tag 的 push → materialize → WS → web | ✓ |
| materialize merge（非整体替换）保留未变字段 | ✓ |
| tx 按 id 反查实体 name（read 侧） | ✓ |
| mobile tx push payload 带 accountId/categoryId/tagIds | ✓ |
| mobile user-scoped tag/category push（ledgerId=0 捎带） | ✓ |
| profile avatar: upload + GET + 客户端按 avatar_version 去重下载 | ✓ |
| profile avatar 首登不依赖 ledger（独立 bootstrap） | ✓ |
| profile display_name / theme / font_scale / language / 其它偏好 | **未实现**（本文 roadmap） |
| WS `profile_changed` 广播 | **未实现**（本文 roadmap） |
| 配置端点的冲突处理（base_*_version + 409） | **未实现**（本文 roadmap） |

---

## 参考代码位置

- 协议实现：`src/routers/sync.py` · `src/routers/write.py` · `src/routers/read.py` · `src/snapshot_mutator.py`
- Mobile 同步引擎：`lib/cloud/sync/sync_engine.dart` · `lib/cloud/sync/change_tracker.dart` · `lib/cloud/sync/entity_serializer.dart`
- Mobile profile 端：`lib/providers/sync_providers.dart:syncMyProfile` · `lib/services/ui/avatar_service.dart`
- Web WS + polling：`frontend/apps/web/src/hooks/useSyncSocket.ts` · `frontend/apps/web/src/state/sync-client.ts`
- Web read consumers：`frontend/apps/web/src/pages/AppPage.tsx:refreshSectionData` · `refreshAllSections`
