import { useMemo, useState } from 'react'
import { Plus, RotateCcw, Settings2 } from 'lucide-react'

import type {
  ReadBudget,
  WorkspaceAccount,
  WorkspaceAnalytics,
  WorkspaceAnalyticsSeriesItem,
  WorkspaceAnalyticsSummary,
  WorkspaceLedgerCounts,
  WorkspaceTag
} from '@beecount/api-client'
import { Button, useT } from '@beecount/ui'
import { SortableCardGrid, type BudgetUsage } from '@beecount/web-features'

import { useLedgers } from '../../context/LedgersContext'
import { isDefaultLayout } from '../../lib/dashboardLayout'
import { useDashboardLayout } from '../../lib/useDashboardLayout'
import {
  HOME_CARDS,
  HOME_CARD_BY_ID,
  type HomeCardContext,
  type HomeCardSection
} from '../dashboard/dashboardRegistry'
import type { HomeStockData } from '../dashboard/stock/useHomeStockData'

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
  ledgerCounts: WorkspaceLedgerCounts | null
  /** 当前账本预算 + 各 budget 当周期 used。空数组 → BudgetUsagePanel 不显示。 */
  budgets: ReadBudget[]
  budgetUsageById: Record<string, BudgetUsage>
  onJumpToTransactionsWithQuery: (query: string) => void
  /** Top 卡片点击分类名时的钩子 — page 端反查 WorkspaceCategory 后派发详情。
   *  没传则 Top 卡片回退到 onJumpToTransactionsWithQuery。 */
  onCategoryClickFromTop?: (name: string, kind: 'expense' | 'income') => void
  /** 股票卡片 + 結餘旁「投資淨投入」共用的資料(useHomeStockData)。 */
  stock: HomeStockData
  onOpenInvestments: () => void
}

const SECTION_ORDER: HomeCardSection[] = ['summary', 'stock', 'analysis']

/**
 * 首页 overview dashboard —— 卡片版面由 `dashboardRegistry.tsx`(卡片註冊表)
 * + 使用者自訂版面(`useDashboardLayout`,存 server、跨裝置同步)決定:
 * 顯示/隱藏、拖曳排序、還原預設。編輯模式用 `SortableCardGrid`。
 * 沒有任何投資理財帳戶時,`requiresInvestment` 的股票卡片整組不顯示。
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
  ledgerCounts,
  budgets,
  budgetUsageById,
  onJumpToTransactionsWithQuery,
  onCategoryClickFromTop,
  stock,
  onOpenInvestments
}: Props) {
  const t = useT()
  const { ledgers, activeLedgerId, currency } = useLedgers()
  const [editing, setEditing] = useState(false)
  const layout = useDashboardLayout(HOME_CARDS, editing)

  // 预算 + 异常归因被合并进 HomeHero 顶部 chip(hover 出详情),不再独占
  // 卡片占首页空间。月份够算 baseline 的判定跟 server 算法一致(已发生月份 ≥ 3)。
  const yearOccurredMonths = (analyticsData?.series || []).filter(
    (s) => s.expense > 0,
  ).length

  const ctx: HomeCardContext = {
    t,
    ledgers,
    activeLedgerId,
    currency,
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
    ledgerCounts,
    budgets,
    budgetUsageById,
    yearOccurredMonths,
    stock,
    onJumpToTransactionsWithQuery,
    onCategoryClickFromTop,
    onOpenInvestments
  }

  const available = (id: string) => {
    const def = HOME_CARD_BY_ID.get(id)
    return !!def && (!def.requiresInvestment || stock.hasInvestmentAccount)
  }
  const shown = useMemo(
    () => layout.cards.filter((c) => c.visible && available(c.id)),
    // available 只依賴 stock.hasInvestmentAccount
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layout.cards, stock.hasInvestmentAccount]
  )
  const hidden = useMemo(
    () => layout.cards.filter((c) => !c.visible && available(c.id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layout.cards, stock.hasInvestmentAccount]
  )
  const atDefault = isDefaultLayout(HOME_CARDS, layout.cards)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {editing && (
          <span className="mr-auto text-xs text-muted-foreground">{t('home.layout.hint')}</span>
        )}
        {editing && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={atDefault}
            onClick={layout.reset}
            data-testid="dash-reset"
          >
            <RotateCcw className="mr-1 h-3.5 w-3.5" />
            {t('home.layout.reset')}
          </Button>
        )}
        <Button
          type="button"
          variant={editing ? 'default' : 'outline'}
          size="sm"
          onClick={() => setEditing((v) => !v)}
          data-testid="dash-customize"
        >
          <Settings2 className="mr-1 h-3.5 w-3.5" />
          {editing ? t('home.layout.done') : t('home.layout.customize')}
        </Button>
      </div>

      {editing && (
        <div className="rounded-2xl border border-dashed border-primary/50 bg-card/60 p-3" data-testid="dash-addable">
          <div className="mb-2 text-sm font-medium">{t('home.layout.addable')}</div>
          {hidden.length === 0 ? (
            <div className="text-xs text-muted-foreground">{t('home.layout.addable.empty')}</div>
          ) : (
            <div className="space-y-2">
              {SECTION_ORDER.map((section) => {
                const items = hidden.filter((c) => HOME_CARD_BY_ID.get(c.id)?.section === section)
                if (items.length === 0) return null
                return (
                  <div key={section} className="flex flex-wrap items-center gap-2">
                    <span className="w-14 shrink-0 text-[11px] text-muted-foreground">
                      {t(`home.layout.section.${section}`)}
                    </span>
                    {items.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        data-testid={`dash-add-${c.id}`}
                        onClick={() => layout.show(c.id)}
                        className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-background px-3 py-1 text-xs hover:bg-accent"
                      >
                        <Plus className="h-3 w-3" />
                        {t(HOME_CARD_BY_ID.get(c.id)!.titleKey)}
                      </button>
                    ))}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {editing ? (
        <SortableCardGrid
          items={shown.map((c) => ({
            id: c.id,
            full: HOME_CARD_BY_ID.get(c.id)?.full,
            label: t(HOME_CARD_BY_ID.get(c.id)!.titleKey)
          }))}
          onMove={layout.move}
          onHide={layout.hide}
          renderCard={(id) => HOME_CARD_BY_ID.get(id)?.render(ctx)}
          dragLabel={t('home.layout.drag')}
          hideLabel={t('home.layout.hide')}
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2" data-testid="dash-grid">
          {shown.map((c) => {
            const def = HOME_CARD_BY_ID.get(c.id)!
            return (
              <div
                key={c.id}
                data-card-id={c.id}
                className={`min-w-0 ${def.full ? 'lg:col-span-2' : '[&>*]:h-full'}`}
              >
                {def.render(ctx)}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
