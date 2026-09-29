/**
 * 台股費用對帳(2026-09-28):價金/手續費/稅無條件捨去、證交稅依標的類型、預估變現淨值。
 * 跟 App investment_settings_test.dart「台股費用對帳」、server tests/test_trade_fees.py
 * 同一組數字(跟永豐對帳單核對過)。
 */
import { describe, expect, it } from 'vitest'

import {
  estimateSell,
  openingTotalCost,
  openingTradeFromCost,
  parseOpeningHoldingsText,
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

// 同一組字串/數字也在 App test/services/investment/opening_holdings_import_test.dart。
describe('期初持股匯入(App/Web 共用案例)', () => {
  it('Tab 分隔(Excel 複製)可帶千分位與名稱,標題列略過', () => {
    const r = parseOpeningHoldingsText('代號\t名稱\t股數\t平均成本\n0050\t元大台灣50\t1,000\t120.5\n2330\t台積電\t50\t580\n')
    expect(r.skipped).toBe(1)
    expect(r.lines).toEqual([
      { symbol: '0050', name: '元大台灣50', shares: 1000, cost: 120.5 },
      { symbol: '2330', name: '台積電', shares: 50, cost: 580 },
    ])
  })

  it('空白分隔,逗號當千分位;CSV;小寫代號轉大寫', () => {
    const r = parseOpeningHoldingsText('0056 2,000 35.2\n2330,台積電,50,580\nvoo 3.5 412.3\n')
    expect(r.skipped).toBe(0)
    expect(r.lines.map((l) => l.symbol)).toEqual(['0056', '2330', 'VOO'])
    expect(r.lines[0]).toMatchObject({ shares: 2000, cost: 35.2 })
    expect(r.lines[1].name).toBe('台積電')
    expect(r.lines[2]).toMatchObject({ shares: 3.5, name: null })
  })

  it('名稱在代號前面、帶單位/貨幣符號也可以', () => {
    const r = parseOpeningHoldingsText('元大高股息 0056 1000股 NT$35.2')
    expect(r.lines).toEqual([{ symbol: '0056', name: '元大高股息', shares: 1000, cost: 35.2 }])
  })

  it('缺成本或數字為 0 的行略過', () => {
    const r = parseOpeningHoldingsText('0050 1000\n2330 0 580\n\n   \n')
    expect(r.lines).toEqual([])
    expect(r.skipped).toBe(2)
  })

  it('平均成本:價格 = 均價、手續費 0', () => {
    expect(openingTradeFromCost(1000, 120.5, false, 'TWD')).toEqual({ price: 120.5, fee: 0 })
    expect(openingTotalCost(1000, 120.5, false, 'TWD')).toBe(120500)
  })

  it('總成本:存下來的成本剛好等於輸入', () => {
    expect(openingTradeFromCost(3, 100, true, 'TWD')).toEqual({ price: 33.3333, fee: 1 })
    expect(openingTotalCost(3, 100, true, 'TWD')).toBe(100)
    expect(openingTradeFromCost(1234, 56357, true, 'TWD').fee).toBe(0)
    expect(openingTotalCost(3.5, 1443.05, true, 'USD')).toBe(1443.05)
  })
})
