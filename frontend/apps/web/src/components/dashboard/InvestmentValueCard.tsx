import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { fetchPendingDividends, fetchWorkspaceHoldings, type HoldingsSummary } from '@beecount/api-client'
import { Card, CardContent, useT } from '@beecount/ui'
import { formatPercent, formatStockMoney } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { routePath } from '../../state/router'

/**
 * 資產頁「投資市值(預估)」卡(股票持股 2026-09-28)。股票不計入淨資產(使用者
 * 確認的需求),這張卡跟淨資產卡分開、口徑同樣是「折算成主幣別,缺匯率剔除
 * 並標示」。沒有任何未平倉部位時不渲染。點擊進投資頁。
 *
 * 這裡用 refresh=false 只讀 server 報價快取,避免資產頁每次進來都等上游
 * 報價;投資頁本身才會觸發補抓。有待確認股利(Phase 2)時多一行提示。
 */
export function InvestmentValueCard() {
  const t = useT()
  const navigate = useNavigate()
  const { token } = useAuth()
  const [summary, setSummary] = usePageCache<HoldingsSummary | null>('accounts:investmentSummary', null)
  const [pendingCount, setPendingCount] = useState(0)

  const load = useCallback(async () => {
    try {
      setSummary(await fetchWorkspaceHoldings(token, { refresh: false }))
    } catch {
      setSummary(null)
    }
    try {
      setPendingCount((await fetchPendingDividends(token, 'pending')).length)
    } catch {
      setPendingCount(0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    void load()
  }, [load])
  useSyncRefresh(() => {
    void load()
  })

  if (!summary || !summary.accounts.some((a) => a.holdings.some((h) => h.shares > 0))) return null
  const base = summary.base_currency || ''
  const pnl = summary.total_unrealized_pnl
  const pct = summary.total_cost > 0 ? (pnl / summary.total_cost) * 100 : null

  return (
    <Card
      className="bc-panel mb-4 cursor-pointer transition-colors hover:bg-accent/30"
      onClick={() => navigate(routePath({ kind: 'app', ledgerId: '', section: 'investments' }))}
    >
      <CardContent className="flex flex-wrap items-end justify-between gap-3 p-5">
        <div className="min-w-0 space-y-1">
          <p className="text-xs text-muted-foreground">{t('investments.card.title')}</p>
          <div className="text-2xl font-semibold tabular-nums">
            {base ? formatStockMoney(summary.total_market_value, base) : '—'}
          </div>
          <div className={`text-sm tabular-nums ${pnl > 0 ? 'text-income' : pnl < 0 ? 'text-expense' : 'text-muted-foreground'}`}>
            {t('investments.unrealized')} {base ? formatStockMoney(pnl, base, { signed: true }) : '—'}
            {pct !== null ? ` (${formatPercent(pct)})` : ''}
          </div>
          {summary.pnl_after_sell_costs && (
            <p className="text-[11px] text-muted-foreground">{t('investments.pnlAfterSellCostsNote')}</p>
          )}
          {summary.missing_rates.length > 0 && (
            <p className="text-[11px] text-amber-600 dark:text-amber-500">
              {t('investments.missingRates', { currencies: summary.missing_rates.join(', ') })}
            </p>
          )}
          {pendingCount > 0 && (
            <p className="text-xs font-medium text-primary">
              {t('investments.dividend.pendingBadge', { count: pendingCount })}
            </p>
          )}
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div className="tabular-nums">
            {t('investments.cost')} {base ? formatStockMoney(summary.total_cost, base) : '—'}
          </div>
          <div>{t('investments.card.hint')} ›</div>
        </div>
      </CardContent>
    </Card>
  )
}
