import { useCallback, useEffect, useMemo } from 'react'

import {
  fetchDividendEvents,
  fetchInvestmentFlow,
  fetchPendingDividends,
  fetchReadRecurringRules,
  fetchRealizedPnl,
  fetchWorkspaceHoldings,
  type DividendEvent,
  type HoldingsSummary,
  type InvestmentFlow,
  type PendingDividend,
  type ReadRecurringRule,
  type RealizedPnlReport,
} from '@beecount/api-client'
import { periodLabel } from '@beecount/web-features'

import { useAuth } from '../../../context/AuthContext'
import { usePageCache } from '../../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../../context/SyncSocketContext'

export type FlowScope = 'month' | 'year' | 'all'

/** 首頁所有股票卡片共用的資料(統一載入一次,各卡片只負責顯示)。 */
export interface HomeStockData {
  /** 有任何投資理財帳戶(含尚無持股的)。沒有時整組股票卡片不顯示。 */
  hasInvestmentAccount: boolean
  holdings: HoldingsSummary | null
  pending: PendingDividend[]
  /** 持有標的近期(過去 45 天起)的除權息事件,含已公告的未來場次。 */
  events: DividendEvent[]
  flow: Record<FlowScope, InvestmentFlow | null>
  realizedYear: RealizedPnlReport | null
  realizedYearNumber: number
  dcaRules: ReadRecurringRule[]
}

const EMPTY_FLOW: Record<FlowScope, InvestmentFlow | null> = { month: null, year: null, all: null }

/** 今天(本地)往前 45 天的 YYYY-MM-DD,除權息卡片只顯示這之後的場次。 */
export function eventsCutoffDate(now = new Date()): string {
  const d = new Date(now.getTime() - 45 * 86400_000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function useHomeStockData(activeLedgerId: string | null, monthStartDay: number): HomeStockData {
  const { token } = useAuth()
  const bucket = activeLedgerId || '__none__'
  const [holdings, setHoldings] = usePageCache<HoldingsSummary | null>('overview:stock:holdings', null)
  const [pending, setPending] = usePageCache<PendingDividend[]>('overview:stock:pending', [])
  const [events, setEvents] = usePageCache<DividendEvent[]>('overview:stock:events', [])
  const [flow, setFlow] = usePageCache<Record<FlowScope, InvestmentFlow | null>>(
    `overview:${bucket}:stockFlow`,
    EMPTY_FLOW,
  )
  const [realizedYear, setRealizedYear] = usePageCache<RealizedPnlReport | null>(
    'overview:stock:realizedYear',
    null,
  )
  const [dcaRules, setDcaRules] = usePageCache<ReadRecurringRule[]>(`overview:${bucket}:stockDca`, [])
  const yearNumber = new Date().getFullYear()

  const load = useCallback(async () => {
    let summary: HoldingsSummary | null = null
    try {
      summary = await fetchWorkspaceHoldings(token, { refresh: false })
      setHoldings(summary)
    } catch {
      setHoldings(null)
      return
    }
    // 沒有投資帳戶就不打其它股票 API(多數使用者根本不會用到)。
    if (!summary || summary.accounts.length === 0) return

    const now = new Date()
    const tzOffsetMinutes = -now.getTimezoneOffset()
    const ledgerId = activeLedgerId || undefined
    const monthPeriod = periodLabel(now, monthStartDay)
    const [rMonth, rYear, rAll, rRealized, rPending, rDca] = await Promise.allSettled([
      fetchInvestmentFlow(token, { scope: 'month', period: monthPeriod, ledgerId, tzOffsetMinutes }),
      fetchInvestmentFlow(token, { scope: 'year', ledgerId, tzOffsetMinutes }),
      fetchInvestmentFlow(token, { scope: 'all', ledgerId, tzOffsetMinutes }),
      fetchRealizedPnl(token, { year: now.getFullYear() }),
      fetchPendingDividends(token, 'pending'),
      activeLedgerId ? fetchReadRecurringRules(token, activeLedgerId) : Promise.resolve([]),
    ])
    setFlow({
      month: rMonth.status === 'fulfilled' ? rMonth.value : null,
      year: rYear.status === 'fulfilled' ? rYear.value : null,
      all: rAll.status === 'fulfilled' ? rAll.value : null,
    })
    setRealizedYear(rRealized.status === 'fulfilled' ? rRealized.value : null)
    setPending(rPending.status === 'fulfilled' ? rPending.value : [])
    setDcaRules(
      rDca.status === 'fulfilled'
        ? (rDca.value as ReadRecurringRule[]).filter((r) => r.kind === 'stock_dca' && r.enabled)
        : [],
    )

    // 除權息事件是逐檔查詢:只查有持股的標的,最多 8 檔,失敗的檔略過。
    const keys = new Map<string, { market: string; symbol: string }>()
    for (const a of summary.accounts) {
      for (const h of a.holdings) {
        if (h.shares > 0) keys.set(`${h.market}:${h.symbol}`, { market: h.market, symbol: h.symbol })
      }
    }
    const cutoff = eventsCutoffDate(now)
    const results = await Promise.allSettled(
      Array.from(keys.values())
        .slice(0, 8)
        .map((k) => fetchDividendEvents(token, k.market, k.symbol)),
    )
    const merged: DividendEvent[] = []
    for (const r of results) {
      if (r.status === 'fulfilled') merged.push(...r.value.filter((e) => e.ex_date >= cutoff))
    }
    setEvents(merged)
    // setter 都是 usePageCache 的穩定 setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, monthStartDay])

  useEffect(() => {
    void load()
  }, [load])
  useSyncRefresh(() => {
    void load()
  })

  return useMemo(
    () => ({
      hasInvestmentAccount: !!holdings && holdings.accounts.length > 0,
      holdings,
      pending,
      events,
      flow,
      realizedYear,
      realizedYearNumber: yearNumber,
      dcaRules,
    }),
    [holdings, pending, events, flow, realizedYear, yearNumber, dcaRules],
  )
}
