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
