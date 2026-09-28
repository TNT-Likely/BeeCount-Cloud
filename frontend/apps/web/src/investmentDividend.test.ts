import { describe, expect, it } from 'vitest'

import { estimateDividend, stockTradeAmount } from '@beecount/web-features'

// 跟 server tests/test_stock_dividends.py 的估算測試用同一組數字(三端規則一致)。
describe('estimateDividend', () => {
  it('TW under NHI threshold only deducts remittance fee', () => {
    expect(estimateDividend({ market: 'TW', currency: 'TWD', shares: 1000, cashPerShare: 7, settings: {} })).toEqual({
      gross: 7000, fee: 10, tax: 0, net: 6990, stockShares: 0,
    })
  })

  it('TW over NHI threshold floors the supplement', () => {
    const est = estimateDividend({ market: 'TW', currency: 'TWD', shares: 5000, cashPerShare: 5, settings: {} })
    expect(est.tax).toBe(527)
    expect(est.net).toBe(25000 - 10 - 527)
  })

  it('user settings override market defaults', () => {
    const est = estimateDividend({
      market: 'TW', currency: 'TWD', shares: 5000, cashPerShare: 5,
      settings: { dividendFeeFixed: 0, nhiSupplementRate: 0 },
    })
    expect([est.fee, est.tax, est.net]).toEqual([0, 0, 25000])
  })

  it('US withholding and fee rate', () => {
    const est = estimateDividend({
      market: 'US', currency: 'USD', shares: 10, cashPerShare: 0.27, settings: { dividendFeeRate: 0.1 },
    })
    expect(est.gross).toBeCloseTo(2.7)
    expect(est.tax).toBeCloseTo(0.81)
    expect(est.fee).toBeCloseTo(0.27)
    expect(est.net).toBeCloseTo(1.62)
  })

  it('stock dividend rounds the truncated ratio before flooring', () => {
    expect(
      estimateDividend({ market: 'TW', currency: 'TWD', shares: 1000, cashPerShare: 0, stockPerShare: 0.04999999, settings: {} })
        .stockShares,
    ).toBe(50)
  })
})

describe('stockTradeAmount dividends', () => {
  it('cash dividend is net of fee and tax; reinvest is cost', () => {
    expect(stockTradeAmount('cash_dividend', 1000, 7, 10, 0)).toBe(6990)
    expect(stockTradeAmount('reinvest', 11, 630, 1, 0)).toBe(6931)
  })
})
