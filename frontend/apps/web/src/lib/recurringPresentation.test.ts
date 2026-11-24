import { describe, expect, it } from 'vitest'
import type { RecurringRuleRow, RecurringTransaction } from '@beecount/api-client'
import {
  editRecurringTransaction,
  initialRecurringView,
  lastPostingDate,
  perPeriodAmount,
  recurringFormCurrency,
  recurringRuleTitle,
  recurringStartDate,
  recurringSubtitle,
  shortPostingDate,
} from './recurringPresentation'

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
  it('uses the scheduler’s local calendar anchor for a timestamp that crosses a month or year boundary', () => {
    const rule = { ...row.data.rule, starts_at: '2027-01-01T02:00:00Z', time_zone: 'America/Los_Angeles' }
    const saved = { ...row.data, rule, anchor_local: '2026-12-31T18:00:00-08:00' }
    expect(recurringStartDate(rule, saved)).toBe('2026-12-31')
    expect(recurringStartDate(rule)).toBe('2026-12-31')
    expect(recurringStartDate({ ...rule, time_zone: 'Asia/Shanghai' }, saved)).toBe('2027-01-01')
    expect(recurringStartDate({ ...rule, starts_at: '2027-01-01' }, saved)).toBe('2027-01-01')
  })
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
  const accounts = [
    { sync_id: 'dollar', name: 'Dollar wallet', currency: 'USD' },
    { sync_id: 'base', name: 'Cash', currency: null },
  ]

  it('shows the effective currency before saving a new rule with a foreign account', () => {
    const draft = editRecurringTransaction(tx, { account_id: 'dollar' }, undefined, 'CNY', accounts)
    expect(recurringFormCurrency(tx, 'CNY', accounts)).toBe('CNY')
    expect(recurringFormCurrency(draft, 'CNY', accounts)).toBe('USD')
    expect(draft.currency).toBeUndefined()
    expect(recurringFormCurrency({ ...draft, currency: 'EUR' }, 'CNY', accounts)).toBe('EUR')
    expect(recurringFormCurrency({ ...tx, account: 'Dollar wallet' }, 'CNY', accounts)).toBe('USD')
  })

  it('displays the transfer source currency, including schedules using the older account alias', () => {
    const transfer: RecurringTransaction = { ...tx, tx_type: 'transfer', account_id: 'dollar', currency: 'EUR' }
    expect(recurringFormCurrency(transfer, 'CNY', accounts)).toBe('USD')
    expect(recurringFormCurrency({ ...transfer, from_account_id: 'base' }, 'CNY', accounts)).toBe('CNY')
  })

  it('preserves the saved conversion after clearing and retyping an amount and releases it on a currency change', () => {
    const saved = { ...tx, account_id: 'dollar', amount: 10, native_amount: 70 }
    const cleared = editRecurringTransaction(saved, { amount: 0 }, saved, 'CNY', accounts)
    const changed = editRecurringTransaction(cleared, { amount: 20 }, saved, 'CNY', accounts)
    expect(changed.native_amount).toBe(140)
    const moved = editRecurringTransaction(changed, { account_id: 'base' }, saved, 'CNY', accounts)
    expect(moved.native_amount).toBeNull()
    expect(editRecurringTransaction(moved, { amount: 30 }, saved, 'CNY', accounts).native_amount).toBeNull()
  })

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

describe('initial recurring filter', () => {
  it('opens the first populated state when a ledger has no active bills', () => {
    expect(initialRecurringView([{ status: 'completed' }])).toBe('completed')
    expect(initialRecurringView([{ status: 'completed' }, { status: 'paused' }])).toBe('paused')
  })

  it('prefers active, including a posting in progress, and has a stable empty state', () => {
    expect(initialRecurringView([{ status: 'paused' }, { status: 'executing' }])).toBe('active')
    expect(initialRecurringView([])).toBe('active')
  })
})

describe('bill subtitle', () => {
  it('retains distinct category and note while omitting empty or repeated details', () => {
    expect(recurringSubtitle('租金', '住房', '含物业费')).toBe('住房 · 含物业费')
    expect(recurringSubtitle('租金', '住房', ' 租金 ')).toBe('住房')
    expect(recurringSubtitle('租金', ' 住房 ', '住房')).toBe('住房')
    expect(recurringSubtitle('租金', '租金', ' ')).toBe('')
  })
})

describe('recurring rule title', () => {
  const categories = [{ sync_id: 'rent', name: 'Housing', kind: 'expense' }]

  it('uses a new bill’s note, category, or localized type', () => {
    const transaction = { ...tx, category_id: 'rent' }
    expect(recurringRuleTitle({ name: '', transaction: { ...transaction, note: '  November rent  ' } }, categories, 'Expense'))
      .toBe('November rent')
    expect(recurringRuleTitle({ name: '', transaction }, categories, 'Expense')).toBe('Housing')
    expect(recurringRuleTitle({ name: '', transaction: tx }, categories, 'Expense')).toBe('Expense')
    expect(recurringRuleTitle({ name: '', transaction: { ...transaction, tx_type: 'transfer' } }, categories, 'Transfer'))
      .toBe('Transfer')
  })

  it('retains existing titles and limits derived titles without changing a long note', () => {
    const transaction = { ...tx, note: '🐝'.repeat(260) }
    expect(recurringRuleTitle({ name: 'Existing title', transaction }, categories, 'Expense')).toBe('Existing title')
    expect(recurringRuleTitle({ name: '', transaction }, categories, 'Expense')).toBe('🐝'.repeat(255))
    expect(transaction.note).toBe('🐝'.repeat(260))
  })
})
