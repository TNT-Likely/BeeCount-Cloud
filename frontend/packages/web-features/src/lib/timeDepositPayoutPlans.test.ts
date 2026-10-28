import { describe, expect, it } from 'vitest'

import { nextPayoutDate, payoutPlansTotal, payoutPlansToWire } from './timeDepositPayoutPlans'

/** 到息计划(0028,多笔)的纯逻辑。过滤口径与服务端 _normalize_payout_plans 一致。 */
describe('payoutPlansToWire', () => {
  it('keeps valid rows and drops incomplete ones', () => {
    const wire = payoutPlansToWire([
      { date: '2026-06-01', amount: '162.5', note: '第二期' },
      { date: '', amount: '50', note: '' },
      { date: '2026-03-01', amount: '', note: '' },
      { date: '2026-09-01', amount: '0', note: '' }
    ])
    expect(wire).toEqual([{ date: '2026-06-01', amount: 162.5, note: '第二期' }])
  })

  it('drops non-positive and non-numeric amounts', () => {
    expect(payoutPlansToWire([{ date: '2026-01-01', amount: '-5' }])).toEqual([])
    expect(payoutPlansToWire([{ date: '2026-01-01', amount: 'abc' }])).toEqual([])
  })

  it('turns blank notes into null', () => {
    expect(payoutPlansToWire([{ date: '2026-01-01', amount: '10', note: '  ' }])).toEqual([
      { date: '2026-01-01', amount: 10, note: null }
    ])
  })

  it('handles null / empty input', () => {
    expect(payoutPlansToWire([])).toEqual([])
    expect(payoutPlansToWire(null)).toEqual([])
    expect(payoutPlansToWire(undefined)).toEqual([])
  })

  it('skips null rows inside the array', () => {
    expect(payoutPlansToWire([null as never, { date: '2026-01-01', amount: '5' }])).toEqual([
      { date: '2026-01-01', amount: 5, note: null }
    ])
  })
})

describe('payoutPlansTotal', () => {
  it('sums amounts and rounds to 2 decimals', () => {
    expect(payoutPlansTotal([{ amount: 100 }, { amount: 50.25 }])).toBe(150.25)
    expect(payoutPlansTotal([{ amount: 0.1 }, { amount: 0.2 }])).toBe(0.3)
  })

  it('ignores non-numeric amounts', () => {
    expect(payoutPlansTotal([{ amount: 100 }, { amount: 'abc' }, { amount: null }])).toBe(100)
  })

  it('returns 0 for empty input', () => {
    expect(payoutPlansTotal([])).toBe(0)
    expect(payoutPlansTotal(null)).toBe(0)
  })
})

describe('nextPayoutDate', () => {
  it('returns the earliest date that is not due yet', () => {
    expect(
      nextPayoutDate([
        { date: '2026-01-01', is_due: true },
        { date: '2026-09-01', is_due: false },
        { date: '2026-06-01', is_due: false }
      ])
    ).toBe('2026-06-01')
  })

  it('returns null when everything is due or there are no plans', () => {
    expect(nextPayoutDate([{ date: '2026-01-01', is_due: true }])).toBeNull()
    expect(nextPayoutDate([])).toBeNull()
    expect(nextPayoutDate(null)).toBeNull()
  })
})
