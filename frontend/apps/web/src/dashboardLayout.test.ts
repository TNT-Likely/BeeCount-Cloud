import { describe, expect, it } from 'vitest'

import {
  buildPayload,
  defaultLayout,
  isDefaultLayout,
  mergeLayout,
  moveCard,
  setCardVisible,
  showCardAtEnd,
} from './lib/dashboardLayout'
import { aggregateHoldings } from './components/dashboard/stock/StockCards'
import { describeNetInvested, dividendsOf, netInvestedRows } from './components/dashboard/stock/flowUtil'

const defs = [
  { id: 'a', defaultVisible: true },
  { id: 'b', defaultVisible: true },
  { id: 'c', defaultVisible: false },
  { id: 'd', defaultVisible: true },
]
const ids = (l: { id: string }[]) => l.map((c) => c.id).join(',')

describe('mergeLayout', () => {
  it('沒有儲存版面 → 預設', () => {
    expect(mergeLayout(defs, null).cards).toEqual(defaultLayout(defs))
    expect(mergeLayout(defs, []).cards).toEqual(defaultLayout(defs))
  })

  it('沿用儲存的順序與可見性', () => {
    const m = mergeLayout(defs, [
      { id: 'd', visible: true },
      { id: 'a', visible: false },
      { id: 'b', visible: true },
      { id: 'c', visible: true },
    ])
    expect(ids(m.cards)).toBe('d,a,b,c')
    expect(m.cards.map((c) => c.visible)).toEqual([true, false, true, true])
  })

  it('未知 id 丟棄但留在 unknown;重複 id 只留第一筆', () => {
    const m = mergeLayout(defs, [
      { id: 'b', visible: true },
      { id: 'zzz', visible: false },
      { id: 'b', visible: false },
      { id: 'a', visible: true },
      { id: 'c', visible: false },
      { id: 'd', visible: true },
    ])
    expect(ids(m.cards)).toBe('b,a,c,d')
    expect(m.cards[0].visible).toBe(true)
    expect(m.unknown).toEqual([{ id: 'zzz', visible: false }])
    // 存檔時未知卡片原樣帶回
    expect(ids(buildPayload(m).cards)).toBe('b,a,c,d,zzz')
  })

  it('新增卡片依預設位置插入並取預設可見性', () => {
    // 儲存時只有 a、d(舊版沒有 b、c)
    const m = mergeLayout(defs, [
      { id: 'd', visible: true },
      { id: 'a', visible: true },
    ])
    // b 接在 a 後面;c 接在 b 後面(預設順序上前一張)
    expect(ids(m.cards)).toBe('d,a,b,c')
    expect(m.cards.find((c) => c.id === 'c')?.visible).toBe(false)
  })

  it('新增卡片在預設最前面且前面沒有已存在卡片 → 放在第一張後繼之前', () => {
    const m = mergeLayout(defs, [
      { id: 'c', visible: true },
      { id: 'b', visible: true },
    ])
    expect(ids(m.cards)).toBe('c,d,a,b')
    // a 預設在 b 之前,但清單裡 b 在最後 → a 放到 b 之前
    expect(m.cards.findIndex((c) => c.id === 'a')).toBeLessThan(m.cards.findIndex((c) => c.id === 'b'))
    expect(m.cards).toHaveLength(4)
  })

  it('壞資料容錯', () => {
    const m = mergeLayout(defs, [null, { id: 5 }, { id: 'a' }] as never)
    expect(ids(m.cards)).toContain('a')
    expect(m.cards).toHaveLength(4)
  })
})

describe('操作', () => {
  it('moveCard / setCardVisible / showCardAtEnd', () => {
    const base = defaultLayout(defs)
    expect(ids(moveCard(base, 'd', 'a'))).toBe('d,a,b,c')
    expect(moveCard(base, 'x', 'a')).toBe(base)
    expect(setCardVisible(base, 'b', false)[1].visible).toBe(false)
    // c 隱藏中 → 顯示後排在最後一張可見卡(d)之後
    expect(ids(showCardAtEnd(base, 'c'))).toBe('a,b,d,c')
    expect(showCardAtEnd(base, 'c').find((c) => c.id === 'c')?.visible).toBe(true)
  })

  it('isDefaultLayout', () => {
    expect(isDefaultLayout(defs, defaultLayout(defs))).toBe(true)
    expect(isDefaultLayout(defs, moveCard(defaultLayout(defs), 'd', 'a'))).toBe(false)
    expect(isDefaultLayout(defs, setCardVisible(defaultLayout(defs), 'a', false))).toBe(false)
  })
})


describe('投資淨投入小工具', () => {
  const flow = {
    scope: 'month' as const,
    period: '2026-09',
    by_currency: [
      { currency: 'TWD', buy_amount: 10, sell_amount: 4, net_invested: 6, fees: 0, taxes: 0, dividends: 2000, buy_count: 1, sell_count: 1 },
      { currency: 'USD', buy_amount: 5, sell_amount: 5, net_invested: 0.001, fees: 0, taxes: 0, dividends: 0, buy_count: 1, sell_count: 1 },
    ],
  }
  it('netInvestedRows 過濾掉 0', () => {
    expect(netInvestedRows(flow)).toEqual([{ currency: 'TWD', net: 6 }])
    expect(netInvestedRows(null)).toEqual([])
  })
  it('describeNetInvested 多幣別合成一句、轉出/轉回各一句', () => {
    const fmt = (v: number, c: string) => `${c}${v}`
    const texts = { out: (a: string) => `投入 ${a}`, in: (a: string) => `轉回 ${a}` }
    expect(describeNetInvested([{ currency: 'TWD', net: 6 }, { currency: 'USD', net: 2 }], fmt, texts)).toEqual(['投入 TWD6 + USD2'])
    expect(describeNetInvested([{ currency: 'TWD', net: 6 }, { currency: 'USD', net: -2 }], fmt, texts)).toEqual(['投入 TWD6', '轉回 USD2'])
    expect(describeNetInvested([], fmt, texts)).toEqual([])
  })
  it('dividendsOf 依幣別', () => {
    expect(dividendsOf(flow, 'twd')).toBe(2000)
    expect(dividendsOf(flow, 'USD')).toBe(0)
    expect(dividendsOf(null, 'TWD')).toBe(0)
  })
  it('aggregateHoldings 跨帳戶合併;任一帳戶沒報價就整檔無市值', () => {
    const mk = (acc: string, shares: number, mv: number | null) =>
      ({ account_id: acc, market: 'TW', symbol: '2330', security_name: '台積電', currency: 'TWD', shares, total_cost: shares * 10, market_value: mv, unrealized_pnl: mv === null ? null : mv - shares * 10 }) as never
    const [a] = aggregateHoldings([mk('a', 100, 1500), mk('b', 50, 800)])
    expect(a.shares).toBe(150)
    expect(a.cost).toBe(1500)
    expect(a.marketValue).toBe(2300)
    expect(a.pnl).toBe(800)
    const [b] = aggregateHoldings([mk('a', 100, 1500), mk('b', 50, null)])
    expect(b.marketValue).toBeNull()
    expect(b.pnl).toBeNull()
  })
})
