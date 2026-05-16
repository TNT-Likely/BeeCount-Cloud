import {
  fetchReadBudgets,
  fetchWorkspaceTransactions,
  type ReadBudget,
} from '@beecount/api-client'

import type { BudgetUsage } from '../features/BudgetsPanel'

/**
 * 给定 budget 的 start_day,算当前周期的 [start, end)。仅 monthly(其他
 * period 当前 mobile 没真正用,默认 monthly,跟 BudgetsPage.tsx 同算法)。
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

export type BudgetsWithUsage = {
  budgets: ReadBudget[]
  usageById: Record<string, BudgetUsage>
}

/**
 * 拉指定账本的 budgets + 每个 budget 当前周期的 used。
 * - total budget:全部 expense 累加(指定 ledger 范围内)
 * - category budget:按 category_sync_id 过滤
 *
 * 每个 budget 独立 fetch 期内 tx — 跟 mobile repository.getBudgetUsage
 * per-budget 模式一致。budgets 数量通常很少(1 个 total + 几个 category),
 * fetch 数次开销可忽略。
 *
 * 返回:`{ budgets, usageById }`。某个 budget 的 fetch 失败时 used=0,不
 * 阻塞其它。整体调用失败抛错给 caller 处理。
 */
export async function fetchBudgetsWithUsage(
  token: string,
  ledgerId: string,
): Promise<BudgetsWithUsage> {
  const budgets = await fetchReadBudgets(token, ledgerId)
  if (budgets.length === 0) {
    return { budgets, usageById: {} }
  }
  const usageById: Record<string, BudgetUsage> = {}
  await Promise.all(
    budgets.map(async (b) => {
      try {
        const startDay = Math.max(1, Math.min(28, Number(b.start_day || 1)))
        const { start, end } = currentMonthRange(startDay)
        const categorySyncId =
          b.type === 'category' ? b.category_id || undefined : undefined
        const page = await fetchWorkspaceTransactions(token, {
          ledgerId,
          txType: 'expense',
          categorySyncId,
          dateFrom: start.toISOString(),
          dateTo: end.toISOString(),
          limit: 1000,
        })
        const used = page.items.reduce(
          (acc, tx) => acc + Math.abs(Number(tx.amount || 0)),
          0,
        )
        usageById[b.id] = { used }
      } catch {
        usageById[b.id] = { used: 0 }
      }
    }),
  )
  return { budgets, usageById }
}
