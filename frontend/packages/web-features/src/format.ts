import type { ReadLedger } from '@beecount/api-client'

export function formatAmountCny(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `CNY ${value.toFixed(2)}`
}

/**
 * 紧凑金额格式化（对齐 mobile `utils/format_utils.dart#formatBalance`）。
 *
 * - 中文环境：< 1 万保留两位；≥ 1 万按 1-2 位小数折算成 "X.X万"。
 * - 其他环境：≥ 100 万折算 M、≥ 1 千折算 k，< 1 千保留两位。
 * - currencyCode 传 null 时不带币种符号（BankCardTile 独立展示 currency pill，
 *   不想在金额字符串里再重复一次）。
 */
export function formatBalanceCompact(
  value: number | null | undefined,
  currencyCode?: string | null,
  opts?: { chinese?: boolean }
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const chinese = opts?.chinese ?? true
  const absVal = Math.abs(value)
  const symbol = currencyCode ? currencySymbol(currencyCode) : ''
  const sign = value >= 0 ? symbol : `-${symbol}`

  const trimZero = (s: string) => s.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')

  if (chinese) {
    if (absVal < 10000) return `${sign}${absVal.toFixed(2)}`
    const wan = absVal / 10000
    const r1 = Number(wan.toFixed(1))
    const err1 = Math.abs(r1 * 10000 - absVal)
    const threshold = wan >= 10 ? 100 : 50
    const formatted = err1 > threshold ? wan.toFixed(2) : wan.toFixed(1)
    return `${sign}${trimZero(formatted)}万`
  }

  if (absVal >= 1_000_000) {
    const m = absVal / 1_000_000
    const r1 = Number(m.toFixed(1))
    const formatted = Math.abs(r1 * 1_000_000 - absVal) > 1000 ? m.toFixed(2) : m.toFixed(1)
    return `${sign}${trimZero(formatted)}M`
  }
  if (absVal >= 1000) {
    const k = absVal / 1000
    const r1 = Number(k.toFixed(1))
    const formatted = Math.abs(r1 * 1000 - absVal) > 100 ? k.toFixed(2) : k.toFixed(1)
    return `${sign}${trimZero(formatted)}k`
  }
  return `${sign}${absVal.toFixed(2)}`
}

function currencySymbol(code: string): string {
  switch (code.toUpperCase()) {
    case 'CNY':
      return '¥'
    case 'USD':
      return '$'
    case 'EUR':
      return '€'
    case 'JPY':
      return '¥'
    case 'HKD':
      return 'HK$'
    case 'GBP':
      return '£'
    default:
      return ''
  }
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
