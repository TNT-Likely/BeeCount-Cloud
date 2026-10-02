/**
 * 股票持股(2026-09-28,docs/STOCK_HOLDINGS_SD.md)API。
 *
 * - 市場資料:`/read/securities/search`、`/read/securities/quotes`(server 盤中
 *   快取超過 15 分鐘會自動補抓)。
 * - 持股:`/read/workspace/holdings`(server 由 stock_trade 即時算移動平均成本,
 *   並折算成主幣別)。
 * - 交易明細:`/read/ledgers/{id}/stock-trades` + `/write/ledgers/{id}/stock-trades`
 *   (buy/sell 會連帶建立/更新/刪除一筆轉帳交易;cash_dividend/reinvest 建
 *   income 交易,分類固定「股利」)。
 * - 股利(Phase 2):`/read/securities/pending-dividends` 待確認股利、
 *   `/write/securities/pending-dividends/{id}/confirm|dismiss|restore`、
 *   `/read/securities/dividend-events` 除權息事件。
 */
import { authedDelete, authedGet, authedPatch, authedPost } from './http'
import type { WriteCommitMeta } from './types'

export type SecuritySearchItem = {
  market: string
  symbol: string
  name: string
  currency: string
  kind: string
}

export type SecurityQuote = {
  market: string
  symbol: string
  name: string | null
  currency: string | null
  price: number | null
  prev_close: number | null
  change: number | null
  change_percent: number | null
  quote_time: string | null
  /** 'close' 收盤價 / 'intraday' 盤中延遲報價 / 'manual' */
  session: string | null
  source: string | null
  fetched_at: string | null
  stale: boolean
}

export type Holding = {
  account_id: string | null
  market: string
  symbol: string
  security_name: string | null
  currency: string | null
  shares: number
  total_cost: number
  avg_cost: number
  realized_pnl: number
  dividends: number
  trade_count: number
  first_trade_date: string | null
  last_trade_date: string | null
  quote: SecurityQuote | null
  market_value: number | null
  /** 現在全部賣掉的預估手續費/交易稅與淨額(server trade_fees.estimate_sell)。 */
  est_sell_fee?: number | null
  est_sell_tax?: number | null
  net_value?: number | null
  /** true = unrealized_pnl 用 net_value 算(帳戶設定 pnlAfterSellCosts,預設開)。 */
  pnl_after_sell_costs?: boolean
  unrealized_pnl: number | null
  unrealized_pnl_percent: number | null
}

export type AccountHoldings = {
  account_id: string
  account_name: string
  currency: string | null
  include_in_total: boolean
  holdings: Holding[]
  market_value_by_currency: Record<string, number>
  net_value_by_currency?: Record<string, number>
  valuation_by_currency?: Record<string, number>
  cost_by_currency: Record<string, number>
  realized_pnl_by_currency: Record<string, number>
}

export type HoldingsSummary = {
  base_currency: string | null
  accounts: AccountHoldings[]
  /** 折算成主幣別;缺匯率的幣別剔除並列在 missing_rates(不按 1.0 裸加)。 */
  total_market_value: number
  total_net_value?: number
  total_cost: number
  /** 有帳戶開了 pnlAfterSellCosts 時,是「預估變現淨值 − 成本」。 */
  total_unrealized_pnl: number
  pnl_after_sell_costs?: boolean
  missing_rates: string[]
  stale: boolean
}

export type StockTradeType =
  | 'buy'
  | 'sell'
  | 'opening'
  | 'stock_dividend'
  | 'split'
  | 'cash_dividend'
  | 'reinvest'

export type StockTrade = {
  id: string
  account_id: string | null
  market: string
  symbol: string
  security_name: string | null
  trade_type: StockTradeType
  shares: number
  price: number | null
  fee: number
  tax: number
  amount: number
  currency: string | null
  trade_date: string | null
  tx_id: string | null
  dividend_event_ref: string | null
  note: string | null
}

export type StockTradeCreatePayload = {
  account_id: string
  /** cash_dividend:shares=持有股數、price=每股股利、fee=股利手續費、tax=預扣稅+二代健保,
   *  入 settlement_account_id;reinvest:入投資理財帳戶本身;
   *  split:shares=「每 1 股變成幾股」的比例(1 拆 4 → 4;2 合 1 → 0.5),
   *  price/fee/tax 不需要、不需交割帳戶、不建轉帳交易。 */
  trade_type: StockTradeType
  market: string
  symbol: string
  security_name?: string | null
  shares: number
  price?: number | null
  fee?: number
  tax?: number
  currency?: string | null
  trade_date: string
  settlement_account_id?: string | null
  /** 交割帳戶幣別跟證券幣別不同時必填(已含手續費/稅)。 */
  settlement_amount?: number | null
  note?: string | null
}

export type StockTradeUpdatePayload = Partial<
  Pick<
    StockTradeCreatePayload,
    'shares' | 'price' | 'fee' | 'tax' | 'trade_date' | 'security_name' | 'settlement_account_id' | 'settlement_amount' | 'note'
  >
>

export async function searchSecurities(
  token: string,
  query: string,
  market?: string | null,
): Promise<SecuritySearchItem[]> {
  const params = new URLSearchParams({ q: query })
  if (market) params.set('market', market)
  return authedGet<SecuritySearchItem[]>(`/read/securities/search?${params.toString()}`, token)
}

export async function fetchSecurityQuotes(
  token: string,
  symbolKeys: string[],
  options?: { refresh?: boolean },
): Promise<SecurityQuote[]> {
  if (symbolKeys.length === 0) return []
  const params = new URLSearchParams({
    symbols: symbolKeys.join(','),
    refresh: options?.refresh === false ? 'false' : 'true',
  })
  return authedGet<SecurityQuote[]>(`/read/securities/quotes?${params.toString()}`, token)
}

export async function fetchWorkspaceHoldings(
  token: string,
  options?: { accountId?: string; refresh?: boolean },
): Promise<HoldingsSummary> {
  const params = new URLSearchParams()
  if (options?.accountId) params.set('account_id', options.accountId)
  if (options?.refresh === false) params.set('refresh', 'false')
  const qs = params.toString()
  return authedGet<HoldingsSummary>(`/read/workspace/holdings${qs ? `?${qs}` : ''}`, token)
}

export async function fetchStockTrades(
  token: string,
  ledgerId: string,
  filters?: { accountId?: string; market?: string; symbol?: string },
): Promise<StockTrade[]> {
  const params = new URLSearchParams()
  if (filters?.accountId) params.set('account_id', filters.accountId)
  if (filters?.market) params.set('market', filters.market)
  if (filters?.symbol) params.set('symbol', filters.symbol)
  const qs = params.toString()
  return authedGet<StockTrade[]>(
    `/read/ledgers/${encodeURIComponent(ledgerId)}/stock-trades${qs ? `?${qs}` : ''}`,
    token,
  )
}

export async function createStockTrade(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: StockTradeCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/stock-trades`,
    token,
    { base_change_id: baseChangeId, ...payload },
    idempotencyKey,
  )
}

export async function updateStockTrade(
  token: string,
  ledgerId: string,
  tradeId: string,
  baseChangeId: number,
  payload: StockTradeUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/stock-trades/${encodeURIComponent(tradeId)}`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

export async function deleteStockTrade(
  token: string,
  ledgerId: string,
  tradeId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/stock-trades/${encodeURIComponent(tradeId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}


export type PendingDividendStatus = 'pending' | 'confirmed' | 'dismissed'

export type PendingDividend = {
  id: number
  ledger_id: string | null
  account_id: string
  account_name: string | null
  account_currency: string | null
  market: string
  symbol: string
  security_name: string | null
  currency: string | null
  ex_date: string
  pay_date: string | null
  cash_per_share: number
  /** 每股配幾股(台股無償配股率)。 */
  stock_per_share: number
  /** 除息日前一天的持股。 */
  shares: number
  est_gross: number
  est_fee: number
  est_tax: number
  est_net: number
  est_stock_shares: number
  status: PendingDividendStatus
  reinvest_default: boolean
  settlement_account_id: string | null
  event_ref: string
  created_at: string | null
  /** 快取報價,再投入價格預填用。 */
  quote_price: number | null
}

export type PendingDividendConfirmPayload = {
  mode: 'cash' | 'reinvest'
  cash_per_share?: number | null
  fee?: number | null
  tax?: number | null
  settlement_account_id?: string | null
  /** 入帳帳戶幣別跟證券幣別不同時必填(入帳帳戶實際收到的金額)。 */
  settlement_amount?: number | null
  reinvest_shares?: number | null
  reinvest_price?: number | null
  reinvest_fee?: number | null
  /** 配股股數;0 = 不記。沒給就用估算值。 */
  stock_shares?: number | null
  trade_date?: string | null
  note?: string | null
}

export type DividendEvent = {
  market: string
  symbol: string
  ex_date: string
  pay_date: string | null
  cash_per_share: number
  stock_per_share: number
  currency: string | null
  source: string
}

export async function fetchPendingDividends(
  token: string,
  status: PendingDividendStatus | 'all' = 'pending',
): Promise<PendingDividend[]> {
  return authedGet<PendingDividend[]>(`/read/securities/pending-dividends?status=${status}`, token)
}

export async function confirmPendingDividend(
  token: string,
  pendingId: number,
  baseChangeId: number,
  payload: PendingDividendConfirmPayload,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/securities/pending-dividends/${pendingId}/confirm`,
    token,
    { base_change_id: baseChangeId, ...payload },
  )
}

export async function dismissPendingDividend(token: string, pendingId: number): Promise<{ id: number; status: string }> {
  return authedPost(`/write/securities/pending-dividends/${pendingId}/dismiss`, token, {})
}

export async function restorePendingDividend(token: string, pendingId: number): Promise<{ id: number; status: string }> {
  return authedPost(`/write/securities/pending-dividends/${pendingId}/restore`, token, {})
}

export async function fetchDividendEvents(token: string, market: string, symbol: string): Promise<DividendEvent[]> {
  const params = new URLSearchParams({ symbol: `${market}:${symbol}` })
  return authedGet<DividendEvent[]>(`/read/securities/dividend-events?${params.toString()}`, token)
}


// ---------------------------------------------------------------------------
// 已實現損益報表(Phase 3):`/read/workspace/realized-pnl`,各幣別分開、不跨幣別加總。
// ---------------------------------------------------------------------------

export type RealizedPnlEvent = {
  trade_id: string
  account_id: string | null
  market: string
  symbol: string
  security_name: string | null
  currency: string | null
  date: string | null
  shares: number
  proceeds: number
  cost_basis: number
  pnl: number
}

export type RealizedPnlSymbol = {
  market: string
  symbol: string
  security_name: string | null
  currency: string | null
  pnl: number
  proceeds: number
  cost_basis: number
  sell_count: number
  events: RealizedPnlEvent[]
}

export type RealizedPnlReport = {
  /** null = 全部年度。 */
  year: number | null
  /** 有賣出紀錄的年份,新到舊。 */
  years: number[]
  realized_pnl_by_currency: Record<string, number>
  dividends_by_currency: Record<string, number>
  /** server 已依損益絕對值由大到小排序。 */
  symbols: RealizedPnlSymbol[]
}

export async function fetchRealizedPnl(
  token: string,
  options?: { accountId?: string | null; year?: number | null; symbol?: string | null },
): Promise<RealizedPnlReport> {
  const params = new URLSearchParams()
  if (options?.accountId) params.set('account_id', options.accountId)
  if (options?.year) params.set('year', String(options.year))
  const sym = options?.symbol?.trim()
  if (sym) params.set('symbol', sym)
  const qs = params.toString()
  return authedGet<RealizedPnlReport>(`/read/workspace/realized-pnl${qs ? `?${qs}` : ''}`, token)
}

// ---------------------------------------------------------------------------
// 首頁「投資淨投入」(2026-10-03):`/read/workspace/investment-flow`。
// 買進/賣出是轉帳到投資帳戶,不是支出/收入;金額各證券幣別分開、不跨幣別加總。
// ---------------------------------------------------------------------------

export type InvestmentFlowCurrency = {
  currency: string
  /** 買進現金流出(含手續費)。 */
  buy_amount: number
  /** 賣出淨收入(已扣手續費+交易稅)。 */
  sell_amount: number
  /** buy_amount − sell_amount,可為負(賣多於買)。 */
  net_invested: number
  /** 手續費 / 交易稅合計(已含在 buy/sell_amount 內,不進收支統計)。 */
  fees: number
  taxes: number
  /** 現金股利 + 股利再投入(已是收入交易,算在首頁收入內)。 */
  dividends: number
  buy_count: number
  sell_count: number
}

export type InvestmentFlow = {
  scope: 'month' | 'year' | 'all'
  period: string | null
  by_currency: InvestmentFlowCurrency[]
}

export async function fetchInvestmentFlow(
  token: string,
  options: {
    scope: 'month' | 'year' | 'all'
    period?: string | null
    ledgerId?: string | null
    tzOffsetMinutes?: number
  },
): Promise<InvestmentFlow> {
  const params = new URLSearchParams({ scope: options.scope })
  if (options.period) params.set('period', options.period)
  if (options.ledgerId) params.set('ledger_id', options.ledgerId)
  if (options.tzOffsetMinutes !== undefined) params.set('tz_offset_minutes', String(options.tzOffsetMinutes))
  return authedGet<InvestmentFlow>(`/read/workspace/investment-flow?${params.toString()}`, token)
}
