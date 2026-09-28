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

## 9. 待辦(Phase 3)

- 股票分割、已實現損益報表、AI 查詢持股、管理後台切換付費資料來源。
