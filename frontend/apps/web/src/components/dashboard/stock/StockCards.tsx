import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import type { Holding, InvestmentFlowCurrency } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { formatPercent, formatPrice, formatShares, formatStockMoney } from '@beecount/web-features'

import { pnlClass } from '../../../pages/sections/investmentsShared'
import { routePath } from '../../../state/router'
import type { HomeStockData } from './useHomeStockData'

/**
 * 首頁股票卡片(2026-10-03,docs/STOCK_HOLDINGS_SD.md §12)。資料由
 * `useHomeStockData` 統一載入,卡片只負責顯示;金額一律依證券幣別分開,
 * 不跨幣別加總(跟投資頁同口徑)。點卡片進對應頁面。
 */

const PALETTE = ['#ec4899', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4', '#ef4444', '#84cc16']

function useGo() {
  const navigate = useNavigate()
  return (section: 'investments' | 'realized-pnl' | 'recurring-rules') =>
    navigate(routePath({ kind: 'app', ledgerId: '', section }))
}

function StockCardShell({
  title,
  onClick,
  children,
}: {
  title: string
  onClick?: () => void
  children: ReactNode
}) {
  return (
    <Card
      className={`bc-panel h-full overflow-hidden ${onClick ? 'cursor-pointer transition-colors hover:bg-accent/30' : ''}`}
      onClick={onClick}
    >
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-24 items-center justify-center text-center text-xs text-muted-foreground">{children}</div>
}

function openHoldings(data: HomeStockData): Holding[] {
  return (data.holdings?.accounts ?? []).flatMap((a) => a.holdings).filter((h) => h.shares > 0)
}

interface AggregatedHolding {
  key: string
  name: string
  symbol: string
  currency: string
  shares: number
  cost: number
  marketValue: number | null
  pnl: number | null
}

/** 同標的跨帳戶合併;市值/損益只要有一個帳戶沒報價就整檔視為沒有(不拿殘缺數字排名)。 */
export function aggregateHoldings(list: Holding[]): AggregatedHolding[] {
  const map = new Map<string, AggregatedHolding>()
  for (const h of list) {
    const key = `${h.market}:${h.symbol}`
    const cur = (h.currency || '').toUpperCase()
    const prev = map.get(key)
    const mv = h.market_value
    const pnl = h.unrealized_pnl
    if (!prev) {
      map.set(key, {
        key,
        name: h.security_name || h.symbol,
        symbol: h.symbol,
        currency: cur,
        shares: h.shares,
        cost: h.total_cost,
        marketValue: mv,
        pnl,
      })
    } else {
      prev.shares += h.shares
      prev.cost += h.total_cost
      prev.marketValue = prev.marketValue !== null && mv !== null ? prev.marketValue + mv : null
      prev.pnl = prev.pnl !== null && pnl !== null ? prev.pnl + pnl : null
    }
  }
  return Array.from(map.values())
}

export function TopHoldingsCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const rows = useMemo(() => {
    const agg = aggregateHoldings(openHoldings(data))
    // 不同幣別的市值不能直接比大小:先依幣別總市值排,再依各檔市值排。
    const totalByCur = new Map<string, number>()
    for (const r of agg) totalByCur.set(r.currency, (totalByCur.get(r.currency) ?? 0) + (r.marketValue ?? r.cost))
    return agg
      .sort(
        (a, b) =>
          (totalByCur.get(b.currency) ?? 0) - (totalByCur.get(a.currency) ?? 0) ||
          (b.marketValue ?? b.cost) - (a.marketValue ?? a.cost),
      )
      .slice(0, 5)
  }, [data])
  return (
    <StockCardShell title={t('home.stock.top.title')} onClick={() => go('investments')}>
      {rows.length === 0 ? (
        <Empty>{t('home.stock.empty')}</Empty>
      ) : (
        <ul className="space-y-2.5">
          {rows.map((r) => {
            const pct = r.pnl !== null && r.cost > 0 ? (r.pnl / r.cost) * 100 : null
            return (
              <li key={r.key} className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{r.name}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {r.symbol} · {formatShares(r.shares)} {t('investments.dividend.sharesUnit')}
                  </div>
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <div className="text-sm">
                    {r.marketValue !== null ? formatStockMoney(r.marketValue, r.currency) : '—'}
                  </div>
                  <div className={`text-[11px] ${pnlClass(r.pnl)}`}>
                    {r.pnl !== null ? formatStockMoney(r.pnl, r.currency, { signed: true }) : '—'}
                    {pct !== null ? ` (${formatPercent(pct)})` : ''}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </StockCardShell>
  )
}

export function TodayChangeCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const rows = useMemo(() => {
    const byCur = new Map<string, { change: number; base: number }>()
    for (const h of openHoldings(data)) {
      const q = h.quote
      if (!q || q.change === null || q.prev_close === null) continue
      const cur = (h.currency || q.currency || '').toUpperCase()
      const acc = byCur.get(cur) ?? { change: 0, base: 0 }
      acc.change += q.change * h.shares
      acc.base += q.prev_close * h.shares
      byCur.set(cur, acc)
    }
    return Array.from(byCur.entries()).map(([currency, v]) => ({
      currency,
      change: v.change,
      pct: v.base > 0 ? (v.change / v.base) * 100 : null,
    }))
  }, [data])
  return (
    <StockCardShell title={t('home.stock.today.title')} onClick={() => go('investments')}>
      {rows.length === 0 ? (
        <Empty>{t('home.stock.today.empty')}</Empty>
      ) : (
        <div className="space-y-2">
          {rows.map((r) => (
            <div key={r.currency} className="flex items-baseline justify-between gap-3">
              <span className="text-xs text-muted-foreground">{r.currency}</span>
              <span className={`text-xl font-semibold tabular-nums ${pnlClass(r.change)}`}>
                {formatStockMoney(r.change, r.currency, { signed: true })}
                {r.pct !== null ? (
                  <span className="ml-2 text-sm font-normal">({formatPercent(r.pct)})</span>
                ) : null}
              </span>
            </div>
          ))}
          <p className="text-[11px] text-muted-foreground">{t('home.stock.today.hint')}</p>
        </div>
      )}
    </StockCardShell>
  )
}

export function AllocationCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const [picked, setPicked] = useState<string | null>(null)
  const { currencies, slices, current } = useMemo(() => {
    const agg = aggregateHoldings(openHoldings(data))
    const totals = new Map<string, number>()
    for (const r of agg) totals.set(r.currency, (totals.get(r.currency) ?? 0) + (r.marketValue ?? r.cost))
    const curs = Array.from(totals.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([c]) => c)
    const cur = picked && curs.includes(picked) ? picked : (curs[0] ?? '')
    const list = agg
      .filter((r) => r.currency === cur)
      .map((r) => ({ name: r.name, value: r.marketValue ?? r.cost }))
      .filter((r) => r.value > 0)
      .sort((a, b) => b.value - a.value)
    const top = list.slice(0, 7)
    const rest = list.slice(7).reduce((s, r) => s + r.value, 0)
    if (rest > 0) top.push({ name: t('home.stock.alloc.other'), value: rest })
    return { currencies: curs, slices: top, current: cur }
  }, [data, picked, t])
  const total = slices.reduce((s, r) => s + r.value, 0)
  return (
    <StockCardShell title={t('home.stock.alloc.title')} onClick={() => go('investments')}>
      {slices.length === 0 ? (
        <Empty>{t('home.stock.empty')}</Empty>
      ) : (
        <div className="space-y-3">
          {currencies.length > 1 && (
            <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
              {currencies.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setPicked(c)}
                  className={`rounded-full border px-2.5 py-0.5 text-[11px] ${
                    c === current ? 'border-primary bg-primary/15 text-primary' : 'border-border/60 text-muted-foreground'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
          )}
          <div className="grid items-center gap-3 sm:grid-cols-[140px_1fr]">
            <div className="h-[140px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={slices} dataKey="value" nameKey="name" innerRadius={38} outerRadius={64} stroke="none">
                    {slices.map((_, i) => (
                      <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: 'hsl(var(--popover))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: 6,
                      fontSize: 11,
                    }}
                    formatter={((v: number) => formatStockMoney(v, current)) as unknown as never}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <ul className="space-y-1">
              {slices.map((s, i) => (
                <li key={s.name} className="flex items-center gap-2 text-xs">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
                  <span className="min-w-0 flex-1 truncate">{s.name}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {total > 0 ? `${((s.value / total) * 100).toFixed(1)}%` : '—'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </StockCardShell>
  )
}

export function RealizedYearCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const r = data.realizedYear
  const pnl = Object.entries(r?.realized_pnl_by_currency ?? {})
  const div = Object.entries(r?.dividends_by_currency ?? {})
  return (
    <StockCardShell
      title={t('home.stock.realized.title', { year: data.realizedYearNumber })}
      onClick={() => go('realized-pnl')}
    >
      {pnl.length === 0 && div.length === 0 ? (
        <Empty>{t('home.stock.realized.empty')}</Empty>
      ) : (
        <div className="space-y-2">
          {pnl.map(([cur, v]) => (
            <div key={cur} className="flex items-baseline justify-between gap-3">
              <span className="text-xs text-muted-foreground">{t('investments.realized')} · {cur}</span>
              <span className={`text-xl font-semibold tabular-nums ${pnlClass(v)}`}>
                {formatStockMoney(v, cur, { signed: true })}
              </span>
            </div>
          ))}
          {div.map(([cur, v]) => (
            <div key={cur} className="flex items-baseline justify-between gap-3 text-sm">
              <span className="text-xs text-muted-foreground">{t('investments.dividends')} · {cur}</span>
              <span className="tabular-nums text-income">{formatStockMoney(v, cur)}</span>
            </div>
          ))}
          <p className="text-[11px] text-muted-foreground">{t('home.stock.realized.hint')}</p>
        </div>
      )}
    </StockCardShell>
  )
}

function FlowRow({ f }: { f: InvestmentFlowCurrency }) {
  const t = useT()
  const cur = f.currency
  return (
    <div className="space-y-1 border-b border-border/30 pb-2 last:border-b-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted-foreground">{cur}</span>
        <span className="text-xl font-semibold tabular-nums" data-testid={`flow-net-${cur}`}>
          {formatStockMoney(f.net_invested, cur, { signed: true })}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 text-[11px] text-muted-foreground tabular-nums">
        <span>{t('home.stock.flow.buy')} {formatStockMoney(f.buy_amount, cur)}</span>
        <span>{t('home.stock.flow.sell')} {formatStockMoney(f.sell_amount, cur)}</span>
        <span>{t('home.stock.flow.fees')} {formatStockMoney(f.fees + f.taxes, cur)}</span>
        <span>{t('home.stock.flow.dividends')} {formatStockMoney(f.dividends, cur)}</span>
      </div>
    </div>
  )
}

export function MonthFlowCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const rows = data.flow.month?.by_currency ?? []
  return (
    <StockCardShell title={t('home.stock.flow.title')} onClick={() => go('investments')}>
      {rows.length === 0 ? (
        <Empty>{t('home.stock.flow.empty')}</Empty>
      ) : (
        <div className="space-y-2">
          {rows.map((f) => (
            <FlowRow key={f.currency} f={f} />
          ))}
          <p className="text-[11px] text-muted-foreground">{t('home.stock.flow.hint')}</p>
        </div>
      )}
    </StockCardShell>
  )
}

export function DividendsCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const today = new Date().toISOString().slice(0, 10)
  const events = useMemo(
    () => data.events.slice().sort((a, b) => (a.ex_date < b.ex_date ? -1 : 1)).slice(0, 5),
    [data.events],
  )
  const pending = data.pending.slice(0, 3)
  const nameBySymbol = useMemo(() => {
    const m = new Map<string, string>()
    for (const h of openHoldings(data)) m.set(`${h.market}:${h.symbol}`, h.security_name || h.symbol)
    return m
  }, [data])
  return (
    <StockCardShell title={t('home.stock.div.title')} onClick={() => go('investments')}>
      {pending.length === 0 && events.length === 0 ? (
        <Empty>{t('home.stock.div.empty')}</Empty>
      ) : (
        <div className="space-y-3">
          {data.pending.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-primary">
                {t('investments.dividend.pendingBadge', { count: data.pending.length })}
              </p>
              {pending.map((p) => (
                <div key={p.id} className="flex items-center justify-between gap-3 text-xs">
                  <span className="min-w-0 truncate">{p.security_name || p.symbol}</span>
                  <span className="shrink-0 tabular-nums text-income">
                    {formatStockMoney(p.est_net, p.currency || p.account_currency)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {events.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[11px] text-muted-foreground">{t('home.stock.div.events')}</p>
              {events.map((e) => (
                <div key={`${e.market}:${e.symbol}:${e.ex_date}`} className="flex items-center justify-between gap-3 text-xs">
                  <span className="min-w-0 truncate">
                    {nameBySymbol.get(`${e.market}:${e.symbol}`) || e.symbol}
                    <span className="ml-1.5 text-muted-foreground">{e.ex_date}</span>
                    {e.ex_date >= today && (
                      <span className="ml-1.5 rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">
                        {t('home.stock.div.upcoming')}
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {e.cash_per_share > 0
                      ? t('investments.dividend.perShare', { amount: formatPrice(e.cash_per_share) })
                      : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </StockCardShell>
  )
}

export function DcaCard({ data }: { data: HomeStockData }) {
  const t = useT()
  const go = useGo()
  const rows = useMemo(
    () =>
      data.dcaRules
        .map((r) => ({ rule: r, next: r.upcoming_run_at || r.next_run_at }))
        .sort((a, b) => (a.next < b.next ? -1 : 1))
        .slice(0, 5),
    [data.dcaRules],
  )
  return (
    <StockCardShell title={t('home.stock.dca.title')} onClick={() => go('recurring-rules')}>
      {rows.length === 0 ? (
        <Empty>{t('home.stock.dca.empty')}</Empty>
      ) : (
        <ul className="space-y-2.5">
          {rows.map(({ rule, next }) => (
            <li key={rule.id} className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{rule.security_name || rule.symbol || rule.note}</div>
                <div className="text-[11px] text-muted-foreground">
                  {t('home.stock.dca.next', { date: new Date(next).toLocaleDateString() })}
                </div>
              </div>
              <div className="shrink-0 text-sm tabular-nums">{rule.amount.toLocaleString()}</div>
            </li>
          ))}
        </ul>
      )}
    </StockCardShell>
  )
}
