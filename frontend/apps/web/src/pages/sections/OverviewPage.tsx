import { useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  fetchWorkspaceAccounts,
  fetchWorkspaceAnalytics,
  fetchWorkspaceLedgerCounts,
  fetchWorkspaceTags,
  type WorkspaceAccount,
  type WorkspaceAnalytics,
  type WorkspaceLedgerCounts,
  type WorkspaceTag,
} from '@beecount/api-client'

import { OverviewSection } from '../../components/sections/OverviewSection'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'

/**
 * 首页 overview 仪表 —— 读多视角 analytics(year/month/all)+ ledgerCounts
 * + accounts(资产构成图)+ tags(Top 标签卡片),全部依当前 activeLedgerId
 * 切换时重拉。点 TopCategories 卡片跳 /app/transactions?q=... 交互在这里。
 *
 * 静默降级:任何 analytics 子请求失败不阻塞其它卡片,dashboard 本身有空态。
 */
export function OverviewPage() {
  const navigate = useNavigate()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()

  // Overview 的所有数据按当前账本分桶 —— 切账本时读对应桶,没命中显示空
  // 态后台 refetch。accounts / tags 是 user-global 不按账本分。
  const bucket = activeLedgerId || '__none__'
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('overview:accounts', [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('overview:tags', [])
  const [analyticsData, setAnalyticsData] = usePageCache<WorkspaceAnalytics | null>(
    `overview:${bucket}:analyticsData`,
    null
  )
  const [analyticsIncomeRanks, setAnalyticsIncomeRanks] = usePageCache<
    WorkspaceAnalytics['category_ranks']
  >(`overview:${bucket}:incomeRanks`, [])
  const [currentMonthSummary, setCurrentMonthSummary] = usePageCache<
    WorkspaceAnalytics['summary'] | null
  >(`overview:${bucket}:monthSummary`, null)
  const [currentMonthSeries, setCurrentMonthSeries] = usePageCache<WorkspaceAnalytics['series']>(
    `overview:${bucket}:monthSeries`,
    []
  )
  const [currentMonthCategoryRanks, setCurrentMonthCategoryRanks] = usePageCache<
    WorkspaceAnalytics['category_ranks']
  >(`overview:${bucket}:monthCategoryRanks`, [])
  const [currentYearSummary, setCurrentYearSummary] = usePageCache<
    WorkspaceAnalytics['summary'] | null
  >(`overview:${bucket}:yearSummary`, null)
  const [currentYearSeries, setCurrentYearSeries] = usePageCache<WorkspaceAnalytics['series']>(
    `overview:${bucket}:yearSeries`,
    []
  )
  const [allTimeSummary, setAllTimeSummary] = usePageCache<WorkspaceAnalytics['summary'] | null>(
    `overview:${bucket}:allSummary`,
    null
  )
  const [allTimeSeries, setAllTimeSeries] = usePageCache<WorkspaceAnalytics['series']>(
    `overview:${bucket}:allSeries`,
    []
  )
  const [ledgerCounts, setLedgerCounts] = usePageCache<WorkspaceLedgerCounts | null>(
    `overview:${bucket}:ledgerCounts`,
    null
  )

  const loadAccountsAndTags = useCallback(async () => {
    try {
      const [a, tg] = await Promise.all([
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceTags(token, { limit: 500 }),
      ])
      setAccounts(a)
      setTags(tg)
    } catch {
      // dashboard 静默降级
    }
  }, [token])

  const loadAnalytics = useCallback(async () => {
    const now = new Date()
    const currentPeriod = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    const tzOffsetMinutes = -now.getTimezoneOffset()
    // allSettled:单个请求失败时其它请求的数据依然 set。
    const results = await Promise.allSettled([
      fetchWorkspaceAnalytics(token, {
        scope: 'year',
        metric: 'expense',
        ledgerId: activeLedgerId || undefined,
        tzOffsetMinutes,
      }),
      fetchWorkspaceAnalytics(token, {
        scope: 'year',
        metric: 'income',
        ledgerId: activeLedgerId || undefined,
        tzOffsetMinutes,
      }),
      fetchWorkspaceAnalytics(token, {
        scope: 'month',
        metric: 'expense',
        period: currentPeriod,
        ledgerId: activeLedgerId || undefined,
        tzOffsetMinutes,
      }),
      fetchWorkspaceAnalytics(token, {
        scope: 'all',
        metric: 'expense',
        ledgerId: activeLedgerId || undefined,
        tzOffsetMinutes,
      }),
      fetchWorkspaceLedgerCounts(token, {
        ledgerId: activeLedgerId || undefined,
      }),
    ])
    const [rYearExpense, rYearIncome, rMonthly, rAll, rCounts] = results
    if (rYearExpense.status === 'fulfilled') {
      setAnalyticsData(rYearExpense.value)
      setCurrentYearSummary(rYearExpense.value.summary)
      setCurrentYearSeries(rYearExpense.value.series || [])
    }
    if (rYearIncome.status === 'fulfilled') {
      setAnalyticsIncomeRanks(rYearIncome.value.category_ranks || [])
    }
    if (rMonthly.status === 'fulfilled') {
      setCurrentMonthSummary(rMonthly.value.summary)
      setCurrentMonthSeries(rMonthly.value.series || [])
      setCurrentMonthCategoryRanks(rMonthly.value.category_ranks || [])
    }
    if (rAll.status === 'fulfilled') {
      setAllTimeSummary(rAll.value.summary)
      setAllTimeSeries(rAll.value.series || [])
    }
    if (rCounts.status === 'fulfilled') {
      setLedgerCounts(rCounts.value)
    } else if (rYearExpense.status === 'fulfilled') {
      // fallback:rCounts 失败时用 analytics summary 凑出 counts
      const s = rYearExpense.value.summary
      setLedgerCounts({
        tx_count: s?.transaction_count ?? 0,
        days_since_first_tx: s?.distinct_days ?? 0,
        distinct_days: s?.distinct_days ?? 0,
        first_tx_at: s?.first_tx_at ?? null,
      })
    }
  }, [token, activeLedgerId])

  useEffect(() => {
    void loadAccountsAndTags()
  }, [loadAccountsAndTags])

  useEffect(() => {
    void loadAnalytics()
  }, [loadAnalytics])

  // mobile 端或其它 tab 写入后 WS / poller 推事件时重拉 analytics + 账户 + 标签。
  useSyncRefresh(() => {
    void loadAnalytics()
    void loadAccountsAndTags()
  })

  return (
    <OverviewSection
      accounts={accounts}
      tags={tags}
      currentMonthSummary={currentMonthSummary}
      currentMonthSeries={currentMonthSeries}
      currentMonthCategoryRanks={currentMonthCategoryRanks}
      currentYearSummary={currentYearSummary}
      currentYearSeries={currentYearSeries}
      allTimeSummary={allTimeSummary}
      allTimeSeries={allTimeSeries}
      analyticsData={analyticsData}
      analyticsIncomeRanks={analyticsIncomeRanks}
      ledgerCounts={ledgerCounts}
      onJumpToTransactionsWithQuery={(q) => {
        // 把关键词(通常是分类名)作为 URL query 传过去,TransactionsPage
        // 在 useState 初始化时会读取 `?q=` 填到 listQuery。
        const suffix = q ? `?q=${encodeURIComponent(q)}` : ''
        navigate(`/app/transactions${suffix}`)
      }}
    />
  )
}
