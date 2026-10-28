import { describe, expect, it } from 'vitest'

import type { ExchangeRateOverride, ExchangeRatesResponse } from '@beecount/api-client'

import {
  loanTotalsToBase,
  reimbursementTotalsToBase,
} from './assetAggregation'

const AUTO: ExchangeRatesResponse = {
  base: 'CNY',
  rate_date: '2026-01-01',
  source: 'test',
  fetched_at: '2026-01-01T00:00:00Z',
  stale: false,
  // 语义:1 CNY = x quote(rates 字段)。故 USD→CNY 乘数 = 1/0.142857 ≈ 7。
  rates: { USD: '0.14285714285714285', JPY: '0.05' },
}

const NO_RATES: ExchangeRatesResponse | null = null

describe('loanTotalsToBase', () => {
  it('借出未结应收作为资产、借入未结应付作为负债', () => {
    const { lentAsset, borrowedLiability } = loanTotalsToBase(
      [
        { direction: 'lent', principal: 8000, repaid_amount: 2000, currency: 'CNY', status: 'active' },
        { direction: 'borrowed', principal: 3000, repaid_amount: 0, currency: 'CNY', status: 'active' },
      ],
      'CNY',
      AUTO,
      [],
    )
    expect(lentAsset).toBeCloseTo(6000, 6) // 8000 - 2000 应收
    expect(borrowedLiability).toBeCloseTo(3000, 6) // 3000 - 0 应付
  })

  it('跨币种按汇率折算到主币种', () => {
    const { lentAsset } = loanTotalsToBase(
      [{ direction: 'lent', principal: 700, repaid_amount: 0, currency: 'USD', status: 'active' }],
      'CNY',
      AUTO,
      [],
    )
    expect(lentAsset).toBeCloseTo(4900, 6) // 700 * 7
  })

  it('非 active 状态不计入', () => {
    const { lentAsset, borrowedLiability } = loanTotalsToBase(
      [
        { direction: 'lent', principal: 5000, repaid_amount: 0, currency: 'CNY', status: 'repaid' },
        { direction: 'borrowed', principal: 5000, repaid_amount: 0, currency: 'CNY', status: 'cancelled' },
      ],
      'CNY',
      AUTO,
      [],
    )
    expect(lentAsset).toBe(0)
    expect(borrowedLiability).toBe(0)
  })

  it('已还清(outstanding<=0)不计入', () => {
    const { lentAsset, borrowedLiability } = loanTotalsToBase(
      [{ direction: 'lent', principal: 1000, repaid_amount: 1000, currency: 'CNY', status: 'active' }],
      'CNY',
      AUTO,
      [],
    )
    expect(lentAsset).toBe(0)
    expect(borrowedLiability).toBe(0)
  })

  it('缺失汇率的整币种剔除并回报 missing,绝不按 1 折入(同币种 rate=1 仍计入)', () => {
    const r = loanTotalsToBase(
      [{ direction: 'lent', principal: 100, repaid_amount: 0, currency: 'EUR', status: 'active' }],
      'CNY',
      NO_RATES,
      [],
    )
    expect(r.lentAsset).toBe(0)
    expect(r.missing).toEqual(['EUR'])
  })

  it('手动 override 优先于自动汇率', () => {
    const overrides: ExchangeRateOverride[] = [
      { sync_id: 'o1', base_currency: 'CNY', quote_currency: 'USD', rate: '8', updated_at: '2026-01-01T00:00:00Z' },
    ]
    const { lentAsset } = loanTotalsToBase(
      [{ direction: 'lent', principal: 100, repaid_amount: 0, currency: 'USD', status: 'active' }],
      'CNY',
      AUTO,
      overrides,
    )
    expect(lentAsset).toBeCloseTo(800, 6) // 100 * 8
  })
})

describe('reimbursementTotalsToBase', () => {
  it('待报销金额作为应收资产', () => {
    const { asset } = reimbursementTotalsToBase(
      [
        { amount: 1200, currency: 'CNY', status: 'pending' },
        { amount: 600, currency: 'CNY', status: 'pending' },
      ],
      'CNY',
      AUTO,
      [],
    )
    expect(asset).toBeCloseTo(1800, 6)
  })

  it('已报销 / 已取消不计入', () => {
    const { asset } = reimbursementTotalsToBase(
      [
        { amount: 1200, currency: 'CNY', status: 'reimbursed' },
        { amount: 600, currency: 'CNY', status: 'cancelled' },
      ],
      'CNY',
      AUTO,
      [],
    )
    expect(asset).toBe(0)
  })

  it('跨币种按汇率折算', () => {
    const { asset } = reimbursementTotalsToBase(
      [{ amount: 100, currency: 'USD', status: 'pending' }],
      'CNY',
      AUTO,
      [],
    )
    expect(asset).toBeCloseTo(700, 6)
  })

  it('缺失汇率的整币种剔除并回报 missing', () => {
    const r = reimbursementTotalsToBase(
      [{ amount: 100, currency: 'EUR', status: 'pending' }],
      'CNY',
      NO_RATES,
      [],
    )
    expect(r.asset).toBe(0)
    expect(r.missing).toEqual(['EUR'])
  })
})
