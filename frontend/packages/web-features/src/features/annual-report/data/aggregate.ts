/**
 * 年度报告聚合 logic。**纯函数 + 离线**,不依赖任何 server / context。
 *
 * 输入:今年 + 去年的 TransactionLite[],输出 AnnualReportData。
 * 一年内典型 1-3K 笔,聚合 < 100ms,主线程 OK。极端 > 5K 笔时考虑 Web Worker
 * (本期不做)。
 */
import type {
  AnnualReportData,
  CategoryStat,
  DayStat,
  HourBucket,
  MonthBucket,
  TagStat,
  TransactionLite,
} from './types'
import { computeAchievements } from './achievements'

/**
 * 笔数 >= MIN_RECORDS 才生成报告,否则显示「数据太少」兜底。
 * 设到 30 是为了至少有月度趋势可看(每月 2-3 笔)。
 */
export const MIN_RECORDS_FOR_REPORT = 30

export type AggregateInput = {
  thisYearTxs: TransactionLite[]
  prevYearTxs: TransactionLite[]
  year: number
  ledger: { id: string; name: string; currency: string }
}

export function aggregate(input: AggregateInput): AnnualReportData {
  const { thisYearTxs, prevYearTxs, year, ledger } = input

  // 排除 transfer(转账不算收入也不算支出,只是账户间挪)
  const txs = thisYearTxs.filter((t) => t.txType !== 'transfer')
  const prevTxs = prevYearTxs.filter((t) => t.txType !== 'transfer')

  // ===== 整体规模 =====
  const totalRecords = txs.length
  const totalIncome = sumBy(txs, (t) => (t.txType === 'income' ? t.amount : 0))
  const totalExpense = sumBy(txs, (t) => (t.txType === 'expense' ? t.amount : 0))
  const netSavings = totalIncome - totalExpense
  const savingsRate = totalIncome > 0 ? (netSavings / totalIncome) * 100 : 0

  const isLeap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
  const totalDays = isLeap ? 366 : 365

  // 记账天数(distinct YYYY-MM-DD)
  const dayOfTx = (t: TransactionLite) => t.happenedAt.slice(0, 10)
  const recordingDaySet = new Set<string>()
  for (const t of txs) recordingDaySet.add(dayOfTx(t))
  const recordingDays = recordingDaySet.size

  // ===== 跟去年比 =====
  const prevYearIncome = sumBy(prevTxs, (t) =>
    t.txType === 'income' ? t.amount : 0
  )
  const prevYearExpense = sumBy(prevTxs, (t) =>
    t.txType === 'expense' ? t.amount : 0
  )
  const prevYearRecords = prevTxs.length

  const yoyIncomeChange = pctChange(prevYearIncome, totalIncome)
  const yoyExpenseChange = pctChange(prevYearExpense, totalExpense)
  const yoyRecordChange = pctChange(prevYearRecords, totalRecords)

  // ===== 月份分布 =====
  const monthlyData: MonthBucket[] = Array.from({ length: 12 }, (_, i) => ({
    month: i + 1,
    income: 0,
    expense: 0,
    netFlow: 0,
    count: 0,
  }))
  for (const t of txs) {
    const m = new Date(t.happenedAt).getMonth() // 0-11
    monthlyData[m].count++
    if (t.txType === 'income') monthlyData[m].income += t.amount
    else if (t.txType === 'expense') monthlyData[m].expense += t.amount
  }
  for (const b of monthlyData) b.netFlow = b.income - b.expense

  const monthsWithData = monthlyData.filter((b) => b.count > 0)
  const peakMonth =
    monthsWithData.length > 0
      ? monthsWithData.reduce((max, b) =>
          b.expense > max.expense ? b : max
        ).month
      : 1
  const troughMonth =
    monthsWithData.length > 0
      ? monthsWithData.reduce((min, b) =>
          b.expense < min.expense ? b : min
        ).month
      : 1

  // ===== 分类 =====
  const topExpenseCategories = computeTopCategories(txs, 'expense', 5)
  const topIncomeCategories = computeTopCategories(txs, 'income', 5)

  // ===== 时段分布(只统计支出)=====
  const hourBuckets: HourBucket[] = [
    { bucket: 'night', count: 0, total: 0 },
    { bucket: 'morning', count: 0, total: 0 },
    { bucket: 'afternoon', count: 0, total: 0 },
    { bucket: 'evening', count: 0, total: 0 },
  ]
  for (const t of txs) {
    if (t.txType !== 'expense') continue
    const h = new Date(t.happenedAt).getHours()
    const idx = h < 6 ? 0 : h < 12 ? 1 : h < 18 ? 2 : 3
    hourBuckets[idx].count++
    hourBuckets[idx].total += t.amount
  }

  // ===== 工作日 vs 周末(只统计支出)=====
  const weekdayDays = new Set<string>()
  const weekendDays = new Set<string>()
  let weekdayExpense = 0
  let weekendExpense = 0
  for (const t of txs) {
    if (t.txType !== 'expense') continue
    const d = new Date(t.happenedAt)
    const day = d.getDay() // 0=Sun, 6=Sat
    const isWeekend = day === 0 || day === 6
    if (isWeekend) {
      weekendDays.add(dayOfTx(t))
      weekendExpense += t.amount
    } else {
      weekdayDays.add(dayOfTx(t))
      weekdayExpense += t.amount
    }
  }
  const weekdayAvgExpense =
    weekdayDays.size > 0 ? weekdayExpense / weekdayDays.size : 0
  const weekendAvgExpense =
    weekendDays.size > 0 ? weekendExpense / weekendDays.size : 0
  const weekendBoost =
    weekdayAvgExpense > 0 ? weekendAvgExpense / weekdayAvgExpense : 0

  // ===== 极端时刻 =====
  const expenses = txs.filter((t) => t.txType === 'expense')
  const incomes = txs.filter((t) => t.txType === 'income')

  const largestExpense = expenses.length
    ? expenses.reduce((max, t) => (t.amount > max.amount ? t : max))
    : null
  const largestIncome = incomes.length
    ? incomes.reduce((max, t) => (t.amount > max.amount ? t : max))
    : null
  const firstRecord = txs.length
    ? txs.reduce((min, t) => (t.happenedAt < min.happenedAt ? t : min))
    : null
  const lastRecord = txs.length
    ? txs.reduce((max, t) => (t.happenedAt > max.happenedAt ? t : max))
    : null

  // 单日聚合(只算支出)
  const dayMap = new Map<string, DayStat>()
  for (const t of expenses) {
    const d = dayOfTx(t)
    const cur = dayMap.get(d) ?? { date: d, total: 0, count: 0 }
    cur.total += t.amount
    cur.count++
    dayMap.set(d, cur)
  }
  const dayStats = Array.from(dayMap.values())
  const mostExpensiveDay = dayStats.length
    ? dayStats.reduce((max, d) => (d.total > max.total ? d : max))
    : null
  const mostFrugalDay = dayStats.length
    ? dayStats.reduce((min, d) => (d.total < min.total ? d : min))
    : null

  // ===== 习惯画像 =====
  const sortedDays = Array.from(recordingDaySet).sort()
  const maxConsecutiveDays = computeMaxConsecutiveDays(sortedDays)

  const avgDailyExpense =
    dayStats.length > 0
      ? dayStats.reduce((s, d) => s + d.total, 0) / dayStats.length
      : 0

  // 按周聚合,周均笔数
  const weekMap = new Map<string, number>()
  for (const t of txs) {
    const w = isoWeekKey(new Date(t.happenedAt))
    weekMap.set(w, (weekMap.get(w) ?? 0) + 1)
  }
  const recordsPerWeekAvg =
    weekMap.size > 0 ? totalRecords / weekMap.size : 0

  // ===== 标签 =====
  const topTags = computeTopTags(txs, 6)

  // ===== 成就 =====
  const tempData: Omit<AnnualReportData, 'achievements'> = {
    year,
    ledgerId: ledger.id,
    ledgerName: ledger.name,
    ledgerCurrency: ledger.currency,
    hasSufficientData: totalRecords >= MIN_RECORDS_FOR_REPORT,
    totalRecords,
    totalIncome,
    totalExpense,
    netSavings,
    savingsRate,
    totalDays,
    recordingDays,
    prevYear: {
      totalIncome: prevYearIncome,
      totalExpense: prevYearExpense,
      totalRecords: prevYearRecords,
    },
    yoyIncomeChange,
    yoyExpenseChange,
    yoyRecordChange,
    monthlyData,
    peakMonth,
    troughMonth,
    topExpenseCategories,
    topIncomeCategories,
    hourBuckets,
    weekdayAvgExpense,
    weekendAvgExpense,
    weekendBoost,
    largestExpense,
    largestIncome,
    firstRecord,
    lastRecord,
    mostExpensiveDay,
    mostFrugalDay,
    maxConsecutiveDays,
    avgDailyExpense,
    recordsPerWeekAvg,
    topTags,
  }
  const achievements = computeAchievements(tempData)

  return { ...tempData, achievements }
}

// ============================================================================
// 内部 helpers
// ============================================================================

function sumBy<T>(arr: T[], fn: (t: T) => number): number {
  let s = 0
  for (const t of arr) s += fn(t)
  return s
}

function pctChange(prev: number, curr: number): number {
  if (prev === 0) return curr === 0 ? 0 : 100
  return ((curr - prev) / prev) * 100
}

function computeTopCategories(
  txs: TransactionLite[],
  kind: 'expense' | 'income',
  limit: number
): CategoryStat[] {
  const filtered = txs.filter((t) => t.txType === kind)
  const total = sumBy(filtered, (t) => t.amount)
  const map = new Map<string, { total: number; count: number }>()
  for (const t of filtered) {
    const name = t.categoryName ?? '未分类'
    const cur = map.get(name) ?? { total: 0, count: 0 }
    cur.total += t.amount
    cur.count++
    map.set(name, cur)
  }
  const sorted = Array.from(map.entries())
    .map(([name, v]) => ({
      name,
      total: v.total,
      count: v.count,
      percent: total > 0 ? (v.total / total) * 100 : 0,
    }))
    .sort((a, b) => b.total - a.total)
  return sorted.slice(0, limit)
}

function computeTopTags(txs: TransactionLite[], limit: number): TagStat[] {
  const map = new Map<string, { count: number; total: number }>()
  for (const t of txs) {
    if (t.txType !== 'expense') continue
    for (const tag of t.tagsList) {
      const cur = map.get(tag) ?? { count: 0, total: 0 }
      cur.count++
      cur.total += t.amount
      map.set(tag, cur)
    }
  }
  return Array.from(map.entries())
    .map(([name, v]) => ({ name, count: v.count, total: v.total }))
    .sort((a, b) => b.count - a.count || b.total - a.total)
    .slice(0, limit)
}

function computeMaxConsecutiveDays(sortedDays: string[]): number {
  if (sortedDays.length === 0) return 0
  let max = 1
  let cur = 1
  for (let i = 1; i < sortedDays.length; i++) {
    const a = new Date(sortedDays[i - 1])
    const b = new Date(sortedDays[i])
    const diff = Math.round((b.getTime() - a.getTime()) / 86400000)
    if (diff === 1) {
      cur++
      if (cur > max) max = cur
    } else {
      cur = 1
    }
  }
  return max
}

function isoWeekKey(d: Date): string {
  // ISO 8601 周编号:周一是一周的第一天,某年第一周包含该年第一个周四。
  // 简化版:用「年-周」字符串作 key,周用 Math.floor((dayOfYear + dayOfWeek) / 7)。
  const start = new Date(d.getFullYear(), 0, 1)
  const dayOfYear = Math.floor(
    (d.getTime() - start.getTime()) / 86400000
  )
  const week = Math.floor((dayOfYear + start.getDay()) / 7)
  return `${d.getFullYear()}-${week}`
}
