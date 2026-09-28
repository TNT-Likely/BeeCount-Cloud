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

export type ResolvedInvestmentSettings = Required<
  Omit<InvestmentSettings, 'market' | 'settlementAccountId'>
> & Pick<InvestmentSettings, 'market' | 'settlementAccountId'>

export function investmentDefaults(market: string | null | undefined): ResolvedInvestmentSettings {
  switch ((market || '').toUpperCase()) {
    case 'TW':
    case 'TWO':
      return {
        feeRate: 0.001425, feeDiscount: 1, feeMin: 20, sellTaxRate: 0.003,
        dividendFeeFixed: 10, dividendFeeRate: 0, dividendWithholdingRate: 0,
        nhiSupplementRate: 0.0211, nhiThreshold: 20000, reinvestDividends: false,
      }
    case 'US':
      return {
        feeRate: 0.0025, feeDiscount: 1, feeMin: 0, sellTaxRate: 0,
        dividendFeeFixed: 0, dividendFeeRate: 0, dividendWithholdingRate: 0.3,
        nhiSupplementRate: 0, nhiThreshold: 0, reinvestDividends: false,
      }
    default:
      return {
        feeRate: 0, feeDiscount: 1, feeMin: 0, sellTaxRate: 0,
        dividendFeeFixed: 0, dividendFeeRate: 0, dividendWithholdingRate: 0,
        nhiSupplementRate: 0, nhiThreshold: 0, reinvestDividends: false,
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
    dividendFeeFixed: s.dividendFeeFixed ?? d.dividendFeeFixed,
    dividendFeeRate: s.dividendFeeRate ?? d.dividendFeeRate,
    dividendWithholdingRate: s.dividendWithholdingRate ?? d.dividendWithholdingRate,
    nhiSupplementRate: s.nhiSupplementRate ?? d.nhiSupplementRate,
    nhiThreshold: s.nhiThreshold ?? d.nhiThreshold,
    reinvestDividends: s.reinvestDividends ?? false,
    settlementAccountId: s.settlementAccountId,
  }
}

/** TWD/JPY/KRW 沒有小數:手續費/稅無條件捨去到整數(台灣券商慣例),其它四捨五入到分。 */
export function currencyDecimals(currency: string | null | undefined): number {
  const upper = (currency || '').toUpperCase()
  return upper === 'TWD' || upper === 'JPY' || upper === 'KRW' ? 0 : 2
}

function roundFee(value: number, currency: string | null | undefined): number {
  const decimals = currencyDecimals(currency)
  if (decimals === 0) return Math.floor(value)
  const factor = 10 ** decimals
  return Math.round(value * factor) / factor
}

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

export function suggestSellTax(
  gross: number,
  settings: InvestmentSettings | null | undefined,
  market: string,
  currency: string,
): number {
  if (!(gross > 0)) return 0
  const r = resolveInvestmentSettings(settings, market)
  return roundFee(gross * r.sellTaxRate, currency)
}

/** 以證券幣別計的現金影響,同 server `snapshot_mutator.stock_trade_amount`。 */
export function stockTradeAmount(
  tradeType: string,
  shares: number,
  price: number,
  fee: number,
  tax: number,
): number {
  const gross = shares * price
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
