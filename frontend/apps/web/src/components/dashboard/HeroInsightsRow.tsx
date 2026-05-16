import { Wallet, AlertTriangle, CheckCircle2 } from 'lucide-react'
import type {
  ReadBudget,
  WorkspaceAnalyticsAnomalyMonth,
} from '@beecount/api-client'
import { useT } from '@beecount/ui'
import type { BudgetUsage } from '@beecount/web-features'

import {
  AnomalyMonthsBody,
} from './AnomalyMonthsAttribution'
import {
  BudgetUsageBody,
  useBudgetUsageViewModel,
} from './BudgetUsagePanel'

interface Props {
  budgets: ReadBudget[]
  budgetUsageById: Record<string, BudgetUsage>
  anomalyMonths: WorkspaceAnalyticsAnomalyMonth[]
  hasEnoughMonths: boolean
  currency?: string
}

/**
 * Hero 卡片内的小型 chip row(预算 + 异常归因)。chip 显示关键数字 + 状态;
 * hover 浮出详细 popover(BudgetUsageBody / AnomalyMonthsBody 复用)。
 *
 * 设计动机:之前两个独立 panel 太占首页空间,用户视觉一眼能看到的"本月情况"
 * 应该在 hero 内部一行带过,详情按需展开。
 *
 * popover:CSS-only(group-hover),不依赖 portal。HomeHero 的最外 wrapper
 * 用 `overflow-visible` 配合,popover 才不会被裁剪 — 装饰光斑挪到内部
 * `overflow-hidden` 子层去 clip。
 */
export function HeroInsightsRow({
  budgets,
  budgetUsageById,
  anomalyMonths,
  hasEnoughMonths,
  currency = 'CNY',
}: Props) {
  const t = useT()
  const budgetVM = useBudgetUsageViewModel(budgets, budgetUsageById)
  const anomalyCount = anomalyMonths.length

  const showBudget = !budgetVM.isEmpty
  // 异常 chip 显示规则:有异常 → 红色;没异常但月份够 → 绿色 ✓;月份不足 → 不显示
  const showAnomaly = anomalyCount > 0 || hasEnoughMonths

  if (!showBudget && !showAnomaly) return null

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {showBudget && (
        <BudgetChip
          ratio={budgetVM.totalRatio}
          hasTotal={budgetVM.total != null}
          categoryOverCount={budgetVM.summary.over}
          categoryCount={budgetVM.categoryBudgets.length}
        >
          <div className="w-[min(420px,calc(100vw-3rem))] max-h-[70vh] overflow-y-auto p-3">
            <BudgetUsageBody
              viewModel={budgetVM}
              usageById={budgetUsageById}
              currency={currency}
            />
          </div>
        </BudgetChip>
      )}

      {showAnomaly && (
        <AnomalyChip count={anomalyCount}>
          <div className="w-[min(420px,calc(100vw-3rem))] max-h-[70vh] overflow-y-auto p-3">
            <AnomalyMonthsBody
              anomalyMonths={anomalyMonths}
              hasEnoughMonths={hasEnoughMonths}
              currency={currency}
            />
          </div>
        </AnomalyChip>
      )}

      <span className="text-[10px] text-muted-foreground/70">
        {t('home.insights.hint')}
      </span>
    </div>
  )
}

function chipBaseClass(active: boolean) {
  return [
    'group relative inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1',
    'text-[11px] font-medium transition-colors cursor-default select-none',
    active
      ? 'border-expense/40 bg-expense/10 text-expense hover:bg-expense/15'
      : 'border-border/60 bg-background/70 text-foreground hover:bg-accent/40',
  ].join(' ')
}

function popoverClass() {
  // CSS-only popover:group-hover 触发,absolute 定位,hover 时 z-50 抢到
  // 并排 sparkline / 其它装饰之上。bottom-full 向上展开避免 hero 底部
  // 紧贴下面的卡片被遮。
  return [
    'pointer-events-none absolute bottom-full left-0 z-50 mb-2 hidden opacity-0',
    'rounded-xl border border-border/60 bg-popover text-popover-foreground shadow-xl',
    'transition-opacity duration-150',
    'group-hover:flex group-hover:opacity-100 group-focus-within:flex group-focus-within:opacity-100',
  ].join(' ')
}

function BudgetChip({
  ratio,
  hasTotal,
  categoryOverCount,
  categoryCount,
  children,
}: {
  ratio: number
  hasTotal: boolean
  categoryOverCount: number
  categoryCount: number
  children: React.ReactNode
}) {
  const t = useT()
  // 总预算无 → 用 category 状态决定 chip 显示;有 total → ratio 主导
  const totalOver = hasTotal && ratio >= 1
  const totalNear = hasTotal && ratio >= 0.9 && ratio < 1
  const isAlert = totalOver || categoryOverCount > 0
  const pct = hasTotal ? `${Math.round(ratio * 100)}%` : null

  return (
    <span className={chipBaseClass(isAlert)} tabIndex={0}>
      <Wallet className="h-3.5 w-3.5" aria-hidden />
      <span>
        {hasTotal
          ? t('home.insights.budgetChipTotal').replace('{pct}', pct ?? '0%')
          : t('home.insights.budgetChipCatOnly').replace(
              '{count}',
              String(categoryCount),
            )}
      </span>
      {categoryOverCount > 0 && (
        <span className="ml-0.5 inline-flex items-center gap-0.5 rounded-full bg-expense/20 px-1.5 py-px text-[10px] text-expense">
          ⚠ {categoryOverCount}
        </span>
      )}
      {totalNear && !categoryOverCount && (
        <span className="ml-0.5 text-[10px] text-orange-500">●</span>
      )}
      <div className={popoverClass()}>{children}</div>
    </span>
  )
}

function AnomalyChip({
  count,
  children,
}: {
  count: number
  children: React.ReactNode
}) {
  const t = useT()
  const has = count > 0

  return (
    <span className={chipBaseClass(has)} tabIndex={0}>
      {has ? (
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
      ) : (
        <CheckCircle2 className="h-3.5 w-3.5 text-green-600" aria-hidden />
      )}
      <span>
        {has
          ? t('home.insights.anomalyChip').replace('{count}', String(count))
          : t('home.insights.anomalyChipClean')}
      </span>
      <div className={popoverClass()}>{children}</div>
    </span>
  )
}
