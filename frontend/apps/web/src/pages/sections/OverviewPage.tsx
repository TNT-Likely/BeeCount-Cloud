import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  fetchCounterpartyStats,
  fetchMemberShareStats,
  fetchWorkspaceAccounts,
  fetchWorkspaceAnalytics,
  fetchWorkspaceCategories,
  fetchWorkspaceLedgerCounts,
  fetchWorkspaceTags,
  type CounterpartyStat,
  type MemberShareStat,
  type ReadBudget,
  type WorkspaceAccount,
  type WorkspaceAnalytics,
  type WorkspaceCategory,
  type WorkspaceLedgerCounts,
  type WorkspaceTag,
} from '@beecount/api-client'
import { fetchBudgetsWithUsage, periodLabel, type BudgetUsage } from '@beecount/web-features'

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
  const { activeLedgerId, currentLedger } = useLedgers()

  // Overview 的所有数据按当前账本分桶 —— 切账本时读对应桶,没命中显示空
  // 态后台 refetch。accounts / tags 实体本身是 user-global,但首页 Top 卡片
  // 想看的是「当前账本里活跃的账户/标签」,所以 stats 用 ledger 过滤,
  // 缓存也按账本分桶。资产页/标签页要跨账本时另外不带 ledgerId 拉。
  const bucket = activeLedgerId || '__none__'
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>(`overview:${bucket}:accounts`, [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>(`overview:${bucket}:tags`, [])
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
  // 成员维度(0024):两个 scope 各存一份,切 本月/本年 时不用重新打接口。
  // null = 还没拉到(卡片走空态),[] = 拉到了但确实没数据。
  const [memberStatsByScope, setMemberStatsByScope] = usePageCache<
    Record<'month' | 'year', MemberShareStat[] | null>
  >(`overview:${bucket}:memberStats`, { month: null, year: null })
  // 卡片内的 本月/本年 切换是纯 UI 状态,不进缓存(刷新回落到 year)。
  const [memberScope, setMemberScope] = useState<'month' | 'year'>('year')
  const memberStats = memberStatsByScope[memberScope]
  // 交易对手维度(0023 借款聚合,平帐):三个 scope 各存一份。默认全部(平帐是累计口径)。
  const [counterpartyStatsByScope, setCounterpartyStatsByScope] = usePageCache<
    Record<'month' | 'year' | 'all', CounterpartyStat[] | null>
  >(`overview:${bucket}:counterpartyStats`, { month: null, year: null, all: null })
  const [counterpartyScope, setCounterpartyScope] = useState<'month' | 'year' | 'all'>('all')
  const counterpartyStats = counterpartyStatsByScope[counterpartyScope]

  const loadAccountsAndTags = useCallback(async () => {
    if (!activeLedgerId) return
    try {
      const [a, globalAccounts, tg] = await Promise.all([
        fetchWorkspaceAccounts(token, { ledgerId: activeLedgerId, limit: 500 }),
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceTags(token, { ledgerId: activeLedgerId, limit: 500 }),
      ])
      const balances = new Map(globalAccounts.map((row) => [row.id, row]))
      setAccounts(a.flatMap((row) => {
        const full = balances.get(row.id)
        return full ? [{ ...row, balance: full.balance, currency: full.currency }] : []
      }))
      setTags(tg)
    } catch {
      // dashboard 静默降级
    }
  }, [token, activeLedgerId])

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
    const msd = Math.max(1, Math.min(28, currentLedger?.month_start_day ?? 1))
    const currentPeriod = periodLabel(now, msd)
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
  }, [token, activeLedgerId, currentLedger])

  useEffect(() => {
    void loadAccountsAndTags()
  }, [loadAccountsAndTags])

  // 成员维度统计(0024)—— 单独一个请求,失败时只让成员卡片空态,不拖累
  // 其它卡片(后端没跑 0024 migration 的旧库会 404,这里必须静默降级)。
  const loadMemberStats = useCallback(async () => {
    if (!activeLedgerId) {
      setMemberStatsByScope({ month: null, year: null })
      return
    }
    const now = new Date()
    const msd = Math.max(1, Math.min(28, currentLedger?.month_start_day ?? 1))
    const currentPeriod = periodLabel(now, msd)
    const tzOffsetMinutes = -now.getTimezoneOffset()
    const results = await Promise.allSettled([
      fetchMemberShareStats(token, activeLedgerId, {
        scope: 'month',
        period: currentPeriod,
        tzOffsetMinutes,
      }),
      fetchMemberShareStats(token, activeLedgerId, {
        scope: 'year',
        tzOffsetMinutes,
      }),
    ])
    setMemberStatsByScope({
      month:
        results[0].status === 'fulfilled' ? results[0].value.items || [] : null,
      year: results[1].status === 'fulfilled' ? results[1].value.items || [] : null,
    })
    // setMemberStatsByScope 是 usePageCache 稳定 setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, currentLedger])

  // 交易对手维度(0023 借款聚合,平帐)—— 单独请求,失败只让卡片空态。
  const loadCounterpartyStats = useCallback(async () => {
    if (!activeLedgerId) {
      setCounterpartyStatsByScope({ month: null, year: null, all: null })
      return
    }
    const now = new Date()
    const msd = Math.max(1, Math.min(28, currentLedger?.month_start_day ?? 1))
    const currentPeriod = periodLabel(now, msd)
    const tzOffsetMinutes = -now.getTimezoneOffset()
    const results = await Promise.allSettled([
      fetchCounterpartyStats(token, activeLedgerId, {
        scope: 'month',
        period: currentPeriod,
        tzOffsetMinutes,
      }),
      fetchCounterpartyStats(token, activeLedgerId, {
        scope: 'year',
        tzOffsetMinutes,
      }),
      fetchCounterpartyStats(token, activeLedgerId, {
        scope: 'all',
        tzOffsetMinutes,
      }),
    ])
    setCounterpartyStatsByScope({
      month: results[0].status === 'fulfilled' ? results[0].value.items || [] : null,
      year: results[1].status === 'fulfilled' ? results[1].value.items || [] : null,
      all: results[2].status === 'fulfilled' ? results[2].value.items || [] : null,
    })
    // setCounterpartyStatsByScope 是 usePageCache 稳定 setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, currentLedger])

  useEffect(() => {
    void loadAnalytics()
  }, [loadAnalytics])

  useEffect(() => {
    void loadMemberStats()
  }, [loadMemberStats])

  useEffect(() => {
    void loadCounterpartyStats()
  }, [loadCounterpartyStats])

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
    void loadMemberStats()
    void loadCounterpartyStats()
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
        // 首页 Top 分类卡片 → 默认当前账本,跟 OverviewPage 其它图表口径一致。
        dispatchOpenDetailCategory(cat, { defaultScope: 'current' })
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
      memberStats={memberStats}
      memberScope={memberScope}
      onMemberScopeChange={setMemberScope}
      onManageMembers={() => navigate('/app/members')}
      counterpartyStats={counterpartyStats}
      counterpartyScope={counterpartyScope}
      onCounterpartyScopeChange={setCounterpartyScope}
      onManageCounterparty={() => navigate('/app/loans')}
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
