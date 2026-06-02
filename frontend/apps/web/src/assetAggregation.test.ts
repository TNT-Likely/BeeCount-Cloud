import type { ReadAccount } from '@beecount/api-client'
import {
  accountBalance,
  computeCurrencySummary,
  LIABILITY_TYPES,
  splitByCurrency
} from '@beecount/web-features'
import { describe, expect, it } from 'vitest'

/**
 * 资产页多币种聚合契约 —— 锁住"绝不跨币种相加"这条铁律。
 * 历史上这页裸加 balance 把不同币种当同币种加错了($1000 当 ¥1000)。
 *
 * 这些函数只读 account_type / currency / balance / initial_balance,其余 ReadAccount
 * 字段不参与聚合,所以用 partial 造数据再 cast,免得每条都填全。
 */
function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
  return p as ReadAccount
}

describe('asset aggregation — 绝不跨币种相加', () => {
  it('splitByCurrency 按归一化币种码分组(缺省 CNY、大小写归一)', () => {
    const map = splitByCurrency([
      acc({ currency: 'CNY', balance: 100 }),
      acc({ currency: 'usd', balance: 5 }),
      acc({ currency: 'USD', balance: 7 }),
      acc({ currency: null, balance: 1 })
    ])
    expect([...map.keys()].sort()).toEqual(['CNY', 'USD'])
    expect(map.get('CNY')?.length).toBe(2)
    expect(map.get('USD')?.length).toBe(2)
  })

  it('accountBalance 优先 balance,回退 initial_balance', () => {
    expect(accountBalance(acc({ balance: 42, initial_balance: 1 }))).toBe(42)
    expect(accountBalance(acc({ balance: null, initial_balance: 9 }))).toBe(9)
    expect(accountBalance(acc({ initial_balance: 3 }))).toBe(3)
    expect(accountBalance(acc({}))).toBe(0)
  })

  it('computeCurrencySummary:资产保留符号、负债按 |balance| 计欠款', () => {
    const s = computeCurrencySummary([
      acc({ account_type: 'cash', balance: 1000 }),
      acc({ account_type: 'bank_card', balance: -200 }), // 透支资产 → 扣减总资产
      acc({ account_type: 'credit_card', balance: -300 }), // 负债
      acc({ account_type: 'loan', balance: -500 }) // 负债
    ])
    expect(s.assetTotal).toBe(800) // 1000 + (-200)
    expect(s.liabilityTotal).toBe(800) // |−300| + |−500|
    expect(s.netWorth).toBe(0) // 800 − 800
  })

  it('每币种汇总各自独立 —— CNY 与 USD 不合并', () => {
    const rows = [
      acc({ account_type: 'cash', currency: 'CNY', balance: 2_472_500 }),
      acc({ account_type: 'cash', currency: 'USD', balance: 1200 }),
      acc({ account_type: 'credit_card', currency: 'USD', balance: -300 })
    ]
    const byCur = splitByCurrency(rows)
    const cny = computeCurrencySummary(byCur.get('CNY') ?? [])
    const usd = computeCurrencySummary(byCur.get('USD') ?? [])

    expect(cny.netWorth).toBe(2_472_500)
    expect(usd.assetTotal).toBe(1200)
    expect(usd.liabilityTotal).toBe(300)
    expect(usd.netWorth).toBe(900)

    // 反例:旧 bug 的裸加会把 $ 当 ¥ 得到 2_473_400 这种错值。分币种后绝不会出现。
    const naiveWrong = rows.reduce((sum, r) => {
      const raw = accountBalance(r)
      return sum + (LIABILITY_TYPES.has(r.account_type || '') ? -Math.abs(raw) : raw)
    }, 0)
    expect(naiveWrong).toBe(2_473_400)
    expect(cny.netWorth).not.toBe(naiveWrong)
  })
})
