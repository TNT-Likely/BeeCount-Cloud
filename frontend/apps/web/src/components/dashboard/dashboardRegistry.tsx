import type { ReactNode } from 'react'

import type {
  ReadBudget,
  ReadLedger,
  WorkspaceAccount,
  WorkspaceAnalytics,
  WorkspaceAnalyticsSeriesItem,
  WorkspaceAnalyticsSummary,
  WorkspaceLedgerCounts,
  WorkspaceTag,
} from '@beecount/api-client'
import type { BudgetUsage } from '@beecount/web-features'

import { dispatchOpenDetailAccount, dispatchOpenDetailTag } from '../../lib/txDialogEvents'
import { AssetCompositionDonut } from './AssetCompositionDonut'
import { ComparisonReportCard } from './ComparisonReportCard'
import { HomeHabitStats } from './HomeHabitStats'
import { HomeHero } from './HomeHero'
import { HomeMonthCategoryDonut } from './HomeMonthCategoryDonut'
import { HomeTopAccounts } from './HomeTopAccounts'
import { HomeTopTags } from './HomeTopTags'
import { HomeYearHeatmap } from './HomeYearHeatmap'
import { InvestmentValueCard } from './InvestmentValueCard'
import { MonthlyTrendBars } from './MonthlyTrendBars'
import { TopCategoriesList } from './TopCategoriesList'
import {
  AllocationCard,
  DcaCard,
  DividendsCard,
  MonthFlowCard,
  RealizedYearCard,
  TodayChangeCard,
  TopHoldingsCard,
} from './stock/StockCards'
import { describeNetInvested, netInvestedRows } from './stock/flowUtil'
import type { HomeStockData } from './stock/useHomeStockData'
import { formatStockMoney } from '@beecount/web-features'

/** 渲染卡片需要的全部資料 / 行為。OverviewSection 組好後傳給每張卡片的 render。 */
export interface HomeCardContext {
  t: (key: string, params?: Record<string, string | number>) => string
  ledgers: ReadLedger[]
  activeLedgerId: string | null
  currency: string
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
  ledgerCounts: WorkspaceLedgerCounts | null
  budgets: ReadBudget[]
  budgetUsageById: Record<string, BudgetUsage>
  yearOccurredMonths: number
  stock: HomeStockData
  onJumpToTransactionsWithQuery: (query: string) => void
  onCategoryClickFromTop?: (name: string, kind: 'expense' | 'income') => void
  onOpenInvestments: () => void
}

export type HomeCardSection = 'summary' | 'stock' | 'analysis'

export interface HomeCardDef {
  /** 穩定 id,會存到 server;改名 = 對所有使用者「刪舊增新」,不要改。 */
  id: string
  /** i18n key:卡片標題(編輯模式、可新增清單用)。 */
  titleKey: string
  section: HomeCardSection
  /** 真 = 佔滿一整列;否則半寬(lg 以上兩欄)。 */
  full?: boolean
  defaultVisible: boolean
  /** 真 = 沒有任何投資理財帳戶時整張卡不顯示(也不出現在「可新增」清單)。 */
  requiresInvestment?: boolean
  render: (ctx: HomeCardContext) => ReactNode
}

/**
 * 首頁卡片註冊表。**陣列順序 = 預設順序**。新增卡片:加一筆(不要改既有 id),
 * 老使用者的已存版面會依這裡的預設位置自動帶入新卡片(見 lib/dashboardLayout.ts)。
 */
export const HOME_CARDS: HomeCardDef[] = [
  {
    id: 'hero',
    titleKey: 'home.card.hero',
    section: 'summary',
    full: true,
    defaultVisible: true,
    render: (c) => (
      <HomeHero
        ledgers={c.ledgers}
        currentLedgerId={c.activeLedgerId || undefined}
        monthSummary={c.currentMonthSummary || undefined}
        monthSeries={c.currentMonthSeries}
        yearSummary={c.currentYearSummary || undefined}
        yearSeries={c.currentYearSeries}
        allSummary={c.allTimeSummary || undefined}
        allSeries={c.allTimeSeries}
        ledgerCounts={c.ledgerCounts || undefined}
        budgets={c.budgets}
        budgetUsageById={c.budgetUsageById}
        anomalyMonths={c.analyticsData?.anomaly_months || []}
        hasEnoughMonthsForAnomaly={c.yearOccurredMonths >= 3}
        investmentFlow={c.stock.hasInvestmentAccount ? c.stock.flow : undefined}
        onOpenInvestments={c.onOpenInvestments}
      />
    ),
  },
  {
    id: 'habit',
    titleKey: 'home.card.habit',
    section: 'summary',
    full: true,
    defaultVisible: true,
    render: (c) => {
      const rows = netInvestedRows(c.stock.flow.month)
      const investedNote =
        c.stock.hasInvestmentAccount && rows.length > 0
          ? describeNetInvested(rows, formatStockMoney, {
              out: (amount) => c.t('home.habit.savingRate.investOut', { amount }),
              in: (amount) => c.t('home.habit.savingRate.investIn', { amount })
            }).join(' · ')
          : undefined
      return (
        <HomeHabitStats
          monthSummary={c.currentMonthSummary || undefined}
          ledgerCounts={c.ledgerCounts || undefined}
          currency={c.currency}
          investedNote={investedNote}
        />
      )
    },
  },
  {
    id: 'stock.value',
    titleKey: 'home.card.stockValue',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => (
      <InvestmentValueCard summary={c.stock.holdings} pendingCount={c.stock.pending.length} className="h-full" />
    ),
  },
  {
    id: 'stock.today',
    titleKey: 'home.card.stockToday',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <TodayChangeCard data={c.stock} />,
  },
  {
    id: 'stock.flow',
    titleKey: 'home.card.stockFlow',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <MonthFlowCard data={c.stock} />,
  },
  {
    id: 'stock.realized',
    titleKey: 'home.card.stockRealized',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <RealizedYearCard data={c.stock} />,
  },
  {
    id: 'stock.top',
    titleKey: 'home.card.stockTop',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <TopHoldingsCard data={c.stock} />,
  },
  {
    id: 'stock.alloc',
    titleKey: 'home.card.stockAlloc',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <AllocationCard data={c.stock} />,
  },
  {
    id: 'stock.dividends',
    titleKey: 'home.card.stockDividends',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <DividendsCard data={c.stock} />,
  },
  {
    id: 'stock.dca',
    titleKey: 'home.card.stockDca',
    section: 'stock',
    requiresInvestment: true,
    defaultVisible: true,
    render: (c) => <DcaCard data={c.stock} />,
  },
  {
    id: 'monthDonut',
    titleKey: 'home.card.monthDonut',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => <HomeMonthCategoryDonut ranks={c.currentMonthCategoryRanks} currency={c.currency} />,
  },
  {
    id: 'yearHeatmap',
    titleKey: 'home.card.yearHeatmap',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => <HomeYearHeatmap yearSeries={c.currentYearSeries} currency={c.currency} />,
  },
  {
    id: 'assetDonut',
    titleKey: 'home.card.assetDonut',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => <AssetCompositionDonut accounts={c.accounts} />,
  },
  {
    id: 'trend',
    titleKey: 'home.card.trend',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => <MonthlyTrendBars data={c.analyticsData?.series || []} />,
  },
  {
    id: 'topExpense',
    titleKey: 'home.card.topExpense',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => (
      <TopCategoriesList
        ranks={c.analyticsData?.category_ranks || []}
        variant="expense"
        title={c.t('analytics.expenseTop5')}
        onClickCategory={
          c.onCategoryClickFromTop
            ? (name) => c.onCategoryClickFromTop?.(name, 'expense')
            : c.onJumpToTransactionsWithQuery
        }
      />
    ),
  },
  {
    id: 'topIncome',
    titleKey: 'home.card.topIncome',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => (
      <TopCategoriesList
        ranks={c.analyticsIncomeRanks}
        variant="income"
        title={c.t('analytics.incomeTop5')}
        onClickCategory={
          c.onCategoryClickFromTop
            ? (name) => c.onCategoryClickFromTop?.(name, 'income')
            : c.onJumpToTransactionsWithQuery
        }
      />
    ),
  },
  {
    id: 'topTags',
    titleKey: 'home.card.topTags',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => (
      <HomeTopTags
        tags={c.tags}
        currency={c.currency}
        onSelectTag={(tag) => dispatchOpenDetailTag(tag, { defaultScope: 'current' })}
      />
    ),
  },
  {
    id: 'topAccounts',
    titleKey: 'home.card.topAccounts',
    section: 'analysis',
    defaultVisible: true,
    render: (c) => (
      <HomeTopAccounts
        accounts={c.accounts}
        currency={c.currency}
        onSelectAccount={(acc) => dispatchOpenDetailAccount(acc, { defaultScope: 'current' })}
      />
    ),
  },
  {
    id: 'comparison',
    titleKey: 'home.card.comparison',
    section: 'analysis',
    full: true,
    defaultVisible: true,
    render: () => <ComparisonReportCard />,
  },
]

export const HOME_CARD_BY_ID = new Map(HOME_CARDS.map((c) => [c.id, c]))
