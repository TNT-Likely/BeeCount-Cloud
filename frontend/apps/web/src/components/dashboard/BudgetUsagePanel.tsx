import { useMemo, useState } from 'react'
import type { ReadBudget } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import {
  Amount,
  currentMonthRange,
  type BudgetUsage,
} from '@beecount/web-features'

interface Props {
  budgets: ReadBudget[]
  usageById: Record<string, BudgetUsage>
  currency?: string
}

const VISIBLE_CATEGORY_COUNT = 5

/**
 * 首页预算利用率卡片 —— 算法见 .docs/dashboard-anomaly-budget/plan.md §3。
 *
 * 状态:
 *   - 无 budget(用户没设过):整卡片不渲染(返 null)
 *   - 有 total / category budget:总预算 + 分类列表(top 5 + 展开剩余)
 *
 * 颜色阈值跟 mobile `BudgetProgressBar._getColor` / web `BudgetsPanel`
 * 保持一致:
 *   ≥100%  bg-red-700      已超支
 *   ≥90%   bg-red-500      接近超支
 *   ≥70%   bg-orange-500   注意
 *   <70%   bg-green-500    健康
 */
export function BudgetUsagePanel({ budgets, usageById, currency = 'CNY' }: Props) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)

  const { total, categoryBudgets, summary } = useMemo(() => {
    const enabled = budgets.filter((b) => b.enabled)
    const totalRow = enabled.find((b) => b.type === 'total') ?? null
    const catRows = enabled
      .filter((b) => b.type === 'category')
      .map((b) => ({
        budget: b,
        used: usageById[b.id]?.used ?? 0,
        ratio: b.amount > 0 ? (usageById[b.id]?.used ?? 0) / b.amount : 0,
      }))
      .sort((a, b) => b.used - a.used)

    let healthy = 0
    let nearing = 0
    let over = 0
    for (const c of catRows) {
      if (c.ratio >= 1) over++
      else if (c.ratio >= 0.9) nearing++
      else healthy++
    }

    return {
      total: totalRow,
      categoryBudgets: catRows,
      summary: { healthy, nearing, over },
    }
  }, [budgets, usageById])

  // 总预算 + 分类预算都没,整卡不显示(plan §3.3)
  if (total == null && categoryBudgets.length === 0) return null

  const visibleCats = expanded
    ? categoryBudgets
    : categoryBudgets.slice(0, VISIBLE_CATEGORY_COUNT)
  const hiddenCount = categoryBudgets.length - VISIBLE_CATEGORY_COUNT

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-end justify-between gap-2">
        <CardTitle className="text-base">{t('home.budget.title')}</CardTitle>
        {categoryBudgets.length > 0 && (
          <span className="text-[11px] text-muted-foreground">
            {t('home.budget.summaryHealthy').replace('{count}', String(summary.healthy))}
            {' · '}
            {t('home.budget.summaryNearing').replace('{count}', String(summary.nearing))}
            {' · '}
            <span className={summary.over > 0 ? 'text-expense' : ''}>
              {t('home.budget.summaryOver').replace('{count}', String(summary.over))}
            </span>
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {total && (
          <TotalBudgetRow budget={total} used={usageById[total.id]?.used ?? 0} currency={currency} />
        )}

        {categoryBudgets.length > 0 && (
          <div className="space-y-2">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t('home.budget.categoryTitle')} ({categoryBudgets.length})
            </div>
            <ul className="space-y-2">
              {visibleCats.map((c) => (
                <CategoryBudgetRow
                  key={c.budget.id}
                  budget={c.budget}
                  used={c.used}
                />
              ))}
            </ul>
            {hiddenCount > 0 && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                {expanded
                  ? t('home.budget.collapse')
                  : t('home.budget.expandRest').replace('{count}', String(hiddenCount))}
              </button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function thresholdColor(ratio: number): string {
  if (ratio >= 1.0) return 'bg-red-700'
  if (ratio >= 0.9) return 'bg-red-500'
  if (ratio >= 0.7) return 'bg-orange-500'
  return 'bg-green-500'
}

function TotalBudgetRow({
  budget,
  used,
  currency,
}: {
  budget: ReadBudget
  used: number
  currency: string
}) {
  const t = useT()
  const ratio = budget.amount > 0 ? Math.min(used / budget.amount, 1.5) : 0
  const displayRatio = Math.min(ratio, 1) // 进度条最多 100%
  const remaining = budget.amount - used
  const isOver = remaining < 0
  const startDay = Math.max(1, Math.min(28, Number(budget.start_day || 1)))
  const { end } = currentMonthRange(startDay)
  const now = new Date()
  const msPerDay = 1000 * 60 * 60 * 24
  const daysRemaining = Math.max(
    0,
    Math.ceil((end.getTime() - now.getTime()) / msPerDay),
  )
  const dailyAvailable =
    daysRemaining > 0 && !isOver ? remaining / daysRemaining : 0

  const fmtAmount = (v: number) =>
    Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })

  return (
    <div className="rounded-lg border border-border/40 bg-muted/20 px-3 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold">{t('home.budget.totalLabel')}</span>
        <Amount value={budget.amount} currency={currency} size="md" bold />
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted/60">
        <div
          className={`h-full transition-all ${thresholdColor(ratio)}`}
          style={{ width: `${displayRatio * 100}%` }}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span>
          {t('home.budget.used')}{' '}
          <span className="font-mono tabular-nums text-foreground">
            {fmtAmount(used)}
          </span>
          {' '}({Math.round(ratio * 100)}%)
        </span>
        {isOver ? (
          <span className="font-semibold text-expense">
            {t('home.budget.over').replace('{amount}', fmtAmount(remaining))}
          </span>
        ) : (
          <span>
            {t('home.budget.remaining')}{' '}
            <span className="font-mono tabular-nums text-foreground">
              {fmtAmount(remaining)}
            </span>
          </span>
        )}
        <span>{t('home.budget.daysLeft').replace('{days}', String(daysRemaining))}</span>
        {!isOver && dailyAvailable > 0 && (
          <span>
            {t('home.budget.dailyAvailable').replace(
              '{amount}',
              fmtAmount(dailyAvailable),
            )}
          </span>
        )}
      </div>
    </div>
  )
}

function CategoryBudgetRow({
  budget,
  used,
}: {
  budget: ReadBudget
  used: number
}) {
  // 分类预算行不用 Amount(currency 在 panel 头部已经隐含),直接 fmt 裸数字
  // 让 list 视觉更紧凑。如果有用户场景需要混合币种再加。
  const t = useT()
  const ratio = budget.amount > 0 ? used / budget.amount : 0
  const displayRatio = Math.min(ratio, 1)
  const isOver = ratio >= 1
  const fmt = (v: number) =>
    Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })

  return (
    <li className="space-y-1">
      <div className="flex items-baseline justify-between text-sm">
        <span className="inline-flex items-center gap-1.5">
          {isOver && <span aria-hidden>⚠</span>}
          <span className="truncate font-medium">
            {budget.category_name || t('home.topCat.uncategorized')}
          </span>
        </span>
        <span className="inline-flex items-baseline gap-1.5 font-mono tabular-nums">
          <span className="text-sm">
            {fmt(used)} / {fmt(budget.amount)}
          </span>
          <span
            className={`text-[11px] ${
              isOver ? 'font-semibold text-expense' : 'text-muted-foreground'
            }`}
          >
            {Math.round(ratio * 100)}%
          </span>
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/50">
        <div
          className={`h-full transition-all ${thresholdColor(ratio)}`}
          style={{ width: `${displayRatio * 100}%` }}
        />
      </div>
    </li>
  )
}
