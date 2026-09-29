/**
 * 股票持股(2026-09-28,docs/STOCK_HOLDINGS_SD.md)前端共用工具。
 *
 * 市場清單、各市場預設費率、手續費/交易稅試算規則都跟 App
 * `lib/services/investment/markets.dart` + `lib/models/investment_settings.dart`
 * 同一套——Web 跟 App 對同一筆交易試算出不同的手續費會讓使用者困惑,改一邊
 * 要改另一邊。試算結果只是預填,使用者每筆都能改。
 */
import type { InvestmentSettings } from '@beecount/api-client'

import { currencySymbol } from './currencies'

export type StockMarketCode = 'TW' | 'TWO' | 'US' | 'HK' | 'JP' | 'SS' | 'SZ' | 'KS' | 'KQ' | 'LSE'

export const STOCK_MARKETS: { code: StockMarketCode; currency: string }[] = [
  { code: 'TW', currency: 'TWD' },
  { code: 'TWO', currency: 'TWD' },
  { code: 'US', currency: 'USD' },
  { code: 'HK', currency: 'HKD' },
  { code: 'JP', currency: 'JPY' },
  { code: 'SS', currency: 'CNY' },
  { code: 'SZ', currency: 'CNY' },
  { code: 'KS', currency: 'KRW' },
  { code: 'KQ', currency: 'KRW' },
  { code: 'LSE', currency: 'GBP' },
]

export function marketCurrency(code: string): string | undefined {
  return STOCK_MARKETS.find((m) => m.code === code.toUpperCase())?.currency
}

export function defaultMarketForCurrency(currency: string | null | undefined): StockMarketCode {
  const upper = (currency || '').toUpperCase()
  return STOCK_MARKETS.find((m) => m.currency === upper)?.code ?? 'TW'
}

/**
 * 標的類型,決定台股賣出證交稅率(普通股 0.3%、ETF 0.1%、債券 ETF 免徵)。只看
 * 代號,同 App `markets.dart::securityKindOf`、server `trade_fees.security_kind`:
 * 台股代號 `00` 開頭是 ETF,其中結尾 `B` 的是債券 ETF。其它市場一律 stock。
 */
export type SecurityKind = 'stock' | 'etf' | 'bond_etf'

export function securityKind(market: string | null | undefined, symbol: string | null | undefined): SecurityKind {
  const m = (market || '').toUpperCase()
  if (m !== 'TW' && m !== 'TWO') return 'stock'
  const s = (symbol || '').trim().toUpperCase()
  if (!/^00\d{2,4}[A-Z]?$/.test(s)) return 'stock'
  return s.endsWith('B') ? 'bond_etf' : 'etf'
}

export type ResolvedInvestmentSettings = Required<
  Omit<InvestmentSettings, 'market' | 'settlementAccountId' | 'etfSellTaxRate' | 'bondEtfSellTaxRate'>
> & Pick<InvestmentSettings, 'market' | 'settlementAccountId' | 'etfSellTaxRate' | 'bondEtfSellTaxRate'>

export function investmentDefaults(market: string | null | undefined): ResolvedInvestmentSettings {
  switch ((market || '').toUpperCase()) {
    case 'TW':
    case 'TWO':
      return {
        feeRate: 0.001425, feeDiscount: 1, feeMin: 20, sellTaxRate: 0.003,
        etfSellTaxRate: 0.001, bondEtfSellTaxRate: 0,
        dividendFeeFixed: 10, dividendFeeRate: 0, dividendWithholdingRate: 0,
        nhiSupplementRate: 0.0211, nhiThreshold: 20000, reinvestDividends: false, pnlAfterSellCosts: true,
      }
    case 'US':
      return {
        feeRate: 0.0025, feeDiscount: 1, feeMin: 0, sellTaxRate: 0,
        dividendFeeFixed: 0, dividendFeeRate: 0, dividendWithholdingRate: 0.3,
        nhiSupplementRate: 0, nhiThreshold: 0, reinvestDividends: false, pnlAfterSellCosts: true,
      }
    default:
      return {
        feeRate: 0, feeDiscount: 1, feeMin: 0, sellTaxRate: 0,
        dividendFeeFixed: 0, dividendFeeRate: 0, dividendWithholdingRate: 0,
        nhiSupplementRate: 0, nhiThreshold: 0, reinvestDividends: false, pnlAfterSellCosts: true,
      }
  }
}

export function resolveInvestmentSettings(
  settings: InvestmentSettings | null | undefined,
  market: string | null | undefined,
): ResolvedInvestmentSettings {
  const s = settings || {}
  const d = investmentDefaults(market ?? s.market)
  return {
    market: s.market ?? market ?? undefined,
    feeRate: s.feeRate ?? d.feeRate,
    feeDiscount: s.feeDiscount ?? d.feeDiscount,
    feeMin: s.feeMin ?? d.feeMin,
    sellTaxRate: s.sellTaxRate ?? d.sellTaxRate,
    etfSellTaxRate: s.etfSellTaxRate ?? d.etfSellTaxRate,
    bondEtfSellTaxRate: s.bondEtfSellTaxRate ?? d.bondEtfSellTaxRate,
    pnlAfterSellCosts: s.pnlAfterSellCosts ?? true,
    dividendFeeFixed: s.dividendFeeFixed ?? d.dividendFeeFixed,
    dividendFeeRate: s.dividendFeeRate ?? d.dividendFeeRate,
    dividendWithholdingRate: s.dividendWithholdingRate ?? d.dividendWithholdingRate,
    nhiSupplementRate: s.nhiSupplementRate ?? d.nhiSupplementRate,
    nhiThreshold: s.nhiThreshold ?? d.nhiThreshold,
    reinvestDividends: s.reinvestDividends ?? false,
    settlementAccountId: s.settlementAccountId,
  }
}

/** TWD/JPY/KRW 沒有小數:成交價金/手續費/稅無條件捨去到整數(證交所與台灣券商慣例),其它四捨五入到分。 */
export function currencyDecimals(currency: string | null | undefined): number {
  const upper = (currency || '').toUpperCase()
  return upper === 'TWD' || upper === 'JPY' || upper === 'KRW' ? 0 : 2
}

/**
 * 依幣別取整。捨去前先四捨五入到小數 6 位清掉浮點殘渣(1000 × 600.1 =
 * 600099.99999… 直接捨去會少 1 元),同 App `InvestmentSettings.roundMoney`、
 * server `trade_fees.round_money`。
 */
export function roundMoney(value: number, currency: string | null | undefined): number {
  const cleaned = Number(value.toFixed(6))
  const decimals = currencyDecimals(currency)
  if (decimals === 0) return Math.floor(cleaned)
  const factor = 10 ** decimals
  return Math.round(Number((cleaned * factor).toFixed(6))) / factor
}

/** 成交價金 = 股數 × 價格,依幣別取整(台幣 50 × 97.45 = 4,872.5 → 4,872)。 */
export function stockGross(shares: number, price: number, currency: string | null | undefined): number {
  return roundMoney(shares * price, currency)
}

const roundFee = roundMoney

export function suggestFee(
  gross: number,
  settings: InvestmentSettings | null | undefined,
  market: string,
  currency: string,
): number {
  if (!(gross > 0)) return 0
  const r = resolveInvestmentSettings(settings, market)
  return Math.max(roundFee(gross * r.feeRate * r.feeDiscount, currency), r.feeMin)
}

/** 這檔標的的賣出交易稅率:台股依 [securityKind] 分普通股 / ETF / 債券 ETF。 */
export function sellTaxRateFor(
  settings: InvestmentSettings | null | undefined,
  market: string,
  symbol: string | null | undefined,
): number {
  const r = resolveInvestmentSettings(settings, market)
  switch (securityKind(market, symbol)) {
    case 'etf':
      return r.etfSellTaxRate ?? r.sellTaxRate
    case 'bond_etf':
      return r.bondEtfSellTaxRate ?? 0
    default:
      return r.sellTaxRate
  }
}

export function suggestSellTax(
  gross: number,
  settings: InvestmentSettings | null | undefined,
  market: string,
  currency: string,
  symbol?: string | null,
): number {
  if (!(gross > 0)) return 0
  return roundFee(gross * sellTaxRateFor(settings, market, symbol), currency)
}

export type SellEstimate = { gross: number; fee: number; tax: number; net: number }

/** 「現在全部賣掉」的預估手續費/交易稅/淨額(同 server `trade_fees.estimate_sell`)。 */
export function estimateSell(params: {
  shares: number
  price: number
  market: string
  symbol: string
  currency: string
  settings: InvestmentSettings | null | undefined
}): SellEstimate {
  const gross = stockGross(params.shares, params.price, params.currency)
  if (!(gross > 0)) return { gross: 0, fee: 0, tax: 0, net: 0 }
  const fee = suggestFee(gross, params.settings, params.market, params.currency)
  const tax = suggestSellTax(gross, params.settings, params.market, params.currency, params.symbol)
  return { gross, fee, tax, net: Math.max(gross - fee - tax, 0) }
}

/**
 * 以證券幣別計的現金影響,同 server `snapshot_mutator.stock_trade_amount`。
 * 成交價金依幣別取整(台幣無條件捨去):0050 買 50 股 @97.45、手續費 6 → 4,878。
 */
export function stockTradeAmount(
  tradeType: string,
  shares: number,
  price: number,
  fee: number,
  tax: number,
  currency?: string | null,
): number {
  const gross = stockGross(shares, price, currency)
  if (tradeType === 'buy' || tradeType === 'opening' || tradeType === 'reinvest') return gross + fee
  if (tradeType === 'sell' || tradeType === 'cash_dividend') return gross - fee - tax
  return 0
}

export type DividendEstimate = {
  gross: number
  fee: number
  /** 預扣稅 + 二代健保。 */
  tax: number
  net: number
  stockShares: number
}

/**
 * 股利實收估算,同 server `services/securities/dividends.estimate_dividend`、App
 * `lib/services/investment/dividend_estimate.dart`——改一邊要改另外兩邊。
 * 二代健保:單筆股利總額 ≥ 門檻才扣;手續費 = 固定 + 總額×比率。
 */
export function estimateDividend(params: {
  market: string
  currency: string | null | undefined
  shares: number
  cashPerShare: number
  stockPerShare?: number
  settings: InvestmentSettings | null | undefined
}): DividendEstimate {
  const { market, currency, settings } = params
  const r = resolveInvestmentSettings(settings, market)
  const round = (v: number) => (currencyDecimals(currency) === 0 ? Math.floor(v + 1e-9) : Math.round(v * 100) / 100)
  const shares = Math.max(params.shares, 0)
  const gross = round(shares * Math.max(params.cashPerShare, 0))
  let fee = 0
  let tax = 0
  if (gross > 0) {
    const withholding = round(gross * r.dividendWithholdingRate)
    const nhi = r.nhiSupplementRate > 0 && gross >= r.nhiThreshold ? round(gross * r.nhiSupplementRate) : 0
    tax = withholding + nhi
    fee = Math.min(round(r.dividendFeeFixed + gross * r.dividendFeeRate), Math.max(gross - tax, 0))
  }
  const net = round(Math.max(gross - fee - tax, 0))
  const rawStock = shares * Math.max(params.stockPerShare ?? 0, 0)
  const upper = market.toUpperCase()
  const stockShares = upper === 'TW' || upper === 'TWO'
    ? Math.floor(Math.round(rawStock * 1000) / 1000)
    : Math.round(rawStock * 1e6) / 1e6
  return { gross, fee, tax, net, stockShares }
}

function groupDigits(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

export function formatShares(shares: number): string {
  if (Math.abs(shares - Math.round(shares)) < 1e-9) return groupDigits(String(Math.round(shares)))
  const [int, frac] = shares.toFixed(4).replace(/0+$/, '').split('.')
  return frac ? `${groupDigits(int)}.${frac}` : groupDigits(int)
}

export function formatPrice(price: number): string {
  const [int, rawFrac = ''] = price.toFixed(4).split('.')
  const frac = rawFrac.replace(/0+$/, '').padEnd(2, '0')
  return `${groupDigits(int)}.${frac}`
}

export function formatStockMoney(
  value: number,
  currency: string | null | undefined,
  options?: { signed?: boolean },
): string {
  const code = (currency || '').toUpperCase()
  const decimals = currencyDecimals(code)
  const [int, frac] = Math.abs(value).toFixed(decimals).split('.')
  const body = frac ? `${groupDigits(int)}.${frac}` : groupDigits(int)
  const sign = value < 0 ? '-' : options?.signed && value > 0 ? '+' : ''
  return `${sign}${code ? currencySymbol(code) : ''}${body}`
}

export function formatPercent(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

/** 比率 ⇄ 畫面百分比(0.001425 ⇄ "0.1425"),避免浮點殘渣。 */
export function rateToPercentText(rate: number | undefined | null): string {
  if (rate === undefined || rate === null) return ''
  return String(Number((rate * 100).toFixed(6)))
}

export function percentTextToRate(text: string): number | undefined {
  const trimmed = text.trim()
  if (!trimmed) return undefined
  const n = Number(trimmed)
  return Number.isFinite(n) ? Number((n / 100).toFixed(8)) : undefined
}

/**
 * 股票定期定額「只買整數股」的市場(2026-09-30)。台股要進證交所撮合,最小
 * 單位 1 股:每期金額(含手續費)能買幾個整股就買幾股,零頭不扣款;美股等
 * 市場券商允許碎股,維持「投入金額 ÷ 股價」、手續費另計。同 server
 * `trade_fees.STOCK_DCA_WHOLE_SHARE_MARKETS`、App `kStockDcaWholeShareMarkets`。
 */
export const STOCK_DCA_WHOLE_SHARE_MARKETS: readonly string[] = ['TW', 'TWO']

export function stockDcaWholeShares(market: string | null | undefined): boolean {
  return STOCK_DCA_WHOLE_SHARE_MARKETS.includes((market || '').toUpperCase())
}

export type StockDcaOrder = {
  shares: number
  /** 成交價金(= 綁定轉帳金額) */
  gross: number
  fee: number
  /** 交割帳戶實際扣款 = 成交價金 + 手續費 */
  total: number
}

/**
 * 定期定額一期的下單試算;買不到任何股數回 null。算法同 server
 * `trade_fees.stock_dca_order`/App `stockDcaOrder`,改一邊要改另外兩邊。
 */
export function stockDcaOrder(
  amount: number,
  price: number,
  settings: InvestmentSettings | null | undefined,
  market: string,
  currency: string,
): StockDcaOrder | null {
  if (!(price > 0)) return null
  const budget = roundMoney(amount, currency)
  if (!(budget > 0)) return null
  const feeOf = (gross: number) => suggestFee(gross, settings, market, currency)
  if (!stockDcaWholeShares(market)) {
    const fee = feeOf(budget)
    return { shares: budget / price, gross: budget, fee, total: budget + fee }
  }
  const fits = (n: number) => {
    const gross = stockGross(n, price, currency)
    return gross + feeOf(gross) <= budget + 1e-9
  }
  let n = Math.max(Math.floor(Number(((budget - feeOf(budget)) / price).toFixed(9))), 0)
  while (fits(n + 1)) n += 1
  while (n > 0 && !fits(n)) n -= 1
  if (n <= 0) return null
  const gross = stockGross(n, price, currency)
  const fee = feeOf(gross)
  return { shares: n, gross, fee, total: gross + fee }
}

// ---------------------------------------------------------------------------
// 批次期初持股(2026-09-30,STOCK_HOLDINGS_SD §10.3)。開始記帳前就持有的
// 股票不用逐筆補記過去的買進:照券商「庫存」頁每一檔填股數 + 平均成本(或
// 總成本)一筆期初持股。對齊 App `opening_holdings_import.dart`,兩邊測試
// 用同一組字串/數字。
// ---------------------------------------------------------------------------

export type OpeningHoldingLine = { symbol: string; name: string | null; shares: number; cost: number }

const OPENING_SYMBOL_RE = /^[A-Za-z0-9][A-Za-z0-9.-]*$/

function parseOpeningNumber(token: string): number | null {
  const cleaned = token
    .replace(/[,，\s]/g, '')
    .replace(/^(NT\$|US\$|HK\$|\$|¥|￥)/, '')
    .replace(/(股|元|TWD|USD)$/i, '')
  if (!/^\d+(\.\d+)?$/.test(cleaned)) return null
  return Number(cleaned)
}

function splitOpeningLine(line: string): string[] {
  // Excel/Google 試算表複製出來是 tab 分隔;空白切得出 3 欄以上用空白(逗號
  // 當千分位/欄尾);否則當 CSV。
  if (line.includes('\t')) return line.split('\t')
  const bySpace = line
    .split(/\s+/)
    .map((t) => t.replace(/^[,，]+|[,，]+$/g, ''))
    .filter(Boolean)
  if (bySpace.length >= 3) return bySpace
  return line.split(/[,，]/)
}

/** 一行一檔「代號 股數 成本」,可夾名稱;解析不出來的行算進 `skipped`。 */
export function parseOpeningHoldingsText(text: string): { lines: OpeningHoldingLine[]; skipped: number } {
  const lines: OpeningHoldingLine[] = []
  let skipped = 0
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const tokens = splitOpeningLine(line)
      .map((t) => t.trim().replace(/^"|"$/g, '').trim())
      .filter(Boolean)
    let symbol: string | null = null
    const names: string[] = []
    const numbers: number[] = []
    for (const tok of tokens) {
      if (symbol === null) {
        if (OPENING_SYMBOL_RE.test(tok)) symbol = tok.toUpperCase()
        else if (parseOpeningNumber(tok) === null) names.push(tok)
        continue
      }
      const n = parseOpeningNumber(tok)
      if (n !== null) numbers.push(n)
      else names.push(tok)
    }
    if (symbol === null || numbers.length < 2 || !(numbers[0] > 0) || !(numbers[1] > 0)) {
      skipped++
      continue
    }
    lines.push({ symbol, name: names.length ? names.join(' ') : null, shares: numbers[0], cost: numbers[1] })
  }
  return { lines, skipped }
}

/**
 * 期初持股要存成的價格/手續費。平均成本模式:價格 = 均價、手續費 0(券商
 * 成本均價通常已含手續費)。總成本模式:價格 = 總成本 ÷ 股數(4 位小數),
 * 價金取整的零頭放手續費,存下來的成本剛好等於輸入。
 */
export function openingTradeFromCost(
  shares: number,
  cost: number,
  costIsTotal: boolean,
  currency: string | null | undefined,
): { price: number; fee: number } {
  if (!costIsTotal || !(shares > 0)) return { price: cost, fee: 0 }
  const price = Number((cost / shares).toFixed(4))
  const gross = stockGross(shares, price, currency)
  const fee = roundMoney(cost - gross, currency)
  return { price, fee: fee > 0 ? fee : 0 }
}

export function openingTotalCost(
  shares: number,
  cost: number,
  costIsTotal: boolean,
  currency: string | null | undefined,
): number {
  const tr = openingTradeFromCost(shares, cost, costIsTotal, currency)
  return stockGross(shares, tr.price, currency) + tr.fee
}
