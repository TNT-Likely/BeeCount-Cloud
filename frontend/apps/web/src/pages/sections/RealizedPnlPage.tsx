import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  fetchRealizedPnl,
  fetchWorkspaceAccounts,
  type RealizedPnlReport,
  type RealizedPnlSymbol,
  type WorkspaceAccount,
} from '@beecount/api-client'
import { Button, Card, CardContent, CardHeader, CardTitle, Input, useT } from '@beecount/ui'
import { formatShares, formatStockMoney } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { routePath } from '../../state/router'
import { isoToDateValue, pnlClass } from './investmentsShared'

/**
 * 已實現損益報表(Phase 3,docs/STOCK_HOLDINGS_SD.md §11)。
 * 資料全部來自 server `/read/workspace/realized-pnl`(移動平均成本法),各幣別分開,
 * 這裡不做任何跨幣別加總。損益顏色沿用投資頁的 `pnlClass`(text-income / text-expense)。
 */

const SYMBOL_DEBOUNCE_MS = 400

function symbolRowKey(s: RealizedPnlSymbol): string {
  return `${s.market}:${s.symbol}`
}

export function RealizedPnlPage() {
  const t = useT()
  const navigate = useNavigate()
  const { token } = useAuth()

  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('realized-pnl:accounts', [])
  const [year, setYear] = useState<number | null>(null)
  const [accountId, setAccountId] = useState('')
  const [symbolInput, setSymbolInput] = useState('')
  const [symbolQuery, setSymbolQuery] = useState('')
  const [report, setReport] = useState<RealizedPnlReport | null>(null)
  // 年度選單的選項不隨篩選縮水:記住第一次(以及之後最新一次)拿到的年份清單。
  const [years, setYears] = useState<number[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const seq = useRef(0)

  useEffect(() => {
    let cancelled = false
    void fetchWorkspaceAccounts(token, { limit: 500 })
      .then((rows) => {
        if (!cancelled) setAccounts(rows)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
    // setAccounts 來自 usePageCache,引用穩定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    const timer = window.setTimeout(() => setSymbolQuery(symbolInput.trim()), SYMBOL_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [symbolInput])

  const load = useCallback(async () => {
    const mine = ++seq.current
    setLoading(true)
    setError(null)
    try {
      const data = await fetchRealizedPnl(token, {
        accountId: accountId || null,
        year,
        symbol: symbolQuery || null,
      })
      if (mine !== seq.current) return
      setReport(data)
      setYears((prev) => {
        // 只有「沒篩年度」時 server 回的 years 才是完整清單;篩了年度也用聯集避免選項消失。
        const merged = new Set([...prev, ...data.years])
        return [...merged].sort((a, b) => b - a)
      })
    } catch (err) {
      if (mine !== seq.current) return
      setError(localizeError(err, t))
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [token, accountId, year, symbolQuery, t])

  useEffect(() => {
    void load()
  }, [load])

  useSyncRefresh(() => {
    void load()
  })

  const investmentAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === 'investment'),
    [accounts],
  )

  const pnlEntries = Object.entries(report?.realized_pnl_by_currency ?? {})
  const dividendEntries = Object.entries(report?.dividends_by_currency ?? {})
  const symbols = report?.symbols ?? []

  const toggle = (key: string) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>{t('realizedPnl.title')}</CardTitle>
            <p className="mt-1.5 text-sm text-muted-foreground">{t('realizedPnl.desc')}</p>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => navigate(routePath({ kind: 'app', ledgerId: '', section: 'investments' }))}
          >
            {t('realizedPnl.backToInvestments')}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="rpnl-year">
                {t('realizedPnl.filter.year')}
              </label>
              <select
                id="rpnl-year"
                className="flex h-9 w-full rounded-md border border-input bg-muted px-3 text-sm"
                value={year === null ? '' : String(year)}
                onChange={(e) => setYear(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">{t('realizedPnl.filter.allYears')}</option>
                {years.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="rpnl-account">
                {t('realizedPnl.filter.account')}
              </label>
              <select
                id="rpnl-account"
                className="flex h-9 w-full rounded-md border border-input bg-muted px-3 text-sm"
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
              >
                <option value="">{t('realizedPnl.filter.allAccounts')}</option>
                {investmentAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="rpnl-symbol">
                {t('realizedPnl.filter.symbol')}
              </label>
              <Input
                id="rpnl-symbol"
                className="h-9"
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value)}
                placeholder={t('realizedPnl.filter.symbolPlaceholder')}
              />
            </div>
          </div>

          {error ? (
            <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
              <span className="text-destructive">
                {t('realizedPnl.error')}:{error}
              </span>
              <Button size="sm" variant="outline" onClick={() => void load()}>
                {t('realizedPnl.retry')}
              </Button>
            </div>
          ) : null}

          {!report && loading ? (
            <p className="text-sm text-muted-foreground">{t('realizedPnl.loading')}</p>
          ) : null}

          {report ? (
            <div className={`grid gap-3 sm:grid-cols-2 ${loading ? 'opacity-60' : ''}`}>
              <div className="rounded-lg border bg-muted/30 p-4">
                <div className="text-sm text-muted-foreground">{t('realizedPnl.summary.pnl')}</div>
                {pnlEntries.length === 0 ? (
                  <div className="mt-1 text-sm text-muted-foreground">{t('realizedPnl.summary.none')}</div>
                ) : (
                  <div className="mt-1 space-y-0.5">
                    {pnlEntries.map(([ccy, v]) => (
                      <div key={ccy} className={`text-2xl font-semibold tabular-nums ${pnlClass(v)}`}>
                        {formatStockMoney(v, ccy, { signed: true })}
                        <span className="ml-2 text-xs font-normal text-muted-foreground">{ccy}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="rounded-lg border bg-muted/30 p-4">
                <div className="text-sm text-muted-foreground">{t('realizedPnl.summary.dividends')}</div>
                {dividendEntries.length === 0 ? (
                  <div className="mt-1 text-sm text-muted-foreground">—</div>
                ) : (
                  <div className="mt-1 space-y-0.5">
                    {dividendEntries.map(([ccy, v]) => (
                      <div key={ccy} className="text-2xl font-semibold tabular-nums">
                        {formatStockMoney(v, ccy)}
                        <span className="ml-2 text-xs font-normal text-muted-foreground">{ccy}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {report ? (
        <Card className="bc-panel">
          <CardContent className="py-4">
            {symbols.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">{t('realizedPnl.empty')}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs text-muted-foreground">
                      <th className="py-2 font-normal">{t('realizedPnl.col.symbol')}</th>
                      <th className="py-2 text-right font-normal">{t('realizedPnl.col.sellCount')}</th>
                      <th className="py-2 text-right font-normal">{t('realizedPnl.col.proceeds')}</th>
                      <th className="py-2 text-right font-normal">{t('realizedPnl.col.cost')}</th>
                      <th className="py-2 text-right font-normal">{t('realizedPnl.col.pnl')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {symbols.map((s) => {
                      const key = symbolRowKey(s)
                      const open = Boolean(expanded[key])
                      return (
                        <Fragment key={key}>
                          <tr
                            className="cursor-pointer border-b hover:bg-accent/40"
                            onClick={() => toggle(key)}
                            aria-expanded={open}
                          >
                            <td className="py-2">
                              <div className="font-medium">
                                <span className="mr-1.5 text-xs text-muted-foreground">{open ? '▾' : '▸'}</span>
                                {s.symbol}{' '}
                                <span className="font-normal text-muted-foreground">{s.security_name || ''}</span>
                              </div>
                              <div className="pl-4 text-xs text-muted-foreground">
                                {t(`investments.market.${s.market}`)}
                                {s.currency ? ` · ${s.currency}` : ''}
                              </div>
                            </td>
                            <td className="py-2 text-right tabular-nums">{s.sell_count}</td>
                            <td className="py-2 text-right tabular-nums">{formatStockMoney(s.proceeds, s.currency)}</td>
                            <td className="py-2 text-right tabular-nums">
                              {formatStockMoney(s.cost_basis, s.currency)}
                            </td>
                            <td className={`py-2 text-right font-medium tabular-nums ${pnlClass(s.pnl)}`}>
                              {formatStockMoney(s.pnl, s.currency, { signed: true })}
                            </td>
                          </tr>
                          {open && (
                            <tr className="border-b bg-muted/20">
                              <td colSpan={5} className="px-2 py-3">
                                <table className="w-full text-xs">
                                  <thead>
                                    <tr className="text-left text-muted-foreground">
                                      <th className="py-1 font-normal">{t('realizedPnl.col.date')}</th>
                                      <th className="py-1 text-right font-normal">{t('realizedPnl.col.shares')}</th>
                                      <th className="py-1 text-right font-normal">{t('realizedPnl.col.proceeds')}</th>
                                      <th className="py-1 text-right font-normal">{t('realizedPnl.col.cost')}</th>
                                      <th className="py-1 text-right font-normal">{t('realizedPnl.col.pnl')}</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {s.events.map((ev) => (
                                      <tr key={ev.trade_id} className="border-t">
                                        <td className="py-1.5">{isoToDateValue(ev.date)}</td>
                                        <td className="py-1.5 text-right tabular-nums">{formatShares(ev.shares)}</td>
                                        <td className="py-1.5 text-right tabular-nums">
                                          {formatStockMoney(ev.proceeds, ev.currency)}
                                        </td>
                                        <td className="py-1.5 text-right tabular-nums">
                                          {formatStockMoney(ev.cost_basis, ev.currency)}
                                        </td>
                                        <td className={`py-1.5 text-right tabular-nums ${pnlClass(ev.pnl)}`}>
                                          {formatStockMoney(ev.pnl, ev.currency, { signed: true })}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
