/** 月历视图的日期工具(纯函数,可单测)。

从 CalendarPage 抽出来,方便对「跨月 / 月末 / 闰年 / 周起始」这些易错点做单测。
所有 key 都是本地时区的字符串:月份 `YYYY-MM`,日期 `YYYY-MM-DD`。
 */

export function todayKey(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function thisMonthKey(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function parseMonth(monthKey: string): { year: number; month: number } {
  const [y, m] = monthKey.split('-').map(Number)
  return { year: y, month: m }
}

export function shiftMonth(monthKey: string, delta: number): string {
  const { year, month } = parseMonth(monthKey)
  const total = year * 12 + (month - 1) + delta
  const nextYear = Math.floor(total / 12)
  const nextMonth = (total % 12) + 1
  return `${nextYear}-${String(nextMonth).padStart(2, '0')}`
}

/** 该月天数(31 / 30 / 28 / 29)。`new Date(y, m, 0)` = 下月第 0 天 = 本月最后一天。 */
export function daysInMonth(monthKey: string): number {
  const { year, month } = parseMonth(monthKey)
  return new Date(year, month, 0).getDate()
}

/**
 * 翻月时把「选中日」搬到目标月份的同一天 —— 否则翻到 9 月后侧栏日详情还停在
 * 今天(或其它月份的日子),跟网格里的月份对不上。
 *
 * 目标月没有这一天(31 号 → 小月 / 2 月)时取该月最后一天,不跨到下一月。
 * dateKey 非法时回退到目标月 1 号。
 */
export function sameDayInMonth(monthKey: string, dateKey: string | null | undefined): string {
  const { year, month } = parseMonth(monthKey)
  const day = Number(String(dateKey || '').slice(8, 10))
  const safeDay = Number.isFinite(day) && day >= 1 ? day : 1
  const maxDay = daysInMonth(monthKey)
  const clamped = Math.min(safeDay, maxDay)
  return `${year}-${String(month).padStart(2, '0')}-${String(clamped).padStart(2, '0')}`
}

/**
 * 给定 YYYY-MM 和 weekStartsOn(0=Sun, 1=Mon),返回 6 行 7 列共 42 格的 dateKey 数组。
 * 跨月日填充:第一周前面用上月末几天,最后一周后面用下月头几天。
 */
export function buildMonthGrid(
  monthKey: string,
  weekStartsOn: 0 | 1,
): { dateKeys: string[]; firstOfMonth: string; lastOfMonth: string } {
  const { year, month } = parseMonth(monthKey)
  const firstDay = new Date(year, month - 1, 1)
  const totalDays = daysInMonth(monthKey)

  // first day's weekday: 0=Sun..6=Sat. 我们要把它对齐到 weekStartsOn 列。
  const firstWeekday = firstDay.getDay()
  const leadingCount = (firstWeekday - weekStartsOn + 7) % 7

  const dateKeys: string[] = []

  // 前置月填充
  for (let i = leadingCount; i > 0; i--) {
    dateKeys.push(formatDateKey(new Date(year, month - 1, 1 - i)))
  }
  // 当月
  for (let i = 1; i <= totalDays; i++) {
    dateKeys.push(`${year}-${String(month).padStart(2, '0')}-${String(i).padStart(2, '0')}`)
  }
  // 后置月填充至 6 行 (42 格,避免月历高度跳变)
  while (dateKeys.length < 42) {
    const last = dateKeys[dateKeys.length - 1]
    const [y, m, d] = last.split('-').map(Number)
    dateKeys.push(formatDateKey(new Date(y, m - 1, d + 1)))
  }

  return {
    dateKeys,
    firstOfMonth: `${year}-${String(month).padStart(2, '0')}-01`,
    lastOfMonth: `${year}-${String(month).padStart(2, '0')}-${String(totalDays).padStart(2, '0')}`,
  }
}

export function formatDateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function isSameMonth(dateKey: string, monthKey: string): boolean {
  return dateKey.startsWith(monthKey)
}
