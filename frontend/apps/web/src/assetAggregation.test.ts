import type { ExchangeRateOverride, ExchangeRatesResponse, ReadAccount } from '@beecount/api-client'
import {
  accountBalance,
  type AssetGroup,
  computeCurrencySummary,
  effectiveRateToBase,
  LIABILITY_TYPES,
  mergeGroupsToBase,
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

/**
 * effectiveRateToBase 契约 —— pin 住各边界分支的有意行为。
 *
 * 特别注意:override 存在但非法(非 finite / <=0)→ 返回 null,**不回落 auto**。
 * 这是有意行为:用户手动填了一个坏值,宁可让折算缺失也不静默用自动值混淆来源。
 */
describe('effectiveRateToBase', () => {
  // 构造辅助
  function auto(rates: Record<string, string>, rateDate = '2025-01-01'): ExchangeRatesResponse {
    return { rates, rate_date: rateDate } as ExchangeRatesResponse
  }
  function ov(base_currency: string, quote_currency: string, rate: string): ExchangeRateOverride {
    return { base_currency, quote_currency, rate } as ExchangeRateOverride
  }

  it('① quote === base → rate 1, source auto', () => {
    const result = effectiveRateToBase('CNY', 'CNY', null, [])
    expect(result).not.toBeNull()
    expect(result!.rate).toBe(1)
    expect(result!.source).toBe('auto')
  })

  it('② override 优先于 auto —— override rate 用于计算,source=manual', () => {
    const autoRates = auto({ USD: '7.2' })    // 1 CNY = 7.2 USD → auto: 1 USD = 1/7.2 CNY
    const overrides = [ov('CNY', 'USD', '7.5')] // 1 USD = 7.5 CNY (手动)
    const result = effectiveRateToBase('USD', 'CNY', autoRates, overrides)
    expect(result).not.toBeNull()
    expect(result!.rate).toBe(7.5)
    expect(result!.source).toBe('manual')
  })

  it('③ auto 取倒数 —— rates["USD"]="0.25" → 1 USD = 4 base', () => {
    // auto rates 存储的是 1 base = x quote,故 1 quote = 1/x base
    const autoRates = auto({ USD: '0.25' })  // 1 CNY = 0.25 USD → 1 USD = 4 CNY
    const result = effectiveRateToBase('USD', 'CNY', autoRates, [])
    expect(result).not.toBeNull()
    expect(result!.rate).toBeCloseTo(4)
    expect(result!.source).toBe('auto')
  })

  it('④ override 存在但非法(非 finite/<=0) → null,不回落 auto(有意行为,pin 住)', () => {
    const autoRates = auto({ USD: '7.2' })   // auto 有值
    const overrides = [ov('CNY', 'USD', 'bad')] // override rate 非法
    expect(effectiveRateToBase('USD', 'CNY', autoRates, overrides)).toBeNull()

    const overridesZero = [ov('CNY', 'USD', '0')]
    expect(effectiveRateToBase('USD', 'CNY', autoRates, overridesZero)).toBeNull()

    const overridesNeg = [ov('CNY', 'USD', '-1')]
    expect(effectiveRateToBase('USD', 'CNY', autoRates, overridesNeg)).toBeNull()
  })

  it('⑤ auto 缺失/非法 → null', () => {
    // auto 为 null
    expect(effectiveRateToBase('USD', 'CNY', null, [])).toBeNull()

    // auto 存在但该 quote 不在 rates 里
    expect(effectiveRateToBase('EUR', 'CNY', auto({ USD: '7.2' }), [])).toBeNull()

    // auto rates 值非法
    expect(effectiveRateToBase('USD', 'CNY', auto({ USD: 'NaN' }), [])).toBeNull()
    expect(effectiveRateToBase('USD', 'CNY', auto({ USD: '0' }), [])).toBeNull()
  })
})

/**
 * mergeGroupsToBase 契约 —— 折算汇总视图的「合并构成 donut」聚合。
 * 锁两点:① 各币种同类型折算后跨币种累加进主币种;② 缺失汇率的整币种**剔除**,
 * 绝不按 1 折入(与净资产/资产/负债折算同口径)。
 */
describe('mergeGroupsToBase — 折算合并构成', () => {
  function group(p: Partial<AssetGroup> & { value: number; currency?: string }): AssetGroup {
    return {
      type: p.type ?? 'cash',
      label: p.label ?? p.type ?? 'cash',
      color: p.color ?? '#000',
      isLiability: p.isLiability ?? false,
      rows: p.rows ?? [],
      subtotals: [{ currency: p.currency ?? 'CNY', value: p.value }]
    }
  }
  function auto(rates: Record<string, string>): ExchangeRatesResponse {
    return { rates, rate_date: '2025-01-01' } as ExchangeRatesResponse
  }

  it('各币种同类型按汇率折算后跨币种合并到主币种', () => {
    // base=CNY。USD 走 auto:1 CNY = 0.25 USD → 1 USD = 4 CNY。
    const buckets = [
      { currency: 'CNY', groups: [group({ type: 'cash', value: 1000, currency: 'CNY' })] },
      {
        currency: 'USD',
        groups: [
          group({ type: 'cash', value: 100, currency: 'USD' }), // ×4 = 400 CNY
          group({ type: 'bank_card', value: 50, currency: 'USD' }) // ×4 = 200 CNY
        ]
      }
    ]
    const merged = mergeGroupsToBase(buckets, 'CNY', auto({ USD: '0.25' }), [])
    const cash = merged.find((g) => g.type === 'cash')!
    const bank = merged.find((g) => g.type === 'bank_card')!
    // cash: 1000(CNY) + 100×4(USD) = 1400;输出单条 subtotal、币种为 base。
    expect(cash.subtotals).toHaveLength(1)
    expect(cash.subtotals[0].currency).toBe('CNY')
    expect(cash.subtotals[0].value).toBeCloseTo(1400)
    expect(bank.subtotals[0].value).toBeCloseTo(200)
  })

  it('缺失汇率的整币种被剔除,绝不按 1 折入', () => {
    // EUR 既无 override 也不在 auto.rates → 整币种丢弃。
    const buckets = [
      { currency: 'CNY', groups: [group({ type: 'cash', value: 1000, currency: 'CNY' })] },
      { currency: 'EUR', groups: [group({ type: 'cash', value: 999, currency: 'EUR' })] }
    ]
    const merged = mergeGroupsToBase(buckets, 'CNY', auto({ USD: '0.25' }), [])
    const cash = merged.find((g) => g.type === 'cash')!
    // 只剩 CNY 的 1000;EUR 的 999 既没按 1 加、也没生成新组。
    expect(cash.subtotals[0].value).toBe(1000)
    expect(cash.subtotals[0].value).not.toBe(1999)
  })
})
