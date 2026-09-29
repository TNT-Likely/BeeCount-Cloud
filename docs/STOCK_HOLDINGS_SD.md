# 股票持股(Stock Holdings)設計文件

日期:2026-09-28(Phase 1 持股/報價;Phase 2 股利見 §7)
App 端對應文件:BeeCount 主 repo `docs/changes/2026-09-28-stock-holdings.md`。

## 需求與已確認決策

- 買股票 = 一筆轉帳(交割帳戶 → 投資理財帳戶),同時記下**股數**;市值 = 股數 ×
  現價。
- 股票**不計入淨資產**,另外顯示「投資市值(預估)」。
- 手續費/交易稅/股利手續費/預扣稅/二代健保全部**使用者自訂**,不寫死。
- **沿用既有 `investment`(投資理財)帳戶類型**,不新增類型。
- **Web 跟 App 功能對等**;Server 另外負責:證券清單、報價、除權息資料抓取,排程,
  股利偵測與通知。
- 資料來源:免費 + Provider 抽象(台股證交所/櫃買 OpenAPI;其它市場 Yahoo
  Finance 非官方端點)。
- 報價:收盤後抓收盤價;App/Web 開啟時盤中快取超過 15 分鐘就補抓。
- 股利(Phase 2,§7):偵測 → 待確認通知 → 使用者確認實收或再投入後由 Server 建交易。

## 1. 資料表

### 全域市場資料(不分 user、不進 sync,比照 `exchange_rate_cache`)

| 表 | 說明 |
|---|---|
| `securities` | 證券清單,業務鍵 `(market, symbol)`。台股由證交所/櫃買全市場收盤檔同步(搜尋時清單不存在或 >7 天就同步;任一市場少於 300 檔視為沒同步過),其它市場在 Yahoo 搜尋/報價時 upsert。 |
| `security_quotes` | 最新報價,每檔一行;`session` = `close`/`intraday`/`manual`。 |

市場代碼(`services/securities/markets.py`,App `lib/services/investment/markets.dart`
同一份清單):`TW` `TWO` `US` `HK` `JP` `SS` `SZ` `KS` `KQ` `LSE`。代碼寫進使用者
資料後不能改名。

### 使用者資料(sync)

- **`read_stock_trade_projection`**:新的 ledger-scoped sync entity `stock_trade`,
  PK `(ledger_id, sync_id)`。wire key 見 `sync_applier.py::_LEDGER_MERGE_SPECS["stock_trade"]`。
- **`user_account_projection.investment_settings_json`**:投資理財帳戶費用設定,
  wire `investmentSettings`(物件)。允許的 key 由
  `snapshot_mutator.normalize_investment_settings` 過濾(未知 key 丟棄)。

**持股不落庫**:`services/securities/holdings.py` 由明細即時算(移動平均成本法)。
App 端 `holdings_calculator.dart` 是同一套算法,兩邊共用
`tests/fixtures/stock_holdings_vectors.json` 測試向量(App repo 有一份相同的)。

## 2. 買賣 = 轉帳(`snapshot_mutator.stock_trade_tx_fields`)

| 動作 | 轉帳方向 | 金額 |
|---|---|---|
| 同幣別買進 | 交割 → 投資 | `amount`=股數×價,`feeAmount`=手續費 |
| 同幣別賣出 | 投資 → 交割 | `amount`=股數×價,`discountAmount`=手續費+稅 |
| 跨幣別買進 | 交割 → 投資 | `amount`=`settlement_amount`,`toAmount`=股數×價+手續費 |
| 跨幣別賣出 | 投資 → 交割 | `amount`=股數×價−費−稅,`toAmount`=`settlement_amount` |

App 的 `stock_trade_tx_mapper.dart` 同規則。`create_stock_trade`/`update_stock_trade`/
`delete_stock_trade` 在同一個 `_commit_write` 裡一起處理明細與轉帳;單筆刪交易
(`_commit_write_fast_tx`)走 `_cascade_delete_linked_stock_trades`,批量刪除走
`delete_transaction` mutator,兩條路都會連帶刪明細。

注意:`create_transaction` 內部會 deepcopy snapshot——新建交易後必須改用它回傳
的 snapshot(`_apply_stock_tx` 已處理)。

賣超由 router `_assert_can_sell` 擋(跨該使用者所有帳本彙總,帳戶是 user-global)。

## 3. API

| 端點 | 說明 |
|---|---|
| `GET /read/securities/search?q=&market=` | 台股走本地官方清單(可打中文名稱);ASCII 查詢另外打 Yahoo search。 |
| `GET /read/securities/quotes?symbols=TW:2330,US:AAPL&refresh=` | 最多 100 檔;`needs_refresh`:沒快取、盤中 >15 分鐘、或 >12 小時。上游失敗回舊值並標 `stale`。 |
| `GET /read/workspace/holdings?account_id=&refresh=` | 持股 + 市值 + 損益,並用主幣別折算(缺匯率剔除列在 `missing_rates`,不按 1.0 裸加,同淨資產卡)。 |
| `GET /read/ledgers/{id}/stock-trades` | 明細列表。 |
| `POST/PATCH/DELETE /write/ledgers/{id}/stock-trades[/{trade_id}]` | Web 買賣。PATCH 不能改 trade_type/帳戶/標的。 |

## 4. 排程

`security_quote_close`(`services/scheduled_jobs.py`,每 5 分鐘):各市場過了
`close_time + close_fetch_delay`(台股 15:00 台北、美股 16:30 紐約,`zoneinfo`
處理夏令)才抓,只抓有人持有的標的。台股一次抓證交所/櫃買全市場(失敗退回
Yahoo 逐檔)。資料日期不是今天(資料晚發布/假日)時,門檻後 3 小時內每次都重試。
不做國定假日日曆——假日抓到的就是前一交易日收盤,結果是對的。

## 5. Web 前端

- `apps/web/src/pages/sections/InvestmentsPage.tsx`(路由 `/app/investments`,
  頭像下拉選單「投資」):總市值、各投資理財帳戶持股表、展開看跨帳本交易紀錄、
  新增/編輯/刪除交易、費用設定。
- `apps/web/src/components/dashboard/InvestmentValueCard.tsx`:資產頁「投資市值
  (預估)」卡(`refresh=false` 只讀快取)。
- `packages/web-features/src/lib/investment.ts`:市場清單、預設費率、手續費試算
  (跟 App `InvestmentSettings` 同規則)。
- `AccountsPanel.tsx`:新建投資理財帳戶預設 `include_in_total=false`。

## 6. 實測踩過的坑

- Yahoo chart `chartPreviousClose` 是「圖表區間開始前」的收盤價,`range` 必須是
  `1d`,不然今日漲跌是跟好幾天前比。
- 證交所全市場檔(~318KB)常超過 10 秒,官方來源用 30 秒逾時(`BULK_TIMEOUT`)。
- 查報價時零星建立的台股證券(英文名)曾讓清單新鮮度判斷誤判,導致永遠不同步
  官方清單;現在上市/上櫃分開計數。
- Web PWA Service Worker 會快取舊 bundle,手動驗證前先 unregister + 清 cache。

## 7. Phase 2 股利(2026-09-28)

App 端對應文件:`docs/changes/2026-09-28-stock-dividends.md`。

### 資料表(migration `0059_stock_dividends`)

| 表 | 說明 |
|---|---|
| `security_dividend_events` | 除權息事件,全域市場資料。業務鍵 `(security_id, ex_date)`;`cash_per_share`、`stock_per_share`(每股配幾股)、`source`。**官方來源(twse/tpex)寫過的事件不會被 Yahoo 蓋掉**;預告表先公告日期、金額後補,所以金額會被更新。 |
| `pending_dividends` | 待確認股利,**server 專屬狀態,不進 sync**。唯一鍵 `(user_id, account_sync_id, event_id)`;`status` = pending / confirmed / dismissed;`est_*` 估算值;`created_trade_ids`(JSON)。`ledger_id` = 該帳戶這檔最近一筆明細所在的帳本(入帳落這本)。 |

### 資料來源(`services/securities/providers/`)

- 證交所 `exchangeReport/TWT48U_ALL`、櫃買 `tpex_exright_prepost`(除權除息預告表,
  整批一次;有配股率)。`StockDividendRatio` 是截斷過的小數(0.04999999 = 每千股 50 股)。
- Yahoo chart `events=div&range=6mo`(逐檔;美股與其它市場,也拿來補台股最近半年歷史)。
  `date` 是除息日開盤的 epoch 秒,**要換成交易所當地日期**(直接取 UTC 日期,紐約會
  差一天);金額有浮點雜訊(5.000011),四捨五入到 4 位。Yahoo 沒有配股、沒有發放日。

### 排程(`services/securities/dividends.py`)

- `security_dividend_sync`(每 6 小時):同步「所有交易過的標的」(不只目前持有的——
  除息日前持有、之後賣光的也要算)。
- `security_dividend_detector`(每小時):除息日當天(市場當地日期)起、
  `DETECT_LOOKBACK_DAYS=60` 天內、金額已公告的事件 → 依「交易日期(換成市場當地日期)
  < 除息日」的明細算每個投資帳戶的持股 → 建 pending + 通知(category=`dividend`,
  payload 帶 `pendingDividendId`/`accountId`/`ledgerId`)。
  - pending 狀態每次重算估算值(使用者補記除息前的買進會跟著變);持股變 0 就刪掉。
  - dismissed 不再動。
  - confirmed 但找不到任何 `dividendEventRef` 指向它的明細(使用者刪了)→ 退回
    pending,**不重發通知**。
  - 回溯上限是刻意的:使用者現在補記一年前的期初持股,不該一口氣跳出一堆陳年股利。

### 實收估算(`dividends.estimate_dividend`)

總額 = 股數 × 每股股利(TWD/JPY/KRW 捨去到整數);預扣稅 = 總額 × `dividendWithholdingRate`;
二代健保 = 總額 ≥ `nhiThreshold` 時 × `nhiSupplementRate`;手續費 = `dividendFeeFixed` +
總額 × `dividendFeeRate`;實收 = 總額 − 以上。配股 = 股數 × 配股率(台股四捨五入到
千分位再捨去)。市場預設值同 App `InvestmentSettings.defaultsFor`。**App
`dividend_estimate.dart`、Web `estimateDividend` 同規則,三端測試用同一組數字。**

### 交易(`snapshot_mutator._apply_dividend_tx`)

- `cash_dividend` → `income` 入 `settlement_account_id`(任何非群組帳戶),金額 = 實收。
- `reinvest` → `income` 入投資理財帳戶本身,金額 = 成本(股數×價格+手續費)。
- 分類固定「股利」(`card_rewards.ensure_dividend_category`,同名 income 分類找/建;
  App 用同名找,不會重複建)。
- 入帳帳戶幣別 ≠ 證券幣別:要 `settlement_amount`。入帳帳戶幣別 ≠ 帳本本位幣:router
  用手動匯率 → 自動匯率補 `currencyCode`/`nativeAmount`(`stock_trades._fx_rate_to_base`,
  用同一個 DB session,不走 `SessionLocal`)。
- 收入金額四捨五入到分;`stock_trade.amount` 保留原精度。
- 這兩種類型也開放 `POST /write/ledgers/{id}/stock-trades` 手動建(資料源漏抓時補記)。

### API

| 端點 | 說明 |
|---|---|
| `GET /read/securities/pending-dividends?status=pending\|confirmed\|dismissed\|all` | 只回自己的。帶 `settlement_account_id` 預設值(費用設定的交割帳戶 → 最近一筆買賣的交割帳戶,`dividends.default_receiving_account`)、`quote_price`(快取報價,再投入預填)。 |
| `POST /write/securities/pending-dividends/{id}/confirm` | `PendingDividendConfirmRequest`:`mode` cash/reinvest、可覆寫每股股利/手續費/稅、入帳帳戶、實際入帳金額、再投入股數/價格/手續費、配股股數(0 = 不記)、日期。在同一個 `_commit_write` 建明細 + 交易並把 pending 標成 confirmed;重複確認 409。 |
| `POST /write/securities/pending-dividends/{id}/dismiss` · `/restore` | 忽略 / 放回待確認。 |
| `GET /read/securities/dividend-events?symbol=TW:2330` | 某檔的除權息事件。 |

### Web

`apps/web/src/pages/sections/PendingDividendsPanel.tsx`(投資頁的待確認股利區塊 + 確認
對話框)、`InvestmentValueCard` 的「N 筆股利待確認」、`NotificationBell` 的
`pendingDividendId` 跳轉、`StockTradeDialog` 多了現金股利/股利再投入兩種類型。

### 已知限制

- 沒有發放日(兩個資料源都沒有),入帳日期預設除息日。
- 再投入只能全額,不支援部分現金部分再投入;再投入模式不記預扣稅。

## 8. 費用對帳(2026-09-28)

使用者拿永豐對帳單比對後修的四件事(App 端完整說明:App repo
`docs/changes/2026-09-28-stock-fee-reconciliation.md`):

- **`services/securities/trade_fees.py`**(只用標準函式庫,`snapshot_mutator` 會
  import):`security_kind`(台股 `00` 開頭 = ETF、結尾 `B` = 債券 ETF)、
  `round_money` / `stock_gross`(TWD/JPY/KRW 無條件捨去,其它四捨五入到分;捨去前
  先 round 到 6 位清浮點殘渣,不用 Python 銀行家捨入)、`sell_tax_rate_for`、
  `suggest_fee`、`suggest_sell_tax`、`estimate_sell`。
- **`snapshot_mutator.stock_trade_amount` / `stock_trade_tx_fields`**:價金改用
  `stock_gross`。0050 買 50 股 @97.45 手續費 6 → 轉帳 4,872 + feeAmount 6,明細
  amount 4,878。美股零碎股價金也四捨五入到分了(1.875885 → 1.88)。舊資料不重算。
- **`normalize_investment_settings`**:接受 `etfSellTaxRate`、`bondEtfSellTaxRate`
  (float)、`pnlAfterSellCosts`(bool,預設開、關掉才存 false)。
- **`/workspace/holdings`**:每檔多了 `est_sell_fee` / `est_sell_tax` / `net_value` /
  `pnl_after_sell_costs`;`market_value` 也用 `stock_gross` 取整;`unrealized_pnl` 依
  帳戶設定用淨值或毛市值算。帳戶多了 `net_value_by_currency` /
  `valuation_by_currency`;總計多了 `total_net_value` / `pnl_after_sell_costs`,
  `total_unrealized_pnl` 改成「valuation − 成本」。
- **Web**:`lib/investment.ts` 同一套函式(`securityKind`、`stockGross`、
  `sellTaxRateFor`、`estimateSell`),新增交易時自動帶入現價
  (`fetchSecurityQuotes`),賣出時顯示「證交稅率 0.1%(ETF)」;費用設定多了兩個
  稅率欄位和損益開關;持股表多一欄「預估淨值」。
- 三端共用測試數字:`tests/test_trade_fees.py`、Web `investmentFees.test.ts`、App
  `investment_settings_test.dart`「台股費用對帳」。

## 9. 轉帳選到投資理財帳戶時導向買進/賣出(2026-09-28)

App 端完整說明:App repo `docs/changes/2026-09-28-transfer-stock-account-redirect.md`。

轉帳選到投資理財帳戶以前不會走 `stock_trade`,只是一筆股數對不上的裸轉帳。
改成轉入投資理財帳戶 = 買進、轉出投資理財帳戶 = 賣出,另一側帳戶帶當交割戶,
跟直接在「投資」頁按「新增交易」殊途同歸。

- **Web 選不到投資理財帳戶的 bug**:`TransactionsPage.tsx::txWriteAccounts`
  之前無條件排除所有估值帳戶類型(含 `investment`),連轉帳都選不到,一併修掉
  ——`tx_type === 'transfer'` 時放行 `investment`,其它估值類型維持排除。
- **`InvestmentsPage.tsx`**:`StockTradeDialog`/`TradeDialogState`/
  `CreatableType` 改 `export` 給 `TransactionsPage.tsx` 重用;
  `TradeDialogState` 多一個 `initialSettlementAccountId`(轉帳過來時把使用者
  已選的另一側帳戶帶當交割戶,優先於帳戶費用設定裡的預設值),
  `initial.market`/`symbol` 改成可選。
- **`TransactionsPage.tsx`**:監看轉帳表單的 `from_account_name`/
  `to_account_name`(表單存名稱,不是 id),判斷出買/賣後拉一次這檔持股
  (`fetchWorkspaceHoldings({accountId, refresh: false})`,賣出時 client 端
  賣超檢查用,`stock_trades.py` 還會再驗一次)再開 `StockTradeDialog`;
  存檔成功關掉整個交易 dialog 並刷新列表,取消只清掉剛選的那一側帳戶。
  編輯既有交易時,拿對話框打開那一刻的兩個帳戶名稱當基準,值沒變就不導向
  (避免這個功能上線前建立、沒有 `stock_trade` 的裸轉帳一打開編輯就被強制
  彈買賣 dialog),使用者主動把某一側改成別的投資理財帳戶才會觸發。
- App 端行為一致:`transfer_form.dart::_pickAccount` 選到投資理財帳戶就導向
  `StockTradeEditorPage`(新增 `initialSettlement` 參數帶交割戶),只在使用者
  主動選帳戶時觸發,編輯既有轉帳單純載入顯示不會觸發。

## 10. 股票定期定額投資(2026-09-28)

App 端完整說明:App repo `docs/changes/2026-09-28-stock-dca-recurring.md`。

使用者需求:新增「定期定額投資」,因為定期定額手續費規則常跟單筆買進不同
(免手續費/不同最低手續費),要能各自設定;同時沿用既有的「週期性收支」
管理介面,用分類(一般交易/股票定期定額)區分管理。

**資料模型**:`read_recurring_rule_projection` 新增 `kind`
(`'general'`/`'stock_dca'`,預設 `'general'`)、`market`/`symbol`/
`security_name`(同 `stock_trade` 對應欄位,只有 `kind='stock_dca'` 才有
值)、`stock_fee_rate`/`stock_fee_min`(規則層級手續費覆寫,皆為 `null` 時
沿用投資理財帳戶的 `investment_settings_json` 預設值)。`kind='stock_dca'`
規則必定 `tx_type='transfer'`——`from_account_id`=交割帳戶、`to_account_id`=
投資理財帳戶(必須是 `account_type='investment'`,建立時 `_assert_account_is_
investment` 擋)、`amount`=每期投入金額(以證券幣別計,**v1 不支援交割帳戶
跟證券不同幣別**)。

**建規則**(`routers/write/recurring_rules.py::create_recurring_rule_ep`):
`kind='stock_dca'` 時額外驗證 tx_type/market/symbol/from-to 帳戶,`to_
account_id` 必須是投資理財帳戶;因為必為 `transfer`,天然沿用既有「transfer
不預生成」分支,不會像一般收支規則那樣建立當下就批次生成 occurrence(股數
要看到期當下的報價才算得出來)。

**到期物化**(新函式 `services.recurring_materializer.materialize_due_stock_
rules`,跟 `materialize_due_transfer_rules` 平行、掛在同一個 15 分鐘排程
`stock_dca_materialization`):到期當下讀本地 `security_quotes` 快取抓報價
(只讀快取,不在批次任務裡現場打上游 API),股數 = 每期投入金額 / 報價
(允許碎股);手續費 = 規則覆寫或投資理財帳戶預設(`trade_fees.
resolve_trade_settings`);檢查交割帳戶當下餘額 ≥ 投入金額+手續費。任一條件
不滿足就跳過(`quote_unavailable`/`insufficient_funds`,各自去重通知,下次
15 分鐘重試同一期)。滿足時直接寫 `read_stock_trade_projection` +
綁定的轉帳交易(不透過 `snapshot_mutator.create_stock_trade`——那套「載入
整份 ledger snapshot 再 diff」的機制對批次任務太重,同 `materialize_due_
transfer_rules` 對一般轉帳規則的既有做法)。**`materialize_due_transfer_
rules` 的查詢額外排除 `kind='stock_dca'`**——這類規則雖然也是
`tx_type='transfer'`,但要生成 `stock_trade` 明細,不能被當成普通自動扣繳
處理掉。

**Web 前端**(`RecurringRulesPanel.tsx`):新建/編輯 Dialog 最上方加「分類」
切換(一般交易/股票定期定額,建立後鎖定不可改),選股票定期定額時：
`tx_type` 鎖定 `transfer`,市場/代號/名稱三個欄位(建立後鎖定),「投資理財
帳戶」(`to_account_id`,帳戶選擇器只列 `account_type==='investment'`)、
「交割帳戶」(`from_account_id`,任何非群組帳戶)、自訂手續費開關(關閉時
`stock_fee_rate`/`stock_fee_min` 傳 `null`,沿用帳戶預設)。列表新增「全部/
一般交易/股票定期定額」篩選 tab;股票規則卡片圖示改成 `trending_up` + 
「股票定期定額」徽章,標題顯示代號/名稱。展開「已生成交易」清單時,股票
規則的每期**不提供**編輯/刪除/連同以後(那筆 occurrence 其實是
`stock_trade` 連帶的轉帳交易,直接改會讓 `tx_sync_id` 對不上),顯示「請至
投資頁管理」提示。

**已知限制**:不支援跨幣別 DCA;不會把使用者已輸入的轉帳金額/備註帶到 DCA
規則(這是獨立的新建流程)。

### 10.1 2026-09-29 修正(使用者回報 + 重新驗證)

App 端說明:App repo `docs/changes/2026-09-29-stock-dca-fixes.md`(App 的同步
欄位、固定 syncId、7 天補期上限、UI 入口)。

**回報 → 根因**

- Web「新增規則」按不下去:`RecurringRuleForm.market` 預設空字串,下拉選單只是
  畫面上顯示 TW(`value={form.market || 'TW'}`),`canSubmit` 要求
  `Boolean(form.market)` 永遠 false。
- Web 輸入代號不會出現股票:DCA 表單的代號只是純文字框,沒接
  `/read/securities/search`。
- App 建的規則在 Web 圖示不對 / 排程不生成:App 沒推 `kind` 等欄位,Cloud 當成
  普通 transfer;同時 **`sync_applier._LEDGER_MERGE_SPECS["recurring_rule"]` 跟
  `snapshot_builder` 都沒登記這六個欄位**——Web 任何 PATCH(snapshot_mutator
  在沒有 kind 的快照上改完、整筆 upsert)或舊版 App 的 partial push 都會把規則
  沖回 `kind='general'`(CLAUDE.md SOP 第 3、6 點漏做)。
- 排程只讀報價快取,而 `security_quote_close` 只抓已持有標的:第一期還沒買的
  代號永遠 `quote_unavailable`。
- `compute_account_balance` 沒排除 `happened_at > now` 的交易(一般收支規則會
  預生成未來 12 個月的 occurrence)、也沒算轉帳手續費/折損,跟 Web 顯示的帳戶
  餘額不一致,會誤判交割戶餘額不足。

**修正**

- `sync_applier.py`、`snapshot_builder.py`:登記 `kind/market/symbol/
  securityName/stockFeeRate/stockFeeMin`(快照對 `kind='general'` 不寫 key,
  維持既有規則形狀)。
- `recurring_materializer.py`:
  - `compute_account_balance` 對齊 `list_workspace_accounts`(`happened_at <=
    now` + fee/discount),也影響自動扣繳、信用卡自動扣繳、餘額調整——三者本來
    就宣稱跟 workspace 同一套公式。
  - `materialize_due_stock_rules`:先挑出到期規則的標的呼叫 `_refresh_quotes`
    (`quotes.get_quotes(refresh=True)`,會 rollback,所以在任何寫入前做、做完
    重查規則);每期用 `stock_dca_occurrence_ids`(uuid5,App 同算法)當
    tx/trade syncId,已存在就只推進度;超過 `STOCK_DCA_MAX_CATCH_UP`(7 天)的
    期數略過不買並發 `stale_skipped` 通知。新增 `next_pending_occurrence`。
- `quotes.held_keys`:加入啟用中 stock_dca 規則的標的,收盤排程每天抓。
- `routers/write/recurring_rules.py`:
  - 建立時擋交割帳戶跟證券不同幣別(`_assert_stock_dca_settlement_currency`,
    v1 本來就不支援,以前只寫在文件裡)。
  - 更新 stock_dca 時帳戶限制照樣成立(投資帳戶、from≠to、同幣別)。
  - transfer/stock_dca 規則第一期之後改 `next_run_at`:晚於
    `generated_until_at` → 清掉進度從新時間起算(`__reset_generated_until_at`
    只在 mutate payload 內部傳,不暴露在 request schema);早於 → 400。以前改了
    沒有任何效果。
- `read/ledgers.py::list_recurring_rules`:新增 `upcoming_run_at`(transfer/
  stock_dca 真正的下一期),Web 卡片與編輯表單改顯示它。
- Web:
  - 新元件 `web-features/components/SecuritySymbolField.tsx`(市場 + 代號搜尋
    建議),股票交易 dialog 與週期性交易的 DCA 表單共用。
  - `RecurringRulesPanel.tsx`:市場預設依帳本幣別/選中的投資帳戶;交割帳戶只列
    同幣別;選投資帳戶自動帶入它設定的預設交割戶;開自訂手續費預填帳戶生效中的
    費率(以前預填空、存檔變 0)並說明折扣會再乘上去。
  - `InvestmentsPage.tsx`:帳戶卡片新增「定期定額」按鈕、持股展開列新增
    「定期定額」快捷;股票交易 dialog 多一個「單筆交易 / 定期定額」切換,定期
    定額模式直接建立 `kind='stock_dca'` 規則(含每期扣款試算);帳戶卡片下方列
    出這個帳戶的定期定額計畫(連到週期性交易頁管理)。

**入口**:投資 → 帳戶卡片「定期定額」/ 持股列「定期定額」/ 新增交易 dialog
的「定期定額」切換;週期性交易 → 「股票定期定額」篩選。

**測試**:`tests/test_recurring_stock_dca.py` 新增 11 個回歸測試(Web PATCH /
App partial push 不沖掉 kind、transfer 排程不處理 stock_dca、run-now 補抓報價
生成、App 已生成的期數不重複、未來預生成支出不影響餘額、跨幣別擋下、收盤標的
含 DCA、uuid5 固定值、7 天補期、`upcoming_run_at` 與改下次執行時間)。

**沒做**:台股整數股 → 已在 §10.2 改掉。

### 10.2 2026-09-30 台股只買整數股

使用者確認:台股不論定期定額或盤中零股都進證交所撮合,最小單位 1 股,券商
不可能把 0.5 股放進集保戶頭;美股(含複委託)是券商吃下整股再切碎分配,允許
碎股。所以依市場拆兩種算法,集中在 `trade_fees.stock_dca_order`(App
`stockDcaOrder`、Web `stockDcaOrder` 同算法,三端測試用同一組數字):

| | 整數股(`STOCK_DCA_WHOLE_SHARE_MARKETS` = TW/TWO) | 碎股(其它市場) |
| --- | --- | --- |
| 每期金額的意思 | **含手續費**的扣款上限 | 成交價金,手續費另計(跟 9/28 版本相同) |
| 股數 | 使「價金 + 手續費 ≤ 金額」的最大整數 | 金額 ÷ 股價 |
| 手續費 | 依**實際**成交價金計 | 依金額計 |
| 轉帳金額 / feeAmount | 實際價金 / 手續費,零頭留在交割帳戶 | 金額 / 手續費 |

整數股的起點用券商公式 ⌊(金額 − 以整筆金額估的手續費) ÷ 股價⌋,再往上試
一股(實際價金較小、手續費也可能較小,省下的錢可能夠多買 1 股),例如 1,000
@10、最低 20:⌊980 / 10⌋ = 98 股,98 股 980 + 20 = 1,000 剛好。

連 1 股(含手續費)都買不起 → `amount_too_small`:**略過這一期**(推進
`generated_until_at`)並通知,不像「餘額不足」停在原期重試——價格不會因為重試
變低,停住會讓後面的期數全部卡死。job summary 多 `skipped_too_small`。

順便修正:`emit_tx` 以前不帶帳戶名稱,排程生成的交易(定期定額、自動扣繳、
refill 視窗)在讀取 API/Web 帳戶明細都顯示「- → -」(projection 直接存 payload
的 `fromAccountName`/`toAccountName`)。現在呼叫方沒帶就依 syncId 從
`user_account_projection` 補上。只影響新生成的交易,舊的那幾筆不回填。

UI:Web 股票交易對話框的定期定額試算改成「可買 N 股、扣款 X(含手續費 Y)、
Z 不扣款」,買不起 1 股時紅字提示;週期性交易表單與 App 編輯頁都加上整數股/
碎股說明(App 也會用快取報價試算)。

**刻意沒做**:港股/日股/陸股/韓股仍當碎股處理(它們有「一手」或券商碎股方案,
各券商不同),需要時把市場代碼加進三端的清單即可。台股定期定額實際手續費
常見最低 1 元,帳戶預設最低 20 元是給單筆交易用的,要在規則打開「自訂手續費」
自己改。

### 10.3 2026-09-30 App↔Web 定期定額顯示、代號自動帶入、批次期初持股

使用者回報三件事:

1. **Web 建的定期定額在 App 顯示成普通轉帳,App 建的在 Web 也是轉帳、圖示還壞掉。**
   - App 端:使用者跑的 App 是 `release/3.7.0` build,§10.1 的六個同步欄位修正還在
     App 另一個分支(`fix/stock-dca`)沒合進去——Cloud 這邊的 payload/快照本來就有
     `kind` 等欄位(`snapshot_builder`、`sync_applier`、`projection` 都有),不需要改。
     App 合併後,舊版 App 建的規則要在 App 打開按一次儲存才會重推。
   - Web 轉帳規則的圖示用了 `sync_alt`,但 `index.html` 的 Material Symbols 是子集
     (`icon_names=`),沒有這個字,ligature 退回成文字「_ALT」疊在圖示上。改用子集裡
     有的 `swap_horiz`(`RecurringRulesPanel.tsx`)。其它 `material-symbols-outlined`
     的字面值掃過一次,只有這一個不在子集裡。
   - 使用者在「間隔」填了 30(想表達每月 30 號),規則變成每 30 個月一次(Web 顯示
     「每月 ×30 · 下次 2029/3/30」)。資料本身沒錯,是欄位語意不清:label 改成「間隔
     (每 N 個週期)」,下方即時顯示「=每 30 個月執行一次。要指定每月幾號,請設定
     『下次執行時間』的日期」(週期性交易表單 + 投資頁定期定額 dialog 都有);規則列
     的「每月 ×30」改成「每 30 個月」。
2. **精準輸入代號(0056)離開輸入框後應該自動帶入名稱**(同新增買入)。
   `SecuritySymbolField` onBlur 時用最近一次搜尋結果(沒有就立刻搜一次)找代號完全
   相符的標的(台股 TW/TWO 互通,其它市場要同市場),找到就等同點選(帶入名稱/市場/
   幣別)。這個元件是股票交易 dialog、定期定額 dialog、週期性交易表單共用的,三處
   一起生效。同一檔點選過後再 blur 不會重複覆蓋使用者改過的名稱。
3. **期初持股的快速輸入/匯入。** 建議「一檔一筆、填平均成本」而不是逐筆補記過去的
   買進:持股成本/未實現損益完全相同(持股計算本來就是加權平均),只少了記帳前的
   個別買進日期與已實現損益,對記帳用途不重要;券商 App 的「庫存」頁本來就顯示股數與
   成本均價。實作成批次頁:
   - `lib/investment.ts::parseOpeningHoldingsText`:一行一檔「代號 股數 成本」,可夾
     名稱;有 tab 用 tab 切(Excel/Google 試算表複製),空白切得出 ≥3 欄用空白(逗號
     當千分位),否則當 CSV;第一個英數字欄位是代號,之後前兩個數字是股數、成本;
     解析不出的行(標題列)算 skipped。
   - `openingTradeFromCost`:平均成本模式 → price = 均價、fee 0;總成本模式 →
     price = 總成本 ÷ 股數(4 位小數),價金取整的零頭放 fee,存下來的成本剛好等於
     輸入。
   - `OpeningHoldingsDialog.tsx`:市場、持股日期、成本模式(平均/總成本)、貼上區、
     可編輯表格(代號 blur 時一次查報價帶名稱、已持有同代號時提示會加上去)。每列
     呼叫一次既有的 `createStockTrade(trade_type='opening')`,沒有新 API;中途失敗時
     已存的列從表格移除,避免重按又存一次。入口:投資頁帳戶卡「期初持股」按鈕、股票
     交易 dialog 選「期初持股」後的「一次新增多檔期初持股 →」。
   - 單筆期初持股:價格欄改叫「平均成本」、不再自動估手續費(成本均價通常已含)。
   - App 同一套(`opening_holdings_import.dart`、`OpeningHoldingsBatchPage`),測試
     用同一組字串/數字(`investmentFees.test.ts`「期初持股匯入」)。

刻意沒做:券商對帳單/交易明細 CSV 匯入(各家格式不同、還要處理配股/減資,之後要做
的話可以在 parser 前面加券商格式轉換);貼上時同代號多行不合併(各存一筆,持股計算
結果相同)。

## 11. 待辦(Phase 3)

- 股票分割、已實現損益報表、AI 查詢持股、管理後台切換付費資料來源。
