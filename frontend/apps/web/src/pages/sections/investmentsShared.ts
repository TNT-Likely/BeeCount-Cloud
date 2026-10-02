/** 投資頁(InvestmentsPage / PendingDividendsPanel)共用的小工具。 */

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || Math.abs(value) < 1e-9) return 'text-muted-foreground'
  return value > 0 ? 'text-income' : 'text-expense'
}

export function formatQuoteTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function todayDateValue(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 純日期 → 當地中午的 ISO,避免時區位移讓日期差一天。 */
export function dateValueToIso(value: string): string {
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, (m || 1) - 1, d || 1, 12, 0, 0).toISOString()
}

export function isoToDateValue(iso: string | null | undefined): string {
  if (!iso) return todayDateValue()
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return todayDateValue()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function numText(value: number | null | undefined): string {
  if (value === null || value === undefined) return ''
  return String(Number(value.toFixed(6)))
}

/**
 * 股票分割比例的顯示資料:shares 欄位存「每 1 股變成幾股」。
 * 比例 ≥ 1 → 拆股「1→N」;< 1 → 反向分割(合併)「N→1」,N = 1/比例。
 * 回傳 null 代表比例不合法(≤ 0 或非有限數)。
 */
export function splitRatioInfo(ratio: number): { merge: boolean; n: number } | null {
  if (!Number.isFinite(ratio) || ratio <= 0) return null
  if (ratio >= 1) return { merge: false, n: trimNumber(ratio) }
  return { merge: true, n: trimNumber(1 / ratio) }
}

function trimNumber(v: number): number {
  return Number(v.toFixed(4))
}
