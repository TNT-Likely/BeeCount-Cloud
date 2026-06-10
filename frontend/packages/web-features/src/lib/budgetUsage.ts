import {
  fetchReadBudgets,
  fetchReadBudgetUsage,
  type ReadBudget,
} from '@beecount/api-client'

import type { BudgetUsage } from '../features/BudgetsPanel'

/**
 * 给定 budget 的 start_day,算当前周期的 [start, end)。仅 monthly(其他
 * period 当前 mobile 没真正用,默认 monthly,跟 BudgetsPage.tsx 同算法)。
 *
 * 注:used 计算已下沉到 server SQL,这里保留是因为 BudgetsPage 还要拿 end
 * 算"日均可用 / 剩余天数"等 UI 派生量。
 *
 * 当天 < startDay → 期间是上个月 startDay 到本月 startDay
 * 当天 >= startDay → 期间是本月 startDay 到下个月 startDay
 */
export function currentMonthRange(
  startDay: number,
  now = new Date(),
): { start: Date; end: Date } {
  const day = Math.max(1, Math.min(28, Math.round(startDay || 1)))
  const today = now.getDate()
  let start: Date
  let end: Date
  if (today >= day) {
    start = new Date(now.getFullYear(), now.getMonth(), day, 0, 0, 0, 0)
    end = new Date(now.getFullYear(), now.getMonth() + 1, day, 0, 0, 0, 0)
  } else {
    start = new Date(now.getFullYear(), now.getMonth() - 1, day, 0, 0, 0, 0)
    end = new Date(now.getFullYear(), now.getMonth(), day, 0, 0, 0, 0)
  }
  return { start, end }
}

/** date 所属记账周期的标签 "YYYY-MM"(day >= startDay 归当月,否则上月)。 */
export function periodLabel(date: Date, startDay: number): string {
  const day = Math.max(1, Math.min(28, Math.round(startDay || 1)))
  const d = new Date(date.getFullYear(), date.getMonth() - (date.getDate() < day ? 1 : 0), 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** 当前记账周期的范围短文案,如 "6.15-7.14"(含端);startDay=1 返回 null(自然月不标注)。 */
export function periodRangeText(startDay: number, now = new Date()): string | null {
  const day = Math.max(1, Math.min(28, Math.round(startDay || 1)))
  if (day === 1) return null
  const { start, end } = currentMonthRange(day, now)
  const endIncl = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 1)
  return `${start.getMonth() + 1}.${start.getDate()}-${endIncl.getMonth() + 1}.${endIncl.getDate()}`
}

/** 「year 年」= [当年1月周期起点, 次年1月周期起点),12 个完整记账周期。 */
export function yearRange(year: number, startDay: number): { start: Date; end: Date } {
  const day = Math.max(1, Math.min(28, Math.round(startDay || 1)))
  // 与 server _analytics_range year-scope 同口径:年 = 1月起始日 → 次年1月起始日
  return { start: new Date(year, 0, day), end: new Date(year + 1, 0, day) }
}

export type BudgetsWithUsage = {
  budgets: ReadBudget[]
  usageById: Record<string, BudgetUsage>
}

/**
 * 拉指定账本的 budgets + 每个 budget 当前周期 used。
 *
 * - total budget: 全部 expense 累加(指定 ledger 范围内)
 * - category budget: 关联分类自身 + 所有子分类(parent_sync_id 指向它的)
 *   的 expense 累加 — 父分类预算自动覆盖子分类支出,跟手机端
 *   `local_budget_repository.getBudgetUsage` 同语义
 *
 * 聚合在 server SQL 完成,这里只做两次并发 fetch + join。usage 接口失败时
 * 静默返回空 usage,不阻塞 budgets 渲染(进度条显示 0%)。
 */
export async function fetchBudgetsWithUsage(
  token: string,
  ledgerId: string,
): Promise<BudgetsWithUsage> {
  const [budgets, usageResp] = await Promise.all([
    fetchReadBudgets(token, ledgerId),
    fetchReadBudgetUsage(token, ledgerId).catch(() => ({ items: [] })),
  ])
  const usageById: Record<string, BudgetUsage> = {}
  for (const item of usageResp.items) {
    usageById[item.budget_id] = { used: item.used }
  }
  return { budgets, usageById }
}
