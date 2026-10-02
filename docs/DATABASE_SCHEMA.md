# 資料庫 Schema 說明（維運參考）

> 本文件對照 [`src/models.py`](../src/models.py) 逐表整理，目的是讓維運人員在看 DB 時，能快速知道「這張表是做什麼的」「這個欄位代表什麼」。
> 欄位型別為 SQLAlchemy 型別（對應到 Postgres/SQLite 實際欄位型別大致為：`String/Text`→varchar/text、`Integer/BigInteger`→整數、`Float`→浮點數、`Boolean`→布林、`DateTime(timezone=True)`→帶時區時間戳、`Date`→日期、`JSON`→json）。
> 「屬性」欄位標註：`PK`=主鍵、`FK→表.欄位`=外鍵、`nullable`=可為空、`unique`=唯一、`index`=有索引、`default`=應用層預設值、`server_default`=資料庫層預設值。
>
> 架構背景請先看 [`CLAUDE.md`](../CLAUDE.md) 與 [`SYNC_ARCHITECTURE.md`](./SYNC_ARCHITECTURE.md)。三個容易混淆的概念先澄清：
> 1. **`ledger_snapshot` 不是一張實體資料表**，而是 `sync_changes.entity_type` 欄位的其中一個值（協定層的邏輯快照，用來給 mobile 端 `/sync/full` 首次同步/重裝用），新程式碼禁止再主動寫入。
> 2. **對帳 / 延後入帳不是獨立表**，只是 `read_tx_projection` 上的 `deferred_posting_at`、`reconciled_at` 兩個欄位。
> 3. 借還款餘額、分期已繳期數、專案花費彙總、信用卡回饋金額、**股票持股（股數/平均成本/已實現損益）**等「彙總數字」都**不落表**，是讀取時即時從交易反查加總算出，避免多處聯動重算造成資料漂移。

---

## 目錄

1. [使用者 / 認證 / 裝置](#1-使用者--認證--裝置)
2. [帳本 / 成員 / 邀請](#2-帳本--成員--邀請)
3. [同步事件流（sync_changes 家族）](#3-同步事件流sync_changes-家族)
4. [帳本層級讀取投影表（Ledger-Scoped Read Projections）](#4-帳本層級讀取投影表ledger-scoped-read-projections)
5. [使用者全域投影表（User-Global Projections）](#5-使用者全域投影表user-global-projections)
6. [附件 / 備份快照 / 稽核 / 通知](#6-附件--備份快照--稽核--通知)
7. [信用卡紅利回饋入帳台帳](#7-信用卡紅利回饋入帳台帳)
8. [匯率快取](#8-匯率快取)
9. [備份系統（rclone 多遠端加密備份）](#9-備份系統rclone-多遠端加密備份)
10. [背景排程管理](#10-背景排程管理)
11. [授權金鑰](#11-授權金鑰)
12. [股票持股（3.7.0）](#12-股票持股370)

---

## 1. 使用者 / 認證 / 裝置

### `users` — 使用者主表（User）
帳號主表，含密碼登入、雙因子驗證（2FA/TOTP）、SSO 單一登入三種登入方式的狀態。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 使用者 ID（UUID） |
| email | String(255) | unique, index | 登入信箱 |
| password_hash | String(255) | not null | 密碼雜湊值 |
| is_admin | Boolean | default False, index | 是否為後台管理員 |
| is_enabled | Boolean | default True, index | 帳號是否啟用（停用後無法登入） |
| created_at | DateTime | default 建立時 | 帳號建立時間 |
| totp_secret_encrypted | Text | nullable | 2FA 密鑰（加密存），null=尚未啟用/未完成設定 |
| totp_enabled | Boolean | default False | 2FA 是否已確認啟用 |
| totp_enabled_at | DateTime | nullable | 2FA 啟用時間 |
| sso_subject | String(255) | unique, nullable, index | SSO(OIDC) 身分識別碼，null=從未透過 SSO 登入 |

### `recovery_codes` — 2FA 復原碼（RecoveryCode）
啟用 2FA 時一次產生 10 組一次性復原碼，供使用者遺失裝置時登入救援用。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| code_hash | String(64) | not null | 復原碼 sha256 雜湊 |
| used_at | DateTime | nullable | 使用時間，null=尚未使用 |
| created_at | DateTime | default 建立時 | 建立時間 |

### `user_profiles` — 使用者個人化設定（UserProfile）
一對一對應 `users`，存顯示名稱、頭像、外觀主題、AI 功能設定、本位幣、SwipeSmart API Key 等個人偏好。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), unique, index | 對應使用者 |
| display_name | String(64) | nullable | 顯示名稱 |
| avatar_file_id | String(128) | nullable | 頭像檔案 ID |
| avatar_version | Integer | default 0 | 頭像版本號（快取失效用） |
| income_is_red | Boolean | nullable, default True | 收支顏色方案：True=紅收綠支，False=紅支綠收（中式會計習慣） |
| theme_primary_color | String(7) | nullable | 主題色 hex 色碼（如 `#RRGGBB`） |
| appearance_json | Text | nullable | 外觀設定 JSON（頭部裝飾樣式、金額緊湊顯示、是否顯示交易時間等） |
| ai_config_json | Text | nullable | AI 功能設定 JSON（服務商、能力綁定、自訂提示詞、策略、帳單辨識開關等），含敏感 API key |
| primary_currency | String(16) | nullable | 本位幣 ISO 代碼，null=由客戶端自行初始化 |
| swipesmart_api_key_encrypted | Text | nullable | SwipeSmart 個人 API Key（加密存），不透過同步機制傳到其他裝置 |
| updated_at | DateTime | default 更新時 | 最後更新時間 |

### `refresh_tokens` — 登入刷新權杖（RefreshToken）
使用者登入後用來換發新 access token 的長效權杖，綁定登入裝置。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 權杖 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| device_id | String(36) | nullable, index | 綁定的裝置 ID |
| token_hash | String(128) | unique, index | 權杖雜湊值 |
| expires_at | DateTime | not null, index | 過期時間 |
| revoked_at | DateTime | nullable, index | 撤銷時間，null=尚未撤銷 |
| created_at | DateTime | default 建立時 | 建立時間 |

每次 `/auth/refresh` 都會 rotate（舊列標 `revoked_at`、新增一列新的），舊列本身不會被刪除。`refresh_token_retention` 排程 job（`services/scheduled_jobs.py`，預設 24 小時一次）會清掉失效（已撤銷或已過期）超過 2 天的舊列，`expires_at`/`revoked_at` 的 index 就是給這個排程查詢用的。

### `personal_access_tokens` — 個人存取權杖（PersonalAccessToken）
專供外部 LLM 客戶端（如 Claude Desktop、Cursor、Cline）透過 MCP 協定存取帳本資料使用的長效權杖，與一般登入 access/refresh token 完全獨立、可單獨撤銷。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 權杖 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| name | String(128) | not null | 使用者自訂名稱（如「Claude Desktop」） |
| token_hash | String(128) | unique, index | 權杖 sha256 雜湊（明文只在建立時顯示一次） |
| prefix | String(32) | index | 權杖明文前 16 字元，供列表展示辨識用 |
| scopes_json | Text | default `[]` | 授權範圍 JSON 陣列，如 `["mcp:read"]`/`["mcp:write"]` |
| expires_at | DateTime | nullable | 過期時間，null=永久有效 |
| last_used_at | DateTime | nullable | 最後使用時間 |
| last_used_ip | String(64) | nullable | 最後使用來源 IP |
| revoked_at | DateTime | nullable, index | 撤銷時間 |
| created_at | DateTime | default 建立時 | 建立時間 |

索引：`ix_pat_user_active(user_id, revoked_at)`

### `mcp_call_logs` — MCP 呼叫紀錄（MCPCallLog）
每次外部 LLM 透過 MCP 協定呼叫工具的審計記錄，供使用者在 Web 設定頁查看「呼叫歷史」。**不記錄**完整參數/結果內容（避免洩漏交易備註等隱私），只存統計用元資料。保留 30 天後由排程自動清除。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| pat_id | String(36) | FK→personal_access_tokens.id (SET NULL), nullable, index | 對應的個人存取權杖，權杖刪除後此欄位設為 NULL 但紀錄保留 |
| pat_prefix | String(32) | nullable | 快取的權杖前綴，供權杖刪除後仍可顯示來源 |
| pat_name | String(128) | nullable | 快取的權杖命名，供權杖改名/刪除後仍可辨識呼叫方 |
| tool_name | String(64) | index | 被呼叫的工具名稱 |
| status | String(16) | index | 呼叫結果：`ok`（成功）/`error`（失敗） |
| error_message | Text | nullable | 失敗時的錯誤訊息（截斷至 500 字） |
| args_summary | Text | nullable | 參數脫敏摘要（截斷至 200 字） |
| duration_ms | Integer | default 0 | 呼叫耗時（毫秒） |
| client_ip | String(64) | nullable | 呼叫來源 IP |
| called_at | DateTime | default 呼叫時, index | 呼叫時間 |

索引：`ix_mcp_call_user_time(user_id, called_at desc)`

### `devices` — 登入裝置（Device）
使用者登入過的裝置清單，供「多裝置管理／遠端登出」功能使用。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 裝置 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| name | String(128) | default "Unknown Device" | 裝置名稱 |
| platform | String(32) | default "unknown" | 平台（ios/android/web 等） |
| app_version | String(64) | nullable | App 版本 |
| os_version | String(64) | nullable | 作業系統版本 |
| device_model | String(128) | nullable | 裝置型號 |
| last_ip | String(64) | nullable | 最後使用來源 IP |
| last_seen_at | DateTime | default 建立時, index | 最後活動時間 |
| revoked_at | DateTime | nullable, index | 撤銷（登出此裝置）時間 |
| created_at | DateTime | default 建立時 | 建立時間 |

---

## 2. 帳本 / 成員 / 邀請

### `ledgers` — 帳本主表（Ledger）
每一本帳（記帳本）的基本資料。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 帳本 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 建立者（擁有者） |
| external_id | String(128) | index | mobile 端本地帳本 ID（同步比對用） |
| name | String(255) | nullable | 帳本名稱 |
| currency | String(16) | default "CNY" | 帳本本位幣 |
| month_start_day | Integer | default 1 | 自訂每月起始日（1-28），統計/預算依此聚合，1=自然月 |
| created_at | DateTime | default 建立時 | 建立時間 |

唯一約束：`uq_ledgers_user_external(user_id, external_id)`

### `ledger_members` — 帳本成員（LedgerMember）
多人協作帳本的成員與角色。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 帳本 ID |
| user_id | String(36) | FK→users.id (CASCADE), PK(複合) | 成員使用者 ID |
| role | String(16) | not null | 角色：`owner`（擁有者）/`editor`（編輯者）；`viewer`（唯讀）為預留 |
| invited_by | String(36) | FK→users.id (SET NULL), nullable | 邀請人 |
| joined_at | DateTime | default 加入時 | 加入時間 |

索引：`ix_ledger_members_user_id`、`ix_ledger_members_ledger_id`

### `ledger_invites` — 帳本邀請碼（LedgerInvite）
帳本共享用的邀請碼。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| code | String(8) | PK | 6 位邀請碼（排除易混淆字元 O/0/I/1） |
| ledger_id | String(36) | FK→ledgers.id (CASCADE), index | 對應帳本 |
| invited_by | String(36) | FK→users.id (CASCADE), not null | 邀請人 |
| target_role | String(16) | not null | 邀請成功後授予的角色 |
| expires_at | DateTime | index, not null | 邀請碼過期時間 |
| used_at | DateTime | nullable | 使用時間，null=尚未使用 |
| used_by | String(36) | FK→users.id (SET NULL), nullable | 使用者（接受邀請的人） |
| created_at | DateTime | default 建立時 | 建立時間 |

---

## 3. 同步事件流（sync_changes 家族）

### `sync_changes` — 同步事件流表（SyncChange）★ 核心表
所有裝置間資料同步的**唯一權威來源**。每一筆新增/修改/刪除操作都會在這裡新增一行紀錄，`change_id` 單調遞增作為拉取游標。**只允許 INSERT，絕不 UPDATE 既有資料列**。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| change_id | BigInteger | PK, 自動遞增 | 事件序號（同步拉取游標） |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| ledger_id | String(36) | FK→ledgers.id (CASCADE), nullable, index | 所屬帳本；scope='user' 時為 NULL（使用者全域資源變更不依附帳本） |
| scope | String(8) | default "ledger", index | 變更範圍：`user`=分類/帳戶/標籤等跨帳本共享資源；`ledger`=預算/交易/帳本等帳本層級資源 |
| entity_type | String(64) | index | 實體型別，如 `transaction`（交易）、`category`（分類）、`account`（帳戶）等 |
| entity_sync_id | String(255) | index | 對應實體的同步 ID |
| action | String(16) | index | 動作類型：create（新增）/update（更新）/delete（刪除） |
| payload_json | JSON | not null | 該次變更的完整內容 |
| updated_at | DateTime | index | 業務時間（多人協作衝突時「最後寫入者優先」的決勝依據） |
| updated_by_device_id | String(36) | nullable, index | 來源裝置 ID |
| updated_by_user_id | String(36) | nullable, index | 來源使用者 ID（共享帳本情境下可能非帳本擁有者本人） |

索引：`idx_sync_changes_user_cursor`、`idx_sync_changes_ledger_cursor`、`idx_sync_changes_entity_latest`、`idx_sync_changes_user_scope_cursor`

> 備註：`entity_type = 'ledger_snapshot'` 只是這張表裡的一種特殊邏輯記錄類型，並非獨立資料表（見文件開頭澄清）。

### `sync_cursors` — 同步拉取游標（SyncCursor）
記錄每個「使用者 + 裝置 + 帳本」組合最後拉取到哪個 `change_id`，避免重複拉取。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 使用者 |
| device_id | String(36) | index | 裝置 ID |
| ledger_external_id | String(128) | index | 帳本本地 ID |
| last_cursor | BigInteger | default 0 | 最後拉取到的 change_id |
| updated_at | DateTime | default 更新時 | 更新時間 |

唯一約束：`uq_sync_cursor(user_id, device_id, ledger_external_id)`

### `sync_push_idempotency` — 同步推送冪等記錄（SyncPushIdempotency）
避免客戶端網路重試時，同一批推送內容被重複套用兩次。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 使用者 |
| device_id | String(64) | index | 裝置 ID |
| idempotency_key | String(128) | index | 客戶端提供的冪等鍵 |
| request_hash | String(128) | not null | 請求內容雜湊（偵測同鍵不同內容的異常用） |
| response_json | JSON | not null | 快取的回應內容，重試時直接回放 |
| created_at | DateTime | default 建立時 | 建立時間 |
| expires_at | DateTime | index | 過期時間 |

唯一約束：`uq_sync_push_idempotency(user_id, device_id, idempotency_key)`

---

## 4. 帳本層級讀取投影表（Ledger-Scoped Read Projections）

> 這一節的表全部是「讀路徑專用」的索引化視圖，主鍵皆為 `(ledger_id, sync_id)` 複合鍵，權威資料來自第 3 節的 `sync_changes`，每次同步套用時同一事務寫入，讓 Web 讀取可以直接走 SQL 查詢與索引，不需要解析整包 JSON。各表的 `source_change_id` 欄位純粹是診斷用途，記錄「這行是被哪一次同步事件寫入的」。

### `read_tx_projection` — 交易投影表（ReadTxProjection）★ 欄位最多的核心表
每一筆記帳交易（支出/收入/轉帳/餘額調整）的落地資料，是帳本內所有統計、列表、報表的資料來源。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 交易同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| tx_type | String(16) | not null | 交易類型：expense（支出）/income（收入）/transfer（轉帳）/adjustment（餘額調整） |
| amount | Float | default 0.0 | 實際影響帳戶餘額的金額 |
| happened_at | DateTime | not null | 交易發生時間 |
| note | Text | nullable | 備註 |
| merchant | Text | nullable | 商店名稱（純展示用，不參與統計） |
| category_sync_id | String(255) | nullable | 分類同步 ID |
| category_name | Text | nullable | 分類名稱快照 |
| category_kind | String(32) | nullable | 分類種類 |
| account_sync_id | String(255) | nullable | 帳戶同步 ID |
| account_name | Text | nullable | 帳戶名稱快照 |
| from_account_sync_id | String(255) | nullable | 轉帳-轉出帳戶同步 ID |
| from_account_name | Text | nullable | 轉出帳戶名稱快照 |
| to_account_sync_id | String(255) | nullable | 轉帳-轉入帳戶同步 ID |
| to_account_name | Text | nullable | 轉入帳戶名稱快照 |
| tags_csv | Text | nullable | 標籤名稱（逗號分隔），供模糊搜尋用 |
| tag_sync_ids_json | Text | nullable | 標籤同步 ID 列表（JSON） |
| attachments_json | Text | nullable | 附件清單（JSON） |
| tx_index | Integer | default 0 | 同一時間戳內的排序索引 |
| created_by_user_id | String(36) | nullable | 建立者（共享帳本情境） |
| last_edited_by_user_id | String(36) | nullable | 最後編輯者 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |
| exclude_from_stats | Boolean | default False | 是否不計入收支統計 |
| exclude_from_budget | Boolean | default False | 是否不計入預算用量 |
| currency_code | String(16) | nullable | 交易原始幣別代碼（多幣種功能），NULL 視為帳本本位幣 |
| native_amount | Float | nullable | 折算為帳本本位幣後的金額快照 |
| base_amount | Float | nullable | 使用者輸入的原始金額（信用卡回饋計算的基準） |
| fee_amount | Float | nullable | 手續費調整金額 |
| fee_label | Text | nullable | 手續費自訂名稱，NULL=顯示預設「手續費」 |
| discount_amount | Float | nullable | 折扣調整金額 |
| discount_label | Text | nullable | 折扣自訂名稱，NULL=顯示預設「折扣」 |
| to_amount | Float | nullable | 跨幣別轉帳時，轉入帳戶自身幣別的金額 |
| refund_of_sync_id | String(255) | nullable | 若此筆為退款，反查被退款的原始支出交易 |
| installment_plan_sync_id | String(255) | nullable | 若此筆為分期付款自動產生的一期，反查所屬分期計劃 |
| recurring_rule_sync_id | String(255) | nullable | 若此筆為週期性規則自動產生的一次，反查所屬規則 |
| recurring_occurrence_overridden | Boolean | default False | 該次週期交易是否已被單獨編輯過（不再被批次覆蓋） |
| has_splits | Boolean | default False | 是否有拆帳明細（true 時 category 應為 NULL） |
| splits_json | Text | nullable | 拆帳明細備援值（權威資料在 `read_tx_split_projection`） |
| debt_sync_id | String(255) | nullable | 若此筆為借還款的還款/收款，反查對應的欠款紀錄 |
| project_sync_id | String(255) | nullable | 使用者手動指定所屬的專案 |
| reward_rule_sync_ids_json | Text | nullable | 使用者勾選適用的信用卡回饋規則清單（JSON） |
| reward_source_tx_sync_id | String(255) | nullable | 若此筆為回饋自動入帳，反查原始消費交易 |
| deferred_posting_at | DateTime | nullable | 延後入帳的實際入帳日；NULL=沿用發生時間 |
| reconciled_at | DateTime | nullable | 對帳模式中已確認出現在該期帳單的時間；NULL=尚未對過帳 |

索引：`ix_read_tx_ledger_time`、`ix_read_tx_ledger_category`、`ix_read_tx_ledger_account`、`ix_read_tx_user_time`

### `read_budget_projection` — 預算投影表（ReadBudgetProjection）
記帳預算設定（依分類或整體設定的收支上限）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 預算同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| budget_type | String(32) | nullable | 預算類型 |
| category_sync_id | String(255) | nullable | 對應分類（若為分類別預算） |
| amount | Float | nullable | 預算金額 |
| period | String(32) | nullable | 週期（如每月/每年） |
| start_day | Integer | nullable | 週期起始日 |
| enabled | Boolean | default True | 是否啟用 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_budget_ledger_cat(ledger_id, category_sync_id)`

### `read_recurring_rule_projection` — 週期性收支規則表（ReadRecurringRuleProjection）
自動定期產生交易的規則（如每月房租、每週訂閱費）。建立規則時系統會依規則批次先生成一段時間內的交易，`generated_until_at` 記錄目前生成到哪個時間點。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 規則同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| tx_type | String(16) | default "expense" | 交易類型 |
| amount | Float | default 0.0 | 每次產生的金額 |
| note | Text | nullable | 備註 |
| category_sync_id | String(255) | nullable | 分類 |
| account_sync_id | String(255) | nullable | 帳戶 |
| from_account_sync_id | String(255) | nullable | 轉帳-轉出帳戶 |
| to_account_sync_id | String(255) | nullable | 轉帳-轉入帳戶 |
| merchant | Text | nullable | 商店名稱（「連同未來週期」批次更新可涵蓋的固定屬性之一） |
| project_sync_id | String(255) | nullable | 所屬專案 |
| tag_sync_ids_json | Text | nullable | 標籤列表（JSON） |
| base_amount | Float | nullable | 規則層級的手續費/折扣/回饋計算基準（每期繼承） |
| fee_amount | Float | nullable | 手續費調整金額 |
| fee_label | Text | nullable | 手續費自訂名稱 |
| discount_amount | Float | nullable | 折扣調整金額 |
| discount_label | Text | nullable | 折扣自訂名稱 |
| reward_rule_sync_ids_json | Text | nullable | 適用的信用卡回饋規則清單 |
| frequency | String(16) | default "monthly" | 觸發頻率：daily/weekly/monthly/yearly |
| interval | Integer | default 1 | 每隔幾個頻率單位觸發一次 |
| next_run_at | DateTime | not null | 原始錨點時間（歷史相容欄位，不再由排程推進） |
| end_at | DateTime | nullable | 規則結束時間，null=長期有效 |
| enabled | Boolean | default True | 是否啟用 |
| generated_until_at | DateTime | nullable | 已批次生成交易到哪個時間點 |
| advanced_rule_json | Text | nullable | 複雜規則設定（如「每週六日」），簡單頻率無法表達時使用 |
| kind | String(16) | server_default "general", index | 【3.7.0】規則種類：`general`=一般週期性收支（既有語意）/ `stock_dca`=股票定期定額。`stock_dca` 必為 `tx_type='transfer'`（轉出=交割帳戶、轉入=投資理財帳戶、amount=每期投入金額，以證券幣別計） |
| market | String(16) | nullable | 【3.7.0】僅 `stock_dca`：證券市場代碼（同 `securities.market`） |
| symbol | String(32) | nullable | 【3.7.0】僅 `stock_dca`：證券代號 |
| security_name | Text | nullable | 【3.7.0】僅 `stock_dca`：證券名稱（顯示用） |
| stock_fee_rate | Float | nullable | 【3.7.0】定期定額專屬手續費率，null=沿用投資帳戶預設 |
| stock_fee_min | Float | nullable | 【3.7.0】定期定額專屬最低手續費，null=沿用投資帳戶預設 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_recurring_rule_due(enabled, next_run_at)`、`ix_read_recurring_rule_kind(kind)`【3.7.0】

> `stock_dca` 規則到期時由 `services.recurring_materializer.materialize_due_stock_rules` 當下抓報價、算股數/手續費，建立轉帳交易與對應的 `stock_trade` 明細（見第 12 節）。

### `read_installment_plan_projection` — 分期付款計劃表（ReadInstallmentPlanProjection）
信用卡/貸款分期付款計劃的主檔。建立時系統會依攤還方式一次算出全部期數，寫入 `read_installment_period_projection` 明細與對應的交易紀錄。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 計劃同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| total_amount | Float | default 0.0 | 分期總金額 |
| periods | Integer | default 1 | 總期數 |
| period_amount | Float | default 0.0 | 每期金額參考值（等額類方案） |
| first_period_at | DateTime | not null | 第一期日期 |
| next_period_at | DateTime | not null | 歷史相容欄位，不再由排程推進 |
| paid_periods | Integer | default 0 | 歷史相容欄位，實際已繳期數由期數明細即時算出 |
| account_sync_id | String(255) | nullable | 扣款帳戶 |
| category_sync_id | String(255) | nullable | 分類 |
| note | Text | nullable | 備註 |
| status | String(16) | default "active" | 狀態：active（進行中）/settled（提前結清）/terminated（終止未來分期） |
| repayment_method | String(32) | default "equal_principal" | 攤還方式：equal_installment（等額本息）/equal_principal（等額本金）/fixed_interest（固定利率算原始本金） |
| interest_period | String(16) | default "monthly" | 計息方式：monthly（每期固定月利率）/daily（按實際天數計息） |
| interest_rate | Float | default 0.0 | 年利率（如 0.06 = 6%/年），0=無息 |
| round_amounts | Boolean | default True | 每期金額是否取整 |
| remainder_position | String(16) | default "last" | 取整尾差塞入哪一期：first（首期）/last（末期） |
| grace_period_months | Integer | default 0 | 寬限期月數 |
| offset_breakdown_json | Text | nullable | 帳單分期沖銷明細（系統計算後寫入，不接受使用者輸入） |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_installment_plan_due(status, next_period_at)`

### `read_installment_period_projection` — 分期付款期數明細表（ReadInstallmentPeriodProjection）
每一期分期的本金、利息、應繳金額明細。永遠只由伺服端生成，不接受客戶端直接建立單期。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 期數同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| plan_sync_id | String(255) | index | 反查所屬分期計劃 |
| period_no | Integer | not null | 第幾期 |
| due_at | DateTime | not null | 到期日 |
| principal_amount | Float | default 0.0 | 本金 |
| interest_amount | Float | default 0.0 | 利息 |
| total_amount | Float | default 0.0 | 該期應繳總額 |
| status | String(16) | default "generated" | 狀態：generated（已生成）/overridden（被單獨改過）/refunded（已退款）/pending（理論上不出現） |
| tx_sync_id | String(255) | nullable | 該期實際產生的交易反查 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_installment_period_plan(plan_sync_id, period_no)`

### `read_tx_split_projection` — 拆帳明細表（ReadTxSplitProjection）
一筆交易拆成多個分類的明細行（例如一張發票同時買了食物跟日用品）。非獨立同步實體，權威值來自父交易的 `splits` 欄位，每次更新父交易都整批重建這張表對應的明細行。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| tx_sync_id | String(255) | PK(複合) | 所屬父交易同步 ID |
| sort_order | Integer | PK(複合), default 0 | 明細排序 |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| category_sync_id | String(255) | nullable | 分類 |
| category_name | Text | nullable | 分類名稱快照 |
| amount | Float | default 0.0 | 此筆拆帳金額 |
| note | Text | nullable | 備註 |

索引：`ix_read_tx_split_ledger_tx`、`ix_read_tx_split_ledger_category`

### `read_debt_projection` — 借還款追蹤表（ReadDebtProjection）
記錄「我欠別人」或「別人欠我」的借貸關係。**不存餘額欄位**：每次還款/收款都是一筆普通交易（帶反查此表的 `debt_sync_id`），剩餘金額與狀態在讀取時即時從這些交易加總算出，避免跨表聯動重算造成資料漂移。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 欠款同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| direction | String(16) | default "payable" | 方向：payable（我欠別人）/receivable（別人欠我） |
| counterparty_name | Text | default "" | 對方名稱 |
| principal_amount | Float | default 0.0 | 本金金額（建立後不可修改） |
| due_at | DateTime | nullable | 到期日 |
| note | Text | nullable | 備註 |
| closed_at | DateTime | nullable | 手動標記結案時間；有值時優先決定顯示狀態（可能未還清就手動結案） |
| category_sync_id | String(255) | nullable | 分類 |
| origin_tx_sync_id | String(255) | nullable | 建立欠款時同時寫入的帳戶餘額起點交易反查 |
| excluded_from_total | Boolean | default False | 是否排除計入總資產計算 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_debt_user_id`、`ix_read_debt_ledger_due(ledger_id, due_at)`

### `read_project_projection` — 專案表（ReadProjectProjection）
使用者自訂的「專案」分組（例如「日本旅遊」「裝修工程」），可設專案預算，與 `read_budget_projection` 邏輯完全獨立。花費彙總不落庫，讀取時從交易的 `project_sync_id` 反查即時加總。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 專案同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| name | Text | default "" | 專案名稱 |
| icon | String(32) | nullable | 圖示 |
| budget_amount | Float | nullable | 專案預算金額 |
| period_type | String(16) | default "monthly" | 週期類型：fixed（單次固定起訖日）/monthly/yearly |
| period_start | Date | nullable | 起始日期 |
| period_end | Date | nullable | 結束日期 |
| carryover_enabled | Boolean | default False | 是否結轉到下個週期 |
| visible_on_home | Boolean | default True | 是否顯示於首頁 |
| enabled | Boolean | default True | 是否啟用 |
| sort_order | Integer | default 0 | 排序 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_project_user_id`、`ix_read_project_ledger_sort(ledger_id, sort_order)`

### `read_tx_template_projection` — 交易範本表（ReadTxTemplateProjection）
使用者儲存的常用交易組合（類型/金額/分類/帳戶），套用時直接產生一筆新交易，範本本身不含排程邏輯。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 範本同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| name | Text | default "" | 範本名稱 |
| tx_type | String(16) | default "expense" | 交易類型 |
| amount | Float | default 0.0 | 金額 |
| note | Text | nullable | 備註 |
| category_sync_id | String(255) | nullable | 分類 |
| account_sync_id | String(255) | nullable | 帳戶 |
| from_account_sync_id | String(255) | nullable | 轉帳-轉出帳戶 |
| to_account_sync_id | String(255) | nullable | 轉帳-轉入帳戶 |
| tag_sync_ids_json | Text | nullable | 標籤列表（JSON） |
| sort_order | Integer | default 0 | 排序 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_tx_template_user_id`、`ix_read_tx_template_ledger_sort(ledger_id, sort_order)`

---

## 5. 使用者全域投影表（User-Global Projections）

> 這一節的表主鍵皆為 `(user_id, sync_id)`，代表這些資源（分類、帳戶、標籤、匯率、信用卡回饋規則）是**跨帳本共享**的，不屬於任何單一帳本。

### `user_category_projection` — 分類表（UserCategoryProjection）
記帳分類（如「餐飲」「交通」），跨帳本共享。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| user_id | String(36) | FK→users.id (CASCADE), PK(複合) | 所屬使用者 |
| sync_id | String(255) | PK(複合) | 分類同步 ID |
| name | Text | nullable | 分類名稱 |
| kind | String(32) | nullable | 種類（收入/支出等） |
| level | Integer | nullable | 層級（一級/二級分類） |
| sort_order | Integer | nullable | 排序 |
| icon | String(255) | nullable | 圖示代碼 |
| icon_type | String(32) | nullable | 圖示類型 |
| custom_icon_path | String(1024) | nullable | 自訂圖示本地路徑（舊制） |
| icon_cloud_file_id | String(36) | nullable | 自訂圖示雲端檔案 ID |
| icon_cloud_sha256 | String(64) | nullable | 自訂圖示雲端檔案雜湊值 |
| parent_name | Text | nullable | 父分類名稱（顯示用備援值） |
| parent_sync_id | String(255) | nullable | 父分類同步 ID（穩定外鍵，父分類改名不影響子分類） |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_user_cat_kind(user_id, kind)`

### `user_account_projection` — 帳戶表（UserAccountProjection）
記帳帳戶（現金、銀行卡、信用卡、帳戶群組等），跨帳本共享。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| user_id | String(36) | FK→users.id (CASCADE), PK(複合) | 所屬使用者 |
| sync_id | String(255) | PK(複合) | 帳戶同步 ID |
| name | Text | nullable | 帳戶名稱 |
| account_type | String(64) | nullable | 帳戶類型（含 `account_group`=純管理容器，不可直接記帳） |
| currency | String(16) | nullable | 帳戶幣別 |
| initial_balance | Float | nullable | 初始餘額 |
| note | Text | nullable | 備註 |
| credit_limit | Float | nullable | 信用卡額度 |
| billing_day | Integer | nullable | 信用卡結帳日 |
| payment_due_day | Integer | nullable | 信用卡繳款日 |
| bank_name | Text | nullable | 銀行名稱 |
| card_last_four | String(8) | nullable | 卡號末四碼 |
| parent_account_id | String(255) | nullable | 主帳戶同步 ID（附卡/子卡指向主卡，用於合併帳單） |
| auto_pay_enabled | Boolean | default False | 信用卡自動扣繳開關 |
| auto_pay_from_account_id | String(255) | nullable | 自動扣繳來源帳戶 |
| avatar_cloud_file_id | String(64) | nullable | 帳戶頭像雲端檔案 ID |
| avatar_cloud_sha256 | String(64) | nullable | 帳戶頭像檔案雜湊值（去重用） |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |
| hidden | Boolean | default False | 是否隱藏（只影響列表顯示，不影響統計） |
| swipesmart_card_id | String(255) | nullable | 對應 SwipeSmart 服務的卡片 ID（僅信用卡類型有意義） |
| include_in_total | Boolean | default True | 是否納入總餘額/淨資產計算 |
| investment_settings_json | Text | nullable | 【3.7.0】投資理財帳戶的預設手續費/交易稅設定（JSON），僅 `account_type` 為投資理財時有意義；定期定額規則未覆寫手續費時沿用此值 |

### `user_exchange_rate_projection` — 手動匯率設定表（UserExchangeRateProjection）
使用者手動覆寫的貨幣匯率（優先於系統自動抓取的匯率）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| user_id | String(36) | FK→users.id (CASCADE), PK(複合) | 所屬使用者 |
| sync_id | String(255) | PK(複合) | 匯率設定同步 ID |
| base_currency | String(16) | not null | 基準幣別 |
| quote_currency | String(16) | not null | 報價幣別 |
| rate | String(32) | not null | 匯率（存字串避免浮點誤差），約定：1 基準幣 = rate × 報價幣 |
| updated_at | DateTime | default 更新時 | 更新時間 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_user_rate_pair(user_id, base_currency, quote_currency)`

### `user_tag_projection` — 標籤表（UserTagProjection）
記帳標籤（如「出差」「聚餐」），跨帳本共享。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| user_id | String(36) | FK→users.id (CASCADE), PK(複合) | 所屬使用者 |
| sync_id | String(255) | PK(複合) | 標籤同步 ID |
| name | Text | nullable | 標籤名稱 |
| color | String(32) | nullable | 標籤顏色 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

### `read_card_reward_rule_projection` — 信用卡紅利回饋規則表（ReadCardRewardRuleProjection）
信用卡消費回饋（現金回饋/點數）的計算規則。回饋金額不落庫，讀取時從綁定帳戶的當期交易即時算出。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| user_id | String(36) | FK→users.id (CASCADE), PK(複合) | 所屬使用者 |
| sync_id | String(255) | PK(複合) | 規則同步 ID |
| account_sync_id | String(255) | default "" | 綁定的信用卡帳戶 |
| label | Text | default "" | 規則名稱 |
| category_sync_ids_json | Text | nullable | 適用分類清單，null/空=所有消費都適用 |
| rate_type | String(16) | default "percentage" | 計算方式（百分比等） |
| rate_value | Float | default 0.0 | 費率/回饋率數值 |
| rounding | String(8) | default "round" | 單筆金額取整方式：round/floor/ceil/keep（不取整） |
| total_rounding | String(8) | default "round" | 總額取整方式 |
| calc_basis | String(24) | default "transaction_date" | 計算基準：交易日期或結算日期 |
| interval | String(16) | default "billing_cycle" | 計算週期 |
| min_spend_threshold | Float | nullable | 最低消費門檻（達標才給回饋） |
| min_tx_amount | Float | nullable | 單筆最低消費金額門檻 |
| cap_amount | Float | nullable | 回饋上限金額 |
| cap_shared_key | String(64) | nullable | 共用回饋上限的分組鍵 |
| starts_at | DateTime | nullable | 規則生效起始時間 |
| ends_at | DateTime | nullable | 規則失效時間 |
| note | Text | nullable | 備註 |
| enabled | Boolean | default True | 是否啟用 |
| settlement_type | String(24) | default "manual" | 自動入帳方式：manual（純顯示不自動化）/immediate_after_tx（逐筆消費後即時入帳）/after_posting_date（入帳日後）/period_end（週期結束時） |
| settlement_days | Integer | nullable | 逐筆結算：消費日後第 N 天入帳 |
| settlement_month_offset | Integer | nullable | period_end 結算：入帳月份偏移 |
| settlement_day_of_month | Integer | nullable | period_end 結算：入帳日 |
| reward_account_id | String(255) | nullable | 自動入帳的目的帳戶（非 manual 時必填） |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_card_reward_rule_account(user_id, account_sync_id)`

---

## 6. 附件 / 備份快照 / 稽核 / 通知

### `attachment_files` — 附件檔案表（AttachmentFile）
使用者上傳的檔案，交易憑證、分類自訂圖示、帳戶頭像共用同一張表。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 附件 ID |
| ledger_id | String(36) | FK→ledgers.id (CASCADE), nullable, index | 所屬帳本；交易附件必填，分類圖示/帳戶頭像為 NULL（跨帳本共享） |
| user_id | String(36) | FK→users.id (CASCADE), index | 上傳使用者 |
| sha256 | String(64) | index | 檔案內容雜湊（去重用） |
| size_bytes | BigInteger | default 0 | 檔案大小（位元組） |
| mime_type | String(128) | nullable | 檔案類型 |
| file_name | String(255) | nullable | 檔案名稱 |
| storage_path | String(1024) | not null | 實際儲存路徑 |
| attachment_kind | String(32) | default "transaction" | 附件用途：`transaction`（交易附件）/`category_icon`（分類自訂圖示）；也用於帳戶頭像 |
| created_at | DateTime | default 建立時, index | 上傳時間 |

索引：`idx_attachment_files_sha256`、`idx_attachment_files_ledger_created(ledger_id, created_at)`

### `backup_snapshots` — 帳本備份快照（BackupSnapshot，舊機制）
較舊版本的帳本備份快照，與第 9 節的 rclone 備份系統並存。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 快照 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| ledger_id | String(36) | FK→ledgers.id (CASCADE), index | 所屬帳本 |
| snapshot_json | Text | not null | 快照內容（JSON 字串） |
| note | String(255) | nullable | 備註 |
| created_at | DateTime | default 建立時 | 建立時間 |

### `backup_artifacts` — 備份產物記錄（BackupArtifact，較新機制）
另一套備份產物記錄，與第 9 節的 `backup_runs`/`backup_remotes` 相關但欄位形狀不同（維運上如需確認實際使用場景，建議與開發者再核對）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | 備份產物 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| ledger_id | String(36) | FK→ledgers.id (CASCADE), index | 所屬帳本 |
| kind | String(16) | index | 備份類型 |
| file_name | String(255) | not null | 檔案名稱 |
| storage_path | String(1024) | not null | 實際儲存路徑 |
| content_type | String(128) | nullable | 檔案類型 |
| checksum_sha256 | String(64) | index | 檔案雜湊值 |
| size_bytes | BigInteger | default 0 | 檔案大小 |
| metadata_json | JSON | not null | 額外結構化資訊 |
| created_at | DateTime | default 建立時, index | 建立時間 |

索引：`idx_backup_artifacts_ledger_created(ledger_id, created_at)`

### `audit_logs` — 系統稽核記錄（AuditLog）
系統層級操作的稽核紀錄。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | BigInteger | PK | 流水號 |
| user_id | String(36) | FK→users.id (SET NULL), nullable, index | 操作使用者，使用者刪除後仍保留紀錄 |
| ledger_id | String(36) | FK→ledgers.id (SET NULL), nullable, index | 相關帳本 |
| action | String(128) | index | 動作名稱 |
| metadata_json | JSON | not null | 額外結構化資訊 |
| created_at | DateTime | default 建立時, index | 發生時間 |

### `notifications` — 使用者通知（Notification）
使用者通知中心（超支提醒、週期到期、信用卡繳款提醒等）。跨帳本共享，走一般 REST API，不進同步事件流。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 通知 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| category | String(32) | index | 分類：`reminder`（提醒）/`budget_alert`（預算警示）/`card_due`（信用卡繳款）/`system`（系統） |
| title | String(255) | not null | 標題 |
| body | Text | nullable | 內文 |
| payload_json | JSON | nullable | 結構化附加資料（如關聯的帳本/交易 ID），供前端跳轉用 |
| read_at | DateTime | nullable, index | 已讀時間，null=尚未讀 |
| created_at | DateTime | default 建立時, index | 建立時間 |
| pinned | Boolean | default False | 是否釘選置頂（目前僅欠款到期提醒會設為 True，避免被新通知洗掉） |

索引：`ix_notifications_user_time(user_id, created_at desc)`

---

## 7. 信用卡紅利回饋入帳台帳

### `card_reward_payouts` — 信用卡回饋入帳去重台帳（CardRewardPayout）
信用卡回饋自動入帳的內部去重記錄，防止同一筆交易/週期重複入帳。**不是同步實體**，不進同步事件流，也不是通知中心的一部分。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE) | 所屬使用者 |
| rule_sync_id | String(255) | not null | 對應的回饋規則同步 ID |
| dedup_key | String(255) | not null | 去重鍵：逐筆結算=交易同步 ID；期末結算=期末日期 |
| amount | Float | default 0.0 | 入帳的回饋金額 |
| payout_tx_sync_id | String(255) | nullable | 產生的入帳交易反查 |
| created_at | DateTime | default 建立時 | 建立時間 |

唯一索引：`ux_card_reward_payouts_dedup(user_id, rule_sync_id, dedup_key)`

---

## 8. 匯率快取

### `exchange_rate_cache` — 匯率快取表（ExchangeRateCache）
匯率代理服務的伺服端快取，每個基準幣別一行，整包報價存 JSON。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| base_currency | String(16) | PK | 基準幣別代碼 |
| rate_date | String(10) | not null | 匯率日期 |
| source | String(32) | not null | 匯率來源服務商 |
| payload_json | JSON | not null | 各報價幣別匯率整包，格式如 `{"USD": "0.1477", ...}`，代表 1 基準幣 = x 報價幣 |
| fetched_at | DateTime | default 抓取時 | 抓取時間 |

---

## 9. 備份系統（rclone 多遠端加密備份）

> 共 5 張表：遠端配置 / 排程設定 / 排程與遠端多對多關聯 / 單次備份運行 / 每個目標遠端的推送狀態。

### `backup_remotes` — 備份遠端配置（BackupRemote）
rclone 遠端儲存位置配置（如 S3、Google Drive），也可以是加密裝飾層。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| name | String(64) | not null | 遠端名稱 |
| backend_type | String(32) | not null | 儲存後端類型：`s3`/`gdrive`/`crypt`（加密層）等 |
| encrypted | Boolean | default False | 是否為套在另一個遠端之上的加密層 |
| config_summary | JSON | nullable | 配置摘要（不含敏感金鑰明文） |
| last_test_at | DateTime | nullable | 最後測試連線時間 |
| last_test_ok | Boolean | nullable | 最後測試是否成功 |
| last_test_error | Text | nullable | 最後測試錯誤訊息 |
| created_at | DateTime | default 建立時 | 建立時間 |
| updated_at | DateTime | default/onupdate 更新時 | 更新時間 |

唯一約束：`uq_backup_remote_user_name(user_id, name)`

### `backup_schedules` — 備份排程設定（BackupSchedule）
自動備份的排程規則。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| name | String(128) | not null | 排程名稱 |
| enabled | Boolean | default True | 是否啟用 |
| cron_expr | String(64) | not null | Cron 排程表達式（5 欄位） |
| retention_days | Integer | default 30 | 備份保留天數 |
| include_attachments | Boolean | default True | 是否包含附件檔案 |
| next_run_at | DateTime | nullable | 下次執行時間 |
| last_run_at | DateTime | nullable | 上次執行時間 |
| last_run_status | String(16) | nullable | 上次執行狀態 |
| created_at | DateTime | default 建立時 | 建立時間 |
| updated_at | DateTime | default/onupdate 更新時 | 更新時間 |

### `backup_schedule_remotes` — 排程與遠端關聯表（BackupScheduleRemote）
一個排程可同時推送到多個遠端做冗餘備份（多對多關聯）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| schedule_id | Integer | FK→backup_schedules.id (CASCADE), PK(複合) | 排程 ID |
| remote_id | Integer | FK→backup_remotes.id (RESTRICT), PK(複合) | 遠端 ID |
| sort_order | Integer | default 0 | 推送順序 |

### `backup_runs` — 備份運行記錄（BackupRun）
每一次實際執行的備份運行紀錄。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| schedule_id | Integer | FK→backup_schedules.id (SET NULL), nullable, index | 觸發的排程；手動觸發時為 NULL |
| started_at | DateTime | default 開始時 | 開始時間 |
| finished_at | DateTime | nullable | 結束時間 |
| status | String(16) | default "running", index | 狀態：running/succeeded/partial（部分成功）/failed/canceled |
| backup_filename | String(128) | nullable | 備份檔名 |
| bytes_total | BigInteger | nullable | 備份檔總大小 |
| error_message | Text | nullable | 錯誤訊息 |
| log_text | Text | nullable | 執行日誌 |

### `backup_run_targets` — 備份運行目標狀態（BackupRunTarget）
單次備份運行中，對每個目標遠端的推送狀態（可能部分成功部分失敗）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| run_id | Integer | FK→backup_runs.id (CASCADE), index | 所屬備份運行 |
| remote_id | Integer | FK→backup_remotes.id, index | 目標遠端 |
| status | String(16) | default "pending" | 狀態：pending/running/succeeded/failed |
| started_at | DateTime | nullable | 開始時間 |
| finished_at | DateTime | nullable | 結束時間 |
| bytes_transferred | BigInteger | nullable | 已傳輸位元組數 |
| error_message | Text | nullable | 錯誤訊息 |

---

## 10. 背景排程管理

### `scheduled_job_configs` — 背景排程設定表（ScheduledJobConfig）
把系統原本散落的多個背景排程動作（MCP 日誌清理、週期性收支物化、借還款提醒、信用卡繳款提醒、自動扣繳、信用卡紅利回饋入帳）統一收斂成一張設定表，讓管理員可在後台調整頻率、停用或立即執行，不需改代碼重新部署。全域單例設定，不分使用者，屬於運維層級操作。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK | 流水號 |
| job_key | String(64) | unique, index | 排程動作識別鍵 |
| interval_seconds | Integer | not null | 執行間隔（秒） |
| enabled | Boolean | default True | 是否啟用 |
| next_run_at | DateTime | nullable | 下次執行時間 |
| last_run_at | DateTime | nullable | 上次執行時間 |
| last_run_status | String(16) | nullable | 上次執行狀態 |
| last_run_message | Text | nullable | 上次執行訊息 |
| created_at | DateTime | default 建立時 | 建立時間 |
| updated_at | DateTime | default/onupdate 更新時 | 更新時間 |

---

## 11. 授權金鑰

### `license_keys` — 授權金鑰（LicenseKey）

管理者在後台產生、使用者啟用後綁定帳號。使用者目前授權 = 名下未撤銷金鑰中最晚的 `expires_at`，不另存欄位。詳見 [`LICENSE_KEYS.md`](./LICENSE_KEYS.md)。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | String(36) | PK | UUID |
| key | String(64) | unique, index | 金鑰明文（`BC-XXXXX-XXXXX-XXXXX-XXXXX`） |
| duration_days | Integer | default 365 | 啟用後可用天數 |
| note | String(255) | nullable | 管理者備註 |
| created_by_user_id | String(36) | FK users, SET NULL | 產生者 |
| created_at | DateTime | default 建立時 | 產生時間 |
| redeemed_by_user_id | String(36) | FK users, SET NULL, index | 啟用的帳號（null = 未使用） |
| redeemed_at | DateTime | nullable | 啟用時間 |
| expires_at | DateTime | nullable | 到期時間（啟用時才寫入） |
| revoked_at | DateTime | nullable | 撤銷時間（非 null = 立即失效） |

另：`app_version_check_config.min_sync_version`（String(32), nullable）= 最低可同步 App 版本，null 不限制。


---

## 12. 股票持股（3.7.0）

> 3.7.0 新增股票持股功能（設計文件：[`STOCK_HOLDINGS_SD.md`](./STOCK_HOLDINGS_SD.md)；Migration：`0058_stock_holdings`、`0059_stock_dividends`、`0060_recurring_stock_dca`）。共新增 **5 張表**，並擴充 2 張既有表（`user_account_projection.investment_settings_json`、`read_recurring_rule_projection` 的股票定期定額欄位，見第 4、5 節）。
>
> 分成兩類，**不要混用**：
> - **全域市場資料（不分使用者、不進 sync）**：`securities`、`security_quotes`、`security_dividend_events`——跟 `exchange_rate_cache` 同款，是 server 自己從外部來源（證交所/櫃買 OpenAPI、Yahoo）抓的。
> - **使用者資料**：`read_stock_trade_projection`（**sync entity** `stock_trade`，ledger-scoped）；`pending_dividends`（**server 專屬狀態，不進 sync**，App/Web 都透過 API 讀寫）。
>
> **持股不落庫**：股數、平均成本（移動平均成本法）、已實現損益、未實現損益一律在讀取時由 `read_stock_trade_projection` 即時彙總（`services/securities/holdings.py`），與借還款餘額同一設計理由。

### `securities` — 證券清單（Security）
全域證券主檔，業務鍵 `(market, symbol)`。台股由 `security_master_refresh` 排程從證交所/櫃買 OpenAPI 整批同步；其它市場在使用者搜尋（Yahoo）時才 upsert。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 證券內部 ID |
| market | String(16) | not null | 市場代碼：TW=上市、TWO=上櫃、US、HK…（見 `services/securities/markets.py`） |
| symbol | String(32) | not null | 證券代號 |
| name | Text | not null, default "" | 證券名稱 |
| currency | String(16) | not null | 計價幣別 |
| kind | String(16) | not null, default "stock" | 類型：`stock`/`etf`/`bond_etf`/`other`（僅搜尋結果顯示用） |
| is_active | Boolean | server_default true | 是否仍在市場上（下市/停用為 false） |
| updated_at | DateTime | default 更新時 | 最後更新時間 |

唯一索引：`ux_securities_market_symbol(market, symbol)`

### `security_quotes` — 最新報價快取（SecurityQuote）
每檔證券一行的最新報價。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| security_id | Integer | PK, FK→securities.id (CASCADE) | 對應證券 |
| price | Float | not null | 最新價 |
| prev_close | Float | nullable | 前一日收盤價（算當日漲跌用） |
| quote_time | DateTime | nullable | 報價本身的時間（交易所時間轉 UTC），不是抓取時間 |
| session | String(16) | not null, default "close" | `close`=收盤價（收盤排程寫入）/ `intraday`=盤中延遲報價（使用者開 App/Web 時補抓） |
| source | String(32) | not null | 報價來源 |
| fetched_at | DateTime | default 抓取時 | 抓取時間 |

### `security_dividend_events` — 除權息事件（SecurityDividendEvent）
全域除權息資料，業務鍵 `(security_id, ex_date)`。官方預告表常先公告除息日、金額之後才補，所以金額欄位會被更新；官方來源（twse/tpex）寫過的事件不會被 Yahoo 蓋掉（`services/securities/dividends.upsert_events`）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 事件 ID |
| security_id | Integer | FK→securities.id (CASCADE), not null | 對應證券 |
| ex_date | Date | not null | 除權息日 |
| pay_date | Date | nullable | 發放日 |
| cash_per_share | Float | not null, default 0.0 | 每股現金股利（證券幣別） |
| stock_per_share | Float | not null, default 0.0 | 每股配股數（台股無償配股率，0.05=每 1000 股配 50 股） |
| currency | String(16) | nullable | 股利幣別 |
| source | String(32) | not null | 來源：`twse`/`tpex`（官方，優先）/`yahoo` |
| updated_at | DateTime | default 更新時 | 最後更新時間 |

唯一索引：`ux_security_dividend_events_sec_date(security_id, ex_date)`

### `pending_dividends` — 待確認股利（PendingDividend）
`security_dividend_detector` 排程在除息日（含）之後，依「除息日前一天收盤時」的持股股數，替每個持有該標的的投資理財帳戶建一筆並發通知；使用者在 App/Web 確認實收金額（或選擇再投入）後，由 server 建立 income 交易 + `stock_trade` 明細。**不是 sync entity。** 狀態仍為 pending 時，`est_*` 估算欄位每次排程依最新持股重算；已確認的股利若使用者之後刪掉建出的明細，排程會改回 pending（不重發通知）。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| id | Integer | PK, autoincrement | 流水號 |
| user_id | String(36) | FK→users.id (CASCADE), not null | 所屬使用者 |
| ledger_id | String(36) | FK→ledgers.id (CASCADE), not null | 入帳落在哪本帳（取該帳戶這檔標的最近一筆明細所在帳本） |
| account_sync_id | String(255) | not null | 持有該標的的投資理財帳戶 |
| event_id | Integer | FK→security_dividend_events.id (CASCADE), not null | 對應除權息事件 |
| market | String(16) | not null | 市場代碼（冗餘，方便顯示） |
| symbol | String(32) | not null | 證券代號 |
| security_name | Text | nullable | 證券名稱 |
| currency | String(16) | nullable | 幣別 |
| shares | Float | not null, default 0.0 | 除息日前一天收盤時的持股股數 |
| est_gross | Float | not null, default 0.0 | 估算股利總額（稅前） |
| est_fee | Float | not null, default 0.0 | 估算手續費（如匯費） |
| est_tax | Float | not null, default 0.0 | 估算稅額（如補充保費） |
| est_net | Float | not null, default 0.0 | 估算實收金額 |
| est_stock_shares | Float | not null, default 0.0 | 估算配股股數 |
| status | String(16) | not null, default "pending" | `pending`/`confirmed`/`dismissed` |
| created_trade_ids | Text | nullable | 確認時建立的 `stock_trade` syncId（JSON 陣列字串），用來判斷是否被刪掉 |
| created_at | DateTime | default 建立時 | 建立時間 |
| updated_at | DateTime | default 更新時 | 更新時間 |
| resolved_at | DateTime | nullable | 確認/略過時間 |

索引：`ux_pending_dividends_user_account_event(user_id, account_sync_id, event_id)` 唯一、`ix_pending_dividends_user_status(user_id, status)`

### `read_stock_trade_projection` — 股票交易明細（ReadStockTradeProjection）
**Ledger-scoped sync entity（`stock_trade`）**，PK 形狀同借還款。持股由這張表即時彙總（移動平均成本法）。`tx_sync_id` 指向對應的轉帳/收入交易（買進/賣出/現金股利/再投入才有；期初持股、配股沒有現金流動，為 null），兩者由同一次寫入一起建立、一起刪除。

| 欄位 | 型別 | 屬性 | 中文說明 |
|---|---|---|---|
| ledger_id | String(36) | FK→ledgers.id (CASCADE), PK(複合) | 所屬帳本 |
| sync_id | String(255) | PK(複合) | 明細同步 ID |
| user_id | String(36) | FK→users.id (CASCADE), index | 所屬使用者 |
| account_sync_id | String(255) | nullable | 投資理財帳戶 |
| market | String(16) | default "" | 市場代碼 |
| symbol | String(32) | default "" | 證券代號 |
| security_name | Text | nullable | 證券名稱 |
| trade_type | String(32) | default "buy" | `buy`買進 / `sell`賣出 / `opening`期初持股 / `cash_dividend`現金股利 / `stock_dividend`配股 / `reinvest`股利再投入 |
| shares | Float | default 0.0 | 股數 |
| price | Float | nullable | 成交單價 |
| fee | Float | default 0.0 | 手續費 |
| tax | Float | default 0.0 | 交易稅 |
| amount | Float | default 0.0 | 以證券幣別計的現金影響（正數）：buy/opening=股數×價格+手續費；sell=股數×價格−手續費−交易稅；cash_dividend/reinvest=實收金額；stock_dividend=0 |
| currency | String(16) | nullable | 證券幣別 |
| trade_date | DateTime | nullable | 交易日期 |
| tx_sync_id | String(255) | nullable | 對應的轉帳/收入交易 syncId |
| dividend_event_ref | String(64) | nullable | 股利明細對應的除權息事件（`security_dividend_events.id` 字串），非股利為 null |
| note | Text | nullable | 備註 |
| created_by_user_id | String(36) | nullable | 建立者 |
| source_change_id | BigInteger | default 0 | 診斷用：來源同步事件 ID |

索引：`ix_read_stock_trade_projection_user_id(user_id)`、`ix_read_stock_trade_account(user_id, account_sync_id)`、`ix_read_stock_trade_symbol(market, symbol)`

---

## 附錄：常見疑問

**Q：`read_deferred_posting_projection` 或「延後入帳表」在哪裡？**
A：不存在獨立表，是 `read_tx_projection.deferred_posting_at` 欄位（見第 4 節）。

**Q：`ledger_snapshot` 表在哪裡？**
A：不是資料表，是 `sync_changes.entity_type = 'ledger_snapshot'` 的一種邏輯記錄類型，新程式碼已不再主動寫入，改由 `/sync/full` 從投影表按需即時組出快照 JSON 給 mobile 端使用。

**Q：為什麼有些表（如借還款、分期、專案）沒有「餘額」「已繳期數」「花費總額」這類統計欄位？**
A：這是本專案刻意的設計取捨：這類彙總數字若落庫，會需要在「行動端推送」與「Web 端寫入」兩條獨立路徑上都維護一段「改交易時聯動重算」的邏輯，容易產生資料漂移的 bug。因此一律改成讀取時從相關交易即時反查加總，詳見 [`CLAUDE.md`](../CLAUDE.md) 與各表在 `src/models.py` 中的 docstring。
