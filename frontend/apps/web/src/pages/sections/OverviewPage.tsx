import { useCallback, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  fetchWorkspaceAccounts,
  fetchWorkspaceAnalytics,
  fetchWorkspaceCategories,
  fetchWorkspaceLedgerCounts,
  fetchWorkspaceTags,
  type ReadBudget,
  type WorkspaceAccount,
  type WorkspaceAnalytics,
  type WorkspaceCategory,
  type WorkspaceLedgerCounts,
  type WorkspaceTag,
} from '@beecount/api-client'
import { fetchBudgetsWithUsage, type BudgetUsage } from '@beecount/web-features'

import { OverviewSection } from '../../components/sections/OverviewSection'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { setAppBadge } from '../../lib/pwa-badge'
import { dispatchOpenDetailCategory } from '../../lib/txDialogEvents'

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
  // 当前账本下的全部分类(用于把 TopCategoriesList 里的 category_name 反查
  // 成完整 WorkspaceCategory,从而打开富统计详情弹窗)。activeLedgerId 变了
  // 重拉,避免错按本来不在当前账本的分类查 stats。
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>(
    `overview:${bucket}:categories`,
    [],
  )
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
  const [budgets, setBudgets] = usePageCache<ReadBudget[]>(
    `overview:${bucket}:budgets`,
    []
  )
  const [budgetUsageById, setBudgetUsageById] = usePageCache<
    Record<string, BudgetUsage>
  >(`overview:${bucket}:budgetUsage`, {})

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

  const loadCategories = useCallback(async () => {
    if (!activeLedgerId) {
      setCategories([])
      return
    }
    try {
      const rows = await fetchWorkspaceCategories(token, {
        ledgerId: activeLedgerId,
        limit: 500,
      })
      setCategories(rows)
    } catch {
      // 静默降级:Top 卡片点击拿不到 detail 时回退到 jump-to-tx-page,
      // 跟没装这个功能等价。
      setCategories([])
    }
    // setCategories 是 usePageCache 稳定 setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId])

  const loadBudgets = useCallback(async () => {
    if (!activeLedgerId) {
      setBudgets([])
      setBudgetUsageById({})
      return
    }
    try {
      const { budgets: b, usageById } = await fetchBudgetsWithUsage(
        token,
        activeLedgerId,
      )
      setBudgets(b)
      setBudgetUsageById(usageById)
    } catch {
      // 静默降级 — 预算面板空时整卡片不显示,失败也走同分支
      setBudgets([])
      setBudgetUsageById({})
    }
    // setBudgets / setBudgetUsageById 是 usePageCache 返回的稳定 setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId])

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

  useEffect(() => {
    void loadBudgets()
  }, [loadBudgets])

  useEffect(() => {
    void loadCategories()
  }, [loadCategories])

  // mobile 端或其它 tab 写入后 WS / poller 推事件时重拉 analytics + 账户 +
  // 标签 + 预算。budget 跟随 tx 变化(used 是 tx 累加)。
  useSyncRefresh(() => {
    void loadAnalytics()
    void loadAccountsAndTags()
    void loadBudgets()
    void loadCategories()
  })

  // 把 Top 卡片里只有 name 的点击事件反查成 WorkspaceCategory 后弹详情;
  // 反查不到(分类被删 / name 是 "Uncategorized" / 异步 race)兜底跳交易页。
  const onCategoryClickFromHome = useCallback(
    (name: string, kind: 'expense' | 'income') => {
      const trimmed = (name || '').trim()
      if (!trimmed) {
        navigate(`/app/transactions`)
        return
      }
      const cat = categories.find(
        (c) =>
          (c.name || '').trim().toLowerCase() === trimmed.toLowerCase() &&
          (c.kind || '').toLowerCase() === kind,
      )
      if (cat) {
        dispatchOpenDetailCategory(cat)
        return
      }
      const params = new URLSearchParams({ q: trimmed })
      if (activeLedgerId) params.set('ledger', activeLedgerId)
      navigate(`/app/transactions?${params.toString()}`)
    },
    [categories, navigate, activeLedgerId],
  )

  // PWA dock badge:统计本月超支预算条数,写到 navigator.setAppBadge。
  // 用户安装 PWA 后,即使应用没在前台,dock 图标也会显示一个小红点(数字),
  // 提醒「有预算超了」。浏览器不支持 Badging API 时静默 no-op。
  const overBudgetCount = useMemo(() => {
    if (!budgets || budgets.length === 0) return 0
    let count = 0
    for (const b of budgets) {
      if (!b.enabled) continue
      const used = budgetUsageById[b.id]?.used ?? 0
      if (used > b.amount) count += 1
    }
    return count
  }, [budgets, budgetUsageById])

  useEffect(() => {
    // setAppBadge 内部已处理不支持/失败,这里 fire-and-forget 即可
    void setAppBadge(overBudgetCount)
  }, [overBudgetCount])

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
      budgets={budgets}
      budgetUsageById={budgetUsageById}
      onJumpToTransactionsWithQuery={(q) => {
        // 把关键词(通常是分类名)作为 URL query 传过去,TransactionsPage
        // 在 useState 初始化时会读取 `?q=` 填到 listQuery。
        const suffix = q ? `?q=${encodeURIComponent(q)}` : ''
        navigate(`/app/transactions${suffix}`)
      }}
      onCategoryClickFromTop={onCategoryClickFromHome}
    />
  )
}
