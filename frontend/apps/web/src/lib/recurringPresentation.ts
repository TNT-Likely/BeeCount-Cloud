import type { RecurringRuleRow, RecurringTransaction } from '@beecount/api-client'

export const recurringViews = ['active', 'paused', 'completed'] as const
export type RecurringView = (typeof recurringViews)[number]
type RuleStatus = Pick<RecurringRuleRow, 'status'>

export const recurringViewFor = (row: RuleStatus): RecurringView =>
  row.status === 'executing' ? 'active' : row.status

export function initialRecurringView(items: RuleStatus[]): RecurringView {
  return recurringViews.find((view) => items.some((row) => recurringViewFor(row) === view)) ?? 'active'
}

export function recurringSubtitle(name: string, category: string, note?: string | null) {
  const parts = [category.trim(), note?.trim()].filter((part) => part && part !== name.trim())
  return [...new Set(parts)].join(' · ')
}

export function recurringCivilDate(instant: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('sv-SE', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone,
  }).format(instant)
}

export const recurringUtcDay = (date: string) => new Date(date + 'T12:00:00Z')

export function lastPostingDate(row: RecurringRuleRow): string | null | undefined {
  // The current API includes adopted local occurrences. Older servers only expose Cloud runs.
  if (row.last_generated_local_date !== undefined) return row.last_generated_local_date
  const instant = row.data.last_due_at && new Date(row.data.last_due_at)
  return instant && Number.isFinite(+instant) ? recurringCivilDate(instant, row.data.rule.time_zone) : undefined
}

export function shortPostingDate(date: string, today: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    ...(date.slice(0, 4) !== today.slice(0, 4) && { year: 'numeric' }),
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(recurringUtcDay(date))
}

export function perPeriodAmount(
  tx: RecurringTransaction,
  baseCurrency: string,
  locale: string,
  accountCurrency: string | null | undefined,
) {
  const base = baseCurrency.toUpperCase()
  // Transfers settle in the source account currency. Undefined means the referenced account is unresolved.
  const configured = tx.tx_type === 'transfer' ? accountCurrency : tx.currency?.trim() || accountCurrency
  const currency = configured === undefined ? '' : configured?.toUpperCase() || base
  const foreign = currency !== base
  function format(value: number, code: string, explicit: boolean) {
    const signDisplay = tx.tx_type === 'income' ? 'always' : 'auto'
    const signed = tx.tx_type === 'expense' ? -value : value
    if (!/^[A-Z]{3}$/.test(code)) {
      return (
        (code ? code + ' ' : '') +
        new Intl.NumberFormat(locale, {
          signDisplay,
          minimumFractionDigits: 2,
          maximumFractionDigits: 8,
        }).format(signed)
      )
    }
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: code,
      currencyDisplay: explicit ? 'code' : 'narrowSymbol',
      signDisplay,
      maximumFractionDigits: 8,
    }).format(signed)
  }
  return {
    original: format(tx.amount, currency, foreign),
    converted:
      tx.tx_type !== 'transfer' && foreign && tx.native_amount != null
        ? format(tx.native_amount, base, false)
        : null,
  }
}
