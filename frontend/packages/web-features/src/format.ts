import type { ReadLedger } from '@beecount/api-client'

export function formatAmountCny(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `CNY ${value.toFixed(2)}`
}

export function formatIsoDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toISOString().slice(0, 19).replace('T', ' ')
}

export function formatLedgerLabel(ledger: ReadLedger, roleLabel: string): string {
  return `${ledger.ledger_name} [${roleLabel}]`
}
