/**
 * 台股費用對帳(2026-09-28):價金/手續費/稅無條件捨去、證交稅依標的類型、預估變現淨值。
 * 跟 App investment_settings_test.dart「台股費用對帳」、server tests/test_trade_fees.py
 * 同一組數字(跟永豐對帳單核對過)。
 */
import { describe, expect, it } from 'vitest'

import {
  estimateSell,
  securityKind,
  sellTaxRateFor,
  stockDcaOrder,
  stockDcaWholeShares,
  stockGross,
  stockTradeAmount,
  suggestSellTax,
} from '@beecount/web-features'

describe('台股費用對帳(三端共用案例)', () => {
  it('標的類型:00 開頭 ETF、結尾 B 債券 ETF、其它普通股', () => {
    expect(securityKind('TW', '0050')).toBe('etf')
    expect(securityKind('TW', '00878')).toBe('etf')
    expect(securityKind('TW', '00631L')).toBe('etf')
    expect(securityKind('TWO', '00679B')).toBe('bond_etf')
    expect(securityKind('TW', '2330')).toBe('stock')
    expect(securityKind('US', '0050')).toBe('stock')
  })

  it('0050 買進 50 股 @97.45、手續費 6 → 價金 4,872,總成本 4,878', () => {
    expect(stockGross(50, 97.45, 'TWD')).toBe(4872)
    expect(stockTradeAmount('buy', 50, 97.45, 6, 0, 'TWD')).toBe(4878)
  })

  it('0050 賣出 50 股 @112.40:ETF 稅率 0.1%,交易稅 5', () => {
    expect(sellTaxRateFor({}, 'TW', '0050')).toBe(0.001)
    expect(suggestSellTax(5620, {}, 'TW', 'TWD', '0050')).toBe(5)
    expect(suggestSellTax(5620, {}, 'TW', 'TWD', '2330')).toBe(16)
    expect(suggestSellTax(5620, {}, 'TW', 'TWD', '00679B')).toBe(0)
  })

  it('只改普通股稅率時 ETF 仍用 ETF 預設', () => {
    expect(sellTaxRateFor({ sellTaxRate: 0.003 }, 'TW', '0050')).toBe(0.001)
    expect(sellTaxRateFor({ etfSellTaxRate: 0.0005 }, 'TW', '0050')).toBe(0.0005)
  })

  it('捨去前先清浮點殘渣', () => {
    expect(stockGross(1000, 600.1, 'TWD')).toBe(600100)
    expect(stockGross(3, 10.005, 'USD')).toBe(30.02)
  })

  it('預估變現淨值', () => {
    const e = estimateSell({
      shares: 50, price: 112.4, market: 'TW', symbol: '0050', currency: 'TWD',
      settings: { feeDiscount: 0.6, feeMin: 1 },
    })
    expect(e).toEqual({ gross: 5620, fee: 4, tax: 5, net: 5611 })
  })
})

describe('定期定額下單試算(三端共用案例,2026-09-30)', () => {
  const fixedFee1 = { feeRate: 0, feeDiscount: 1, feeMin: 1 }

  it('台股整數股:券商範例 3,000/月、手續費 1 元', () => {
    for (const [price, shares, total] of [
      [150, 19, 2851],
      [100, 29, 2901],
      [200, 14, 2801],
    ] as const) {
      expect(stockDcaOrder(3000, price, fixedFee1, 'TW', 'TWD')).toEqual({
        shares,
        gross: shares * price,
        fee: 1,
        total,
      })
    }
  })

  it('台股預設費率:3,000 @97.45 → 30 股、價金 2,923 + 手續費 20', () => {
    expect(stockDcaOrder(3000, 97.45, null, 'TW', 'TWD')).toEqual({ shares: 30, gross: 2923, fee: 20, total: 2943 })
  })

  it('實際手續費較低時用剩下的錢多買 1 股;買不起 1 股回 null', () => {
    expect(stockDcaOrder(1000, 10, null, 'TWO', 'TWD')?.shares).toBe(98)
    expect(stockDcaOrder(1000, 10, { feeRate: 0.1, feeDiscount: 1, feeMin: 0 }, 'TW', 'TWD')).toEqual({
      shares: 90,
      gross: 900,
      fee: 90,
      total: 990,
    })
    expect(stockDcaOrder(100, 95, null, 'TW', 'TWD')).toBeNull()
  })

  it('美股碎股:金額全部買進、手續費另計', () => {
    const order = stockDcaOrder(100, 450, null, 'US', 'USD')!
    expect(order.shares).toBeCloseTo(100 / 450, 12)
    expect(order.gross).toBe(100)
    expect(order.fee).toBe(0.25)
    expect(stockDcaWholeShares('US')).toBe(false)
    expect(stockDcaWholeShares('two')).toBe(true)
  })
})
