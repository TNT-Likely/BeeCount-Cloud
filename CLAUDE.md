# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# BeeCount Cloud —— AI 助手 / 開發者指南

本文件旨在提供 AI 編碼助手 (Claude Code / Copilot 等) 與新進開發者快速定位與開發規範指引，包含**常用指令**、**架構契約**與**避免 Bug 的標準作業流程 (SOP)**。

---

## 常用指令

### 後端 (FastAPI, Python 3.12+)

```bash
make setup-backend        # 建立 .venv + 安裝 requirements.txt + 複製 .env
make migrate              # alembic upgrade head
make dev-api              # uvicorn server:app --reload --host 0.0.0.0 --port 8080
make test                 # pytest -q（或 python -m pytest tests/）
make lint                 # ruff check src tests alembic
make typecheck            # mypy src
```

**單一測試執行：**
```bash
. .venv/bin/activate && pytest tests/test_budget_crud.py -q
. .venv/bin/activate && pytest tests/test_budget_crud.py::test_some_case -q
```

**常用維護腳本：**
```bash
make seed-demo                       # 灌入演示資料
make grant-admin EMAIL=user@x.com    # 將指定使用者提升為 Admin
make wipe-local                      # 清空本地 SQLite + data/ 運行時檔案（保留 docs-index）
python scripts/rebuild_all_projections.py   # 從 sync_changes 事件流重建所有 read_*_projection 表
```

> 本地預設資料庫為專案根目錄的 `beecount.db` (SQLite)；可透過 `make dev-db` 拉起 Docker Postgres 進行多處理器/生產環境儲存路徑驗證。

### 前端 (`frontend/`, pnpm workspace: `apps/web` + `packages/{api-client,ui,web-features}`)

```bash
make dev-web                         # pnpm install + pnpm -C apps/web dev
cd frontend && pnpm -C apps/web build       # tsc -b && vite build
cd frontend && pnpm -C apps/web test        # vitest run
cd frontend && pnpm -C apps/web test:unit   # vitest run src (僅單元測試)
```

---

## 核心架構與資料模型

同步層分為三種儲存形態，**切勿混用**：

1. **`sync_changes` (事件流)**: Append-only 數據表，`change_id` 單調遞增，為增量同步 (pull) 的權威源頭。**永遠只做 INSERT，絕不 UPDATE**。
2. **`read_*_projection` (讀取模型/投影表)**: 讀路徑 (Read APIs) 的唯一權威源。LWW 衝突決勝與 Rename Cascade 均在此落盤。
3. **`ledger_snapshot` (JSON 快照)**: `/sync/full` 採懶加載 (Lazy Build) 模式從 Projection 即時構建。**新程式碼請勿主動寫入 `ledger_snapshot`**。

### 實體作用域 (Scope)
* **Ledger-Scoped**: 包含 Transaction, Category, Budget, Debt, RecurringRule, InstallmentPlan, TxTemplate, DeferredPosting 等（PK 帶有 `ledger_id`）。
* **User-Global**: 包含 UserAccountProjection, UserTagProjection, UserCategoryProjection, CardRewardRule 等（PK 為 `(user_id, sync_id)`，跨帳本共享）。

---

## 開發規範與鐵律 (Hard Rules)

### 1. `main.py` 載入順序鐵律
* `ensure_jwt_secret()` **必須在任何 `from .routers ...` 導入前執行**。
* 原因：部分 Router 模組頂層呼叫了 `@lru_cache` 的 `get_settings()`，若先導入 Router，會導致預設占位 JWT_SECRET 被快取，造成生產環境 JWT 簽驗失敗。

### 2. 新增或修改 Sync Entity 檢查清單 (SOP)
當需要新增/修改同步實體時，必須確保同時更新以下 7 個位置：
1. **DB & Migration**: 新增 `read_*_projection` 資料表與 Alembic Migration。
2. **Projection**: 在 `src/projection.py` 新增 `upsert_*` / `delete_*` / `rename_cascade_*`。
3. **Sync Applier**: 在 `src/sync_applier.py` 的 `_MERGE_SPECS` / `_UPSERT_DISPATCH` / `_DELETE_DISPATCH` 登記。
4. **Write Routers**: 在 `src/routers/write/<entity>.py` 實作 POST / PATCH / DELETE 端點（並加入必要校驗）。
5. **Read Routers**: 在 `src/routers/read/ledgers.py` 或 `workspace.py` 新增讀取端點。
6. **Snapshot Builder**: ⚠️ **極易遺漏** ── 若該實體屬帳本快照的一部分，**必須同步更新 `src/snapshot_builder.py` 中的 SELECT 語句**（包含所有欄位），否則連續 Update 會讀到空基線而校驗失敗。
7. **測試 (pytest)**: 必須補上 `test_mobile_push_<entity>_partial_update_keeps_existing_fields` 風格的 Merge 契約測試，確保 Partial Update 不會將未帶欄位靜默衝成 null。

### 3. 特殊業務校驗規範
* **帳戶群組限制**: `account_type == "account_group"` 為純管理容器。任何交易（Transaction）、週期性收支（RecurringRule）、分期（InstallmentPlan）、範本套用（TxTemplate）寫入路徑，必須呼叫 `_assert_account_not_group` 擋下。
* **系統自動產生交易不可漏分類**（2026-08-07 Phase 9 補充；2026-08-08 Phase 12 擴充）: 任何「系統自動產生/歸類」的交易（退款、信用卡回饋入帳、欠還款…）落地前都要確保分類欄位不會留空，比照 `src/services/card_rewards.py` 現有的 `ensure_refund_category`/`ensure_reward_category`/`ensure_debt_category`/`ensure_uncategorized_category` 模式——共用 `_ensure_user_global_category(db, user_id, name, kind, device_id)` 這個「sync push 等价」旁路（不走 snapshot_mutator 整套 HTTP write 引擎），自建/複用一個固定名稱的 user-global 分類，且只在使用者沒有自己填分類/備註時才帶入預設值，不覆蓋使用者自己填的內容。新增同類自動分類需求時，優先擴充這個共用 helper，不要各自複製一份「找不到就建立」的邏輯。**Phase 12 補充的第二種模式**：週期性收支（非轉帳）/分期付款這類「使用者自己該手動指定分類、系統不該代猜」的情境，改成前端強制必選 + 後端 `src/routers/write/_shared.py::_assert_category_required(tx_type, category_id)` 擋（transfer 類型豁免），而不是自動歸類——新增/修改任何會產生交易的 write endpoint（create + update 都要檢查，PATCH 顯式清空分類也要擋）時，先判斷這個實體屬於「該自動歸類」還是「該強制必選」哪一種，不要預設沿用其中一種模式。既有資料（上線前已存在、分類是 NULL 的規則/計畫）不會被前端擋新建這件事自動修好，要另外寫一次性回填（`scripts/backfill_recurring_installment_categories.py` 是範本：用跟一般 write endpoint 相同的「SyncChange + `sync_applier.apply_change_to_projection`」局部更新，不要直接改 projection 表，否則其它裝置下次同步拉不到這次修復、且下一次 `_commit_write` 的 diff 可能把它当「本来就没有」撤销）。
* **貨幣符號單一來源**（2026-08-08 Phase 12 補充）: 幣別代碼 → 顯示符號（`¥`/`$`/`€`…）只有一份實作，在 `frontend/packages/web-features/src/lib/currencies.ts::currencySymbol()`，其它地方一律 import 這個函式，不要各自寫一份 switch/對照表（Phase 12 之前有 4 套各自獨立的重複實作，這是收斂後的唯一來源）。這個函式刻意**不**用 `Intl.NumberFormat` 動態推導——那樣會依 locale 帶出 `US$`/`JP¥` 這種國家碼前綴，跟現況的固定符號（`$`/`¥`）不一致；需要幣別符號時直接呼叫這個函式，不要重新引入 Intl 版本。
* **分期付款年利率 UI 顯示慣例**（2026-08-08 Phase 12 補充）: 後端/表單狀態（`TxForm.installment_interest_rate`、`InstallmentPlanForm.interest_rate` 等）一律存**小數分數**（`0.06` = 6%/年，`services/installment_amortization.py` 的既有數學吃這個），但 UI `<Input>` 一律顯示/接受**整數百分比**（使用者打 `6` 代表 6%），透過 `format.ts::interestRateToPercentDisplay`/`percentDisplayToInterestRate` 這兩個共用函式做「顯示 ⇄ 儲存」轉換（會四捨五入到固定精度，避免 `0.06 * 100` 這類二進位浮點運算殘留雜訊數字）。新增任何年利率輸入框都要複用這兩個函式，不要直接把 state 綁 `<Input value>`（那樣使用者又要自己心算打 `0.06`）。
* **前端 UI 驗證要求**: 任何觸及 Web UI 的改動，**必須實際在瀏覽器中手動操作過一遍**，不能僅憑 `pytest` 或 `pnpm build` 通過就宣稱完成。
* **Service Worker 快取陷阱**（2026-08-07 實測踩到；2026-08-07 Phase 8 補充踩到 unregister 仍不夠的變體）: `apps/web` 是 PWA，瀏覽器可能對 `localhost:5173` 殘留舊版建置的 Service Worker registration，導致 `pnpm dev` 熱重載後新程式碼完全不生效（新增的按鈕/欄位「看起來沒出現」）。手動驗證前若改動明明已存在於原始碼卻不出現在畫面上，先用 DevTools → Application → Service Workers 檢查有沒有殘留 registration，或執行 `navigator.serviceWorker.getRegistrations()` 全部 `unregister()` + `caches.keys()` 全部 `delete()` 後重新整理，不要先懷疑程式碼邏輯本身有問題。**補充**：`unregister()` + 清 Cache API 之後接一般的重新整理（含程式呼叫 `navigate()`）不保證吃到新內容——可能是舊 SW 在 unregister 生效前那個 tick 就把回應交回去了，也可能是瀏覽器層的 HTTP disk cache（獨立於 Service Worker 的 Cache API）還留著一份。已經用 `curl`/`fetch` 對 Vite dev server 直接驗證過原始碼確實是新版，但畫面還是舊的話，直接跳過「unregister + 清快取 + 普通重整」，一律用 `Ctrl+Shift+R` 硬性重整（bypass HTTP cache）比較省事，不要在普通重整上反覆試。
* **本地 API server 沒帶 `--reload` 陷阱**（2026-08-07 實測踩到）: 手動用 `uvicorn server:app --host 0.0.0.0 --port 8080`（沒加 `--reload`）背景啟動的話，後續對 `src/` 的任何修改都不會生效，瀏覽器打到的永遠是啟動當下那個版本的程式碼，新增的 schema 欄位/查詢邏輯在 API 回應裡會整個消失，容易誤判「邏輯寫錯了」。手動驗證前若懷疑後端改動沒生效，先用 `curl .../openapi.json` 比對目標 schema 有沒有帶到新欄位，或直接確認啟動指令有沒有 `--reload`；沒有就 kill 掉重開一個新的進程，不要先排查程式碼邏輯。
* **殭屍 listener 陷阱**（2026-08-07 Phase 7 實測踩到）: 就算 `--reload` 有掛、也重開了新的 uvicorn 行程，`curl .../openapi.json` 仍可能持續回傳舊版 schema——根因可能是 port 8080 上還留著一個「行程本體已經死亡（`Get-Process`/`tasklist` 都查不到），但 TCP listening socket 沒有真的釋放」的殭屍監聽（常見於用 `Stop-Process` 只殺了子行程、沒有連 uvicorn `--reload` 的 reloader 父行程一起砍乾淨）。判斷方法：`netstat -ano | findstr :8080` 看到 LISTENING 的 PID，若 `Get-Process -Id <pid>` 查不到該行程，就是殭屍 listener。修法：連父子整棵行程樹一起用 `taskkill /F /PID <pid> /T` 砍掉（只砍看到的那個 PID 通常不夠，因為 uvicorn `--reload` 會有 reloader 行程再 fork 一個實際跑 app 的子行程），確認 `netstat` 不再顯示該 port 的 LISTENING 項目後才重新啟動。
* **帳戶緊湊列表 + 巢狀子帳戶元件**（2026-08-08 Phase 10 補充；2026-08-08 Phase 11 更新）: `AccountListRow`（取代舊的 `BankCardTile` 漸層卡片網格）已從 `AccountsPanel.tsx` 抽成共用元件 `frontend/packages/web-features/src/components/AccountListRow.tsx`（連同 `TYPE_COLORS`/`TypeIcon`/`accountTypeLabel`/`buildAccountChildrenMap`），供 `AccountsPanel.tsx`（資產頁列表）與新的 `AccountPickerDialog.tsx`（Phase 11 交易表單帳戶選擇彈窗）共用。支援 `childRows` 縮排巢狀渲染（同一 `account_group` 底下、`parent_account_id` 指向它的子帳戶）。**巢狀子帳戶會從自己原本的 `account_type` 分組（例如信用卡）整段搬到父層 `account_group` 所在分組底下渲染**，因此分組標題徽章數字必須用「過濾掉已被巢狀吸收的子帳戶後」的 `topLevelCount`，不能直接用 `group.rows.length`（否則會出現「信用卡 (2)」卻只看到 1 行的落差）；某個 type 分組的帳戶全部被吸收成子帳戶時，該分組標題本身也要整個跳過不渲染（`topLevelCount === 0` 時 return null）。`AccountPickerDialog` 新增了 `selectMode`（隱藏編輯/刪除按鈕、`account_group` 列點擊只展開不選中）與 `hiddenBadge`（顯示「已隱藏」灰標）兩個 prop，之後改動 `AccountListRow` 時要同時檢查這兩個呼叫點都還正常。Phase 8（信用卡回饋規則的帳戶選擇）規劃之後也改用 `AccountPickerDialog`，目前**尚未做**。
* **交易表單「帳戶必選」的新舊資料相容邏輯**（2026-08-08 Phase 11 補充）: `TxForm` 新增 `original_account_name` 欄位，記錄編輯既有交易時「打開表單當下」的原始帳戶名（新建交易固定是 `''`）。`TransactionsPage.tsx::onSaveTransaction`/`GlobalEditDialogs.tsx::handleSaveTx` 兩處的必選校驗都是「非轉帳 + 帳戶欄位為空 + (新建 或 目前值跟 `original_account_name` 不同)」才擋——這樣舊版 mobile 匯入的無帳戶交易只改其它欄位（不碰帳戶）時能正常存檔，但只要使用者「主動」把帳戶從有改成沒有、或新建交易不選帳戶，都會被擋。這兩處校驗邏輯是手動維護的兩份，不是共用函式，之後改動其中一處記得同步改另一處。
* **分類/標籤選擇器的表單內新增**（2026-08-08 Phase 11 補充）: `CategorySelector.tsx`/`TagSelector.tsx` 新增可選的 `onCreateNew?: (name: string) => void | Promise<void>` prop——搜尋框輸入的字串在既有清單裡找不到完全同名（大小寫不敏感）項目時顯示「新增「xxx」」內嵌按鈕。這兩個 selector 元件本身**不呼叫任何 API**，只負責 UI 觸發；實際建立分類/標籤的邏輯（呼叫 `createCategory`/`createTag` + `retryOnConflict` + 樂觀更新本地字典狀態）由呼叫方實作並往下傳（見 `TransactionsPage.tsx::onCreateTxCategory`/`onCreateTxTag`、`GlobalEditDialogs.tsx` 同名函式），因為只有呼叫方知道要用哪個 `ledgerId`/`token`。新增其它需要「表單內建立」的選擇器時比照這個「元件只管 UI 觸發、呼叫方管 API」的分工模式。
* **`schemas.py` 用 Edit 工具插入新 class 時，`old_string` 必須含到該 class 的最後一個欄位**（2026-08-08 Phase 13 實測踩到）: `WriteDebtUpdateRequest` 最後一個欄位是 `closed_at`（帶一段多行註解），插入 `WriteProjectCreateRequest`/`WriteProjectUpdateRequest` 時 `old_string` 只匹配到前面的 `note: str | None = None` 就收尾，結果把新 class 插在 `note` 和 `closed_at` 之間，把 `closed_at` 切到 `WriteDebtUpdateRequest` 外面變成一段孤立、永遠不會被讀到的宣告——這個 class 從此收不到 `closed_at` 欄位，`PATCH .../debts/{id}` 帶 `closed_at` 的請求會被 pydantic 靜默丟棄（不報錯，因為 extra field 預設被忽略），導致「結案/重新開啟」功能整個失效，且沒有任何錯誤訊息或型別檢查會抓到這個問題——是連續兩個既有 pytest 測試從綠轉紅才發現的（`test_close_and_reopen_debt_overrides_status`/`test_send_due_debt_reminders_skips_closed_debt`）。**教訓**：在一長串同類 `class Write*Request` 定義中間插入新 class 前，先確認 `old_string` 涵蓋到緊鄰那個既有 class 的**最後一行**（不是「看起來像結尾」的某個欄位），插入後最好立即跑一次該 class 相關的既有測試而不是只跑新測試，才能抓到「改到別人」的靜默破壞。

---

## 功能模組與文件地圖

更詳細的業務架構、歷史背景與測試計畫請參閱 `docs/` 文件：

| 模組 / 主題 | 核心職責與程式碼入口 | 對應說明文件 |
| :--- | :--- | :--- |
| **資料庫 Schema（維運參考）** | `src/models.py` 全部 39 張表的中文表名/欄位說明 | [`docs/DATABASE_SCHEMA.md`](./docs/DATABASE_SCHEMA.md) |
| **同步架構 (Sync)** | `routers/sync/`, `sync_applier.py`, `ws.py` (LWW, Change ID, 鎖粒度) | [`docs/SYNC_ARCHITECTURE.md`](./docs/SYNC_ARCHITECTURE.md) |
| **Moze 功能對標 (Phase 0~5)** | 週期性收支, 分期付款, 拆帳, 借還款, 信用卡, 範本 | [`docs/MOZE_FEATURE_GAP_SD.md`](./docs/MOZE_FEATURE_GAP_SD.md) |
| **信用卡與群組模型** | `services/credit_card_billing.py`, `credit_card_autopay.py` | [`docs/PH4_CREDIT_CARD_WEB_UI_MANUAL_TEST_PLAN.md`](./docs/PH4_CREDIT_CARD_WEB_UI_MANUAL_TEST_PLAN.md) |
| **信用卡紅利回饋** | `routers/write/card_reward_rules.py`, `services/card_rewards.py` | [`docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_TEST_PLAN.md`](./docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_TEST_PLAN.md) |
| **對帳、延後入帳與報表** | `services/deferred_posting.py`, `read/workspace.py` | [`docs/PH5_RECONCILIATION_WEB_UI_MANUAL_TEST_PLAN.md`](./docs/PH5_RECONCILIATION_WEB_UI_MANUAL_TEST_PLAN.md) |
| **專案 (Projects, Phase 13)** | `routers/write/projects.py`, `read/ledgers.py::list_projects`（前端獨立路由 `/app/projects`，頂部導航緊鄰「標籤」右側，`nav.ts` NAV_GROUPS 的 bookkeeping 組；原本設計放在標籤分頁底下的子分頁，後續改成獨立入口） | [`docs/PH13_PROJECT_SD.md`](./docs/PH13_PROJECT_SD.md), [`docs/PH13_PROJECT_WEB_MANUAL_TEST_PLAN.md`](./docs/PH13_PROJECT_WEB_MANUAL_TEST_PLAN.md) |
| **比較報表矩陣 (Web)** | `read/workspace.py::comparison_matrix` / `comparison_matrix_cell`(與 `/workspace/comparison` 共用 `_stat_legs` 口徑),前端 `pages/sections/ComparisonReportPage.tsx`(路由 `/app/comparison-report`) | [`docs/COMPARISON_MATRIX_SD.md`](./docs/COMPARISON_MATRIX_SD.md) |
| **授權金鑰 / 最低可同步版本** | `services/license.py`（強制點在 `deps.get_current_user` / PAT / `ws.py`），`routers/license.py`, `routers/admin_licenses.py`；新增端點務必經過 `get_current_user`，`tests/test_license_route_audit.py` 會擋 | [`docs/LICENSE_KEYS.md`](./docs/LICENSE_KEYS.md) |
| **股票持股 (2026-09-28)** | `services/securities/`(報價/搜尋/持股計算;`dividends.py` 除權息同步 + 待確認股利)、`routers/read/securities.py`、`routers/write/stock_trades.py`(含待確認股利 confirm/dismiss)、`snapshot_mutator.create_stock_trade`;sync entity `stock_trade`(ledger-scoped);`pending_dividends` 是 server 專屬狀態不進 sync;前端 `/app/investments` | [`docs/STOCK_HOLDINGS_SD.md`](./docs/STOCK_HOLDINGS_SD.md) |
| **部署與備份** | rclone 多遠端加密備份, docker-compose, Alembic | [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md), [`docs/MIGRATION.md`](./docs/MIGRATION.md) |

---

## 前端與移動端結構

* **Web 前端** (`frontend/apps/web`): Vite + React + TypeScript + Tailwind + shadcn。
* **共用 Package**:
  * `frontend/packages/api-client`: 與 Server 互動的型別化 API Client。
  * `frontend/packages/ui`: 通用 UI 組件。
  * `frontend/packages/web-features`: 跨頁面業務邏輯。
* **Mobile 端 (Flutter)**: 原始碼位於 `../BeeCount/` 專案庫中。同步契約規範請參閱 Mobile 倉內之 `CLAUDE.md`。
