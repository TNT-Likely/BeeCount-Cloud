import { describe, expect, it } from 'vitest'
import type { RecurringRuleRow, RecurringTransaction } from '@beecount/api-client'
import { lastPostingDate, perPeriodAmount, shortPostingDate } from './recurringPresentation'

const tx: RecurringTransaction = { tx_type: 'expense', amount: 8.99 }
const row: RecurringRuleRow = {
  id: 'test',
  revision: 1,
  status: 'active',
  can_edit: true,
  next_due_at: null,
  data: {
    executed_count: 0,
    rule: {
      name: 'Rent',
      frequency: 'monthly',
      interval: 1,
      starts_at: '2026-12-01',
      time_zone: 'Pacific/Kiritimati',
      enabled: true,
      transaction: tx,
    },
  },
}

describe('posting history', () => {
  it('uses authoritative dates, including history from a local installation', () => {
    expect(lastPostingDate({ ...row, last_generated_local_date: '2026-05-01' })).toBe('2026-05-01')
    expect(lastPostingDate({ ...row, last_generated_local_date: null })).toBeNull()
  })
  it('converts legacy Cloud runs into the rule timezone and distinguishes missing history', () => {
    expect(lastPostingDate({ ...row, data: { ...row.data, last_due_at: '2026-11-30T10:00:00Z' } })).toBe(
      '2026-12-01',
    )
    expect(lastPostingDate(row)).toBeUndefined()
  })
  it('includes the year for a date outside the current local year', () => {
    expect(shortPostingDate('2025-12-01', '2026-01-01', 'en')).toBe('Dec 1, 2025')
  })
})

describe('recurring currency', () => {
  it('uses the configured original amount and a supplied base amount even if numerically identical', () => {
    const value = perPeriodAmount({ ...tx, currency: 'USD', native_amount: 8.99 }, 'CNY', 'en', 'EUR')
    expect(value.original).toMatch(/USD.*8\.99/)
    expect(value.converted).toMatch(/¥8\.99/)
  })
  it('uses the account currency without estimating a conversion', () => {
    expect(perPeriodAmount(tx, 'CNY', 'en', 'USD').original).toMatch(/USD.*8\.99/)
    expect(perPeriodAmount(tx, 'CNY', 'en', 'USD').converted).toBeNull()
    expect(perPeriodAmount(tx, 'CNY', 'en', undefined).original).toBe('-8.99')
    expect(perPeriodAmount(tx, 'CNY', 'en', null).original).toBe('-¥8.99')
  })
  it('transfers follow their source account and display an unsigned amount', () => {
    const value = perPeriodAmount(
      { ...tx, tx_type: 'transfer', currency: 'EUR', native_amount: 8 },
      'CNY',
      'en',
      'USD',
    )
    expect(value.original).toMatch(/^USD.*8\.99/)
    expect(value.converted).toBeNull()
  })
})
