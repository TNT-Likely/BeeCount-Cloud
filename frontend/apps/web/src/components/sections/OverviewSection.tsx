import type {
  NetWorthHistory,
  ReadBudget,
  WorkspaceAccount,
  WorkspaceAnalytics,
  WorkspaceAnalyticsSeriesItem,
  WorkspaceAnalyticsSummary,
  WorkspaceLedgerCounts,
  WorkspaceTag
} from '@beecount/api-client'
import { useT } from '@beecount/ui'
import type { BudgetUsage } from '@beecount/web-features'

import { useLedgers } from '../../context/LedgersContext'
import {
  dispatchOpenDetailAccount,
  dispatchOpenDetailTag,
} from '../../lib/txDialogEvents'
import { HomeHero } from '../dashboard/HomeHero'
import { HomeHabitStats } from '../dashboard/HomeHabitStats'
import { HomeYearHeatmap } from '../dashboard/HomeYearHeatmap'
import { HomeMonthCategoryDonut } from '../dashboard/HomeMonthCategoryDonut'
import { HomeTopTags } from '../dashboard/HomeTopTags'
import { HomeTopAccounts } from '../dashboard/HomeTopAccounts'
import { AssetCompositionDonut } from '../dashboard/AssetCompositionDonut'
import { MonthlyTrendBars } from '../dashboard/MonthlyTrendBars'
import { NetWorthTrend } from '../dashboard/NetWorthTrend'
import { TopCategoriesList } from '../dashboard/TopCategoriesList'

interface Props {
  accounts: WorkspaceAccount[]
  tags: WorkspaceTag[]
  currentMonthSummary: WorkspaceAnalyticsSummary | null
  currentMonthSeries: WorkspaceAnalyticsSeriesItem[]
  currentMonthCategoryRanks: WorkspaceAnalytics['category_ranks']
  currentYearSummary: WorkspaceAnalyticsSummary | null
  currentYearSeries: WorkspaceAnalyticsSeriesItem[]
  allTimeSummary: WorkspaceAnalyticsSummary | null
  allTimeSeries: WorkspaceAnalyticsSeriesItem[]
  analyticsData: WorkspaceAnalytics | null
  analyticsIncomeRanks: WorkspaceAnalytics['category_ranks']
  /** 回算的每月净资产累积序列(net-worth-history 端点)。null → 净值趋势卡空态。 */
  netWorthHistory: NetWorthHistory | null
  ledgerCounts: WorkspaceLedgerCounts | null
  /** 当前账本预算 + 各 budget 当周期 used。空数组 → BudgetUsagePanel 不显示。 */
  budgets: ReadBudget[]
  budgetUsageById: Record<string, BudgetUsage>
  onJumpToTransactionsWithQuery: (query: string) => void
  /** Top 卡片点击分类名时的钩子 — page 端反查 WorkspaceCategory 后派发详情。
   *  没传则 Top 卡片回退到 onJumpToTransactionsWithQuery。 */
  onCategoryClickFromTop?: (name: string, kind: 'expense' | 'income') => void
}

/**
 * 首页 overview dashboard —— 从 AppPage.tsx 抽出独立组件。
 *
 * 渲染顺序对应 mobile 首页对标 + Web 独有扩展分析:
 *   - HomeHero:核心指标(本月/本年/全期)+ 账本列表 hero
 *   - HomeHabitStats:习惯画像(连续记账天数等)
 *   - [扩展分析分割线]
 *   - HomeMonthCategoryDonut + HomeYearHeatmap 并排
 *   - AssetCompositionDonut + MonthlyTrendBars 并排
 *   - TopCategoriesList(支出 + 收入)并排
 *   - HomeTopTags + HomeTopAccounts 并排
 */
export function OverviewSection({
  accounts,
  tags,
  currentMonthSummary,
  currentMonthSeries,
  currentMonthCategoryRanks,
  currentYearSummary,
  currentYearSeries,
  allTimeSummary,
  allTimeSeries,
  analyticsData,
  analyticsIncomeRanks,
  netWorthHistory,
  ledgerCounts,
  budgets,
  budgetUsageById,
  onJumpToTransactionsWithQuery,
  onCategoryClickFromTop,
}: Props) {
  const t = useT()
  const { ledgers, activeLedgerId, currency } = useLedgers()

  // 预算 + 异常归因被合并进 HomeHero 顶部 chip(hover 出详情),不再独占
  // 卡片占首页空间。月份够算 baseline 的判定跟 server 算法一致(已发生月份 ≥ 3)。
  const yearOccurredMonths = (analyticsData?.series || []).filter(
    (s) => s.expense > 0,
  ).length

  return (
    <div className="space-y-4">
      <HomeHero
        ledgers={ledgers}
        currentLedgerId={activeLedgerId || undefined}
        monthSummary={currentMonthSummary || undefined}
        monthSeries={currentMonthSeries}
        yearSummary={currentYearSummary || undefined}
        yearSeries={currentYearSeries}
        allSummary={allTimeSummary || undefined}
        allSeries={allTimeSeries}
        ledgerCounts={ledgerCounts || undefined}
        budgets={budgets}
        budgetUsageById={budgetUsageById}
        anomalyMonths={analyticsData?.anomaly_months || []}
        hasEnoughMonthsForAnomaly={yearOccurredMonths >= 3}
      />

      <HomeHabitStats
        monthSummary={currentMonthSummary || undefined}
        ledgerCounts={ledgerCounts || undefined}
        currency={currency}
      />

      {/* 扩展分析:Web 端独有的加强仪表,不属于 mobile 首页对标范围 */}
      <div className="flex items-center gap-2 pt-2">
        <span className="h-px flex-1 bg-border/60" aria-hidden />
        <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
          {t('analytics.ext.title')}
        </span>
        <span className="h-px flex-1 bg-border/60" aria-hidden />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <HomeMonthCategoryDonut ranks={currentMonthCategoryRanks} currency={currency} />
        <HomeYearHeatmap yearSeries={currentYearSeries} currency={currency} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <AssetCompositionDonut accounts={accounts} />
        <MonthlyTrendBars data={analyticsData?.series || []} />
      </div>

      <div className="grid gap-4">
        <NetWorthTrend data={netWorthHistory} />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <TopCategoriesList
          ranks={analyticsData?.category_ranks || []}
          variant="expense"
          title={t('analytics.expenseTop5')}
          onClickCategory={
            onCategoryClickFromTop
              ? (name) => onCategoryClickFromTop(name, 'expense')
              : onJumpToTransactionsWithQuery
          }
        />
        <TopCategoriesList
          ranks={analyticsIncomeRanks}
          variant="income"
          title={t('analytics.incomeTop5')}
          onClickCategory={
            onCategoryClickFromTop
              ? (name) => onCategoryClickFromTop(name, 'income')
              : onJumpToTransactionsWithQuery
          }
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <HomeTopTags
          tags={tags}
          currency={currency}
          onSelectTag={(tag) =>
            dispatchOpenDetailTag(tag, { defaultScope: 'current' })
          }
        />
        <HomeTopAccounts
          accounts={accounts}
          currency={currency}
          onSelectAccount={(acc) =>
            dispatchOpenDetailAccount(acc, { defaultScope: 'current' })
          }
        />
      </div>
    </div>
  )
}
