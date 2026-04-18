import { useMemo, useState } from 'react'
import { Area, AreaChart, ResponsiveContainer, Tooltip } from 'recharts'
import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  Receipt
} from 'lucide-react'

import type {
  ReadLedger,
  WorkspaceAnalyticsSeriesItem,
  WorkspaceAnalyticsSummary,
  WorkspaceLedgerCounts
} from '@beecount/api-client'
import { Amount } from '@beecount/web-features'

type HeroScope = 'month' | 'year' | 'all'

interface Props {
  ledgers: ReadLedger[]
  currentLedgerId?: string
  monthSummary?: WorkspaceAnalyticsSummary
  monthSeries?: WorkspaceAnalyticsSeriesItem[]
  yearSummary?: WorkspaceAnalyticsSummary
  yearSeries?: WorkspaceAnalyticsSeriesItem[]
  allSummary?: WorkspaceAnalyticsSummary
  allSeries?: WorkspaceAnalyticsSeriesItem[]
  ledgerCounts?: WorkspaceLedgerCounts
}

const SCOPE_OPTIONS: Array<{ value: HeroScope; label: string; hint: string }> = [
  { value: 'month', label: '本月', hint: '本月结余' },
  { value: 'year', label: '今年', hint: '今年结余' },
  { value: 'all', label: '汇总', hint: '全部结余' }
]

/**
 * 首页 hero 卡。三视角切换（本月 / 今年 / 汇总）：
 * - 大号结余 = 对应 scope 的 income - expense（对齐 mobile `monthlyTotals` /
 *   `yearlyTotals` / 全量聚合）
 * - 本月/今年/全部 收入 + 支出 两个 HeroStat 跟随 scope 变
 * - 记账笔数 / 记账天数 从 ledgerCounts 来（账本全量，不随 scope 变）
 * - 右侧 sparkline: month 按日累计；year / all 按月累计
 */
export function HomeHero({
  ledgers,
  currentLedgerId,
  monthSummary,
  monthSeries,
  yearSummary,
  yearSeries,
  allSummary,
  allSeries,
  ledgerCounts
}: Props) {
  const [scope, setScope] = useState<HeroScope>('month')

  const activeLedger =
    ledgers.find((l) => l.ledger_id === currentLedgerId) || ledgers[0]
  const currency = activeLedger?.currency || 'CNY'

  const summaryByScope: Record<HeroScope, WorkspaceAnalyticsSummary | undefined> = {
    month: monthSummary,
    year: yearSummary,
    all: allSummary
  }
  const seriesByScope: Record<HeroScope, WorkspaceAnalyticsSeriesItem[]> = {
    month: monthSeries || [],
    year: yearSeries || [],
    all: allSeries || []
  }

  const activeSummary = summaryByScope[scope]
  const activeSeries = seriesByScope[scope]
  const scopeLabel = SCOPE_OPTIONS.find((o) => o.value === scope)?.label || '本月'
  const scopeBalanceHint =
    SCOPE_OPTIONS.find((o) => o.value === scope)?.hint || '本月结余'

  const income = activeSummary?.income_total ?? 0
  const expense = activeSummary?.expense_total ?? 0
  const balance = activeSummary?.balance ?? income - expense

  const txCount = ledgerCounts?.tx_count ?? 0
  const days = ledgerCounts?.days_since_first_tx ?? 0

  // sparkline: 本月按日累计；年/全部按月累计。series 已按 bucket 分桶。
  const trendData = useMemo(() => {
    const sorted = activeSeries.slice().sort((a, b) => a.bucket.localeCompare(b.bucket))
    let running = 0
    return sorted.map((it) => {
      running += (it.income || 0) - (it.expense || 0)
      return { bucket: it.bucket, v: running }
    })
  }, [activeSeries])

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-primary/30"
      style={{
        background:
          'linear-gradient(135deg, hsl(var(--primary)/0.18) 0%, hsl(var(--primary)/0.04) 55%, transparent 100%)'
      }}
    >
      {/* 装饰光斑 */}
      <div
        className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-primary/30 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -left-24 bottom-0 h-56 w-56 rounded-full bg-primary/15 blur-3xl"
        aria-hidden
      />

      <div className="relative grid gap-5 p-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="min-w-0">
          {/* 顶部：账本名 + 三视角切换 */}
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
                <CalendarDays className="h-3 w-3" />
                当前账本 · {scopeLabel}
              </div>
              <div className="mt-1 flex items-baseline gap-3">
                <span className="truncate text-xl font-bold">
                  {activeLedger?.ledger_name || '—'}
                </span>
                <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                  {currency}
                </span>
              </div>
            </div>
            <ScopeSwitcher value={scope} onChange={setScope} />
          </div>

          <div className="mt-4 text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
            {scopeBalanceHint}
          </div>
          <Amount
            value={balance}
            showCurrency
            currency={currency}
            size="4xl"
            bold
            tone={balance >= 0 ? 'positive' : 'negative'}
            className="mt-1 block font-black tracking-tight"
          />

          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <HeroStat
              icon={<ArrowDownLeft className="h-3.5 w-3.5 text-income" />}
              label={`${scopeLabel}收入`}
            >
              <Amount
                value={income}
                currency={currency}
                showCurrency
                bold
                size="xl"
                tone="positive"
                className="mt-0.5 block leading-tight"
              />
            </HeroStat>
            <HeroStat
              icon={<ArrowUpRight className="h-3.5 w-3.5 text-expense" />}
              label={`${scopeLabel}支出`}
            >
              <Amount
                value={expense}
                currency={currency}
                showCurrency
                bold
                size="xl"
                tone="negative"
                className="mt-0.5 block leading-tight"
              />
            </HeroStat>
            <HeroStat
              icon={<Receipt className="h-3.5 w-3.5 text-amber-500" />}
              label="记账笔数"
            >
              <div className="mt-0.5 font-mono text-xl font-bold tabular-nums leading-tight">
                {txCount.toLocaleString('zh-CN')}
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  笔
                </span>
              </div>
            </HeroStat>
            <HeroStat
              icon={<CalendarDays className="h-3.5 w-3.5 text-sky-500" />}
              label="记账天数"
            >
              <div className="mt-0.5 font-mono text-xl font-bold tabular-nums leading-tight">
                {days.toLocaleString('zh-CN')}
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  天
                </span>
              </div>
            </HeroStat>
          </div>
        </div>

        {/* 右侧：sparkline，随 scope 变 */}
        <div className="flex min-h-[220px] flex-col gap-2 rounded-xl border border-border/40 bg-background/40 p-3 backdrop-blur-sm">
          <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-muted-foreground">
            <span>{scopeLabel}结余走势</span>
            {trendData.length > 0 ? (
              <span className="font-mono tabular-nums">
                {trendData.length}
                {scope === 'month' ? '天' : scope === 'year' ? '月' : '期'}
              </span>
            ) : null}
          </div>
          <div className="flex-1">
            {trendData.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={trendData}
                  margin={{ left: 0, right: 0, top: 4, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="homeHeroGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor="hsl(var(--primary))"
                        stopOpacity={0.55}
                      />
                      <stop
                        offset="95%"
                        stopColor="hsl(var(--primary))"
                        stopOpacity={0.02}
                      />
                    </linearGradient>
                  </defs>
                  <Tooltip
                    cursor={false}
                    contentStyle={{
                      background: 'hsl(var(--popover))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: 6,
                      fontSize: 11
                    }}
                    formatter={
                      ((v: number) => [
                        v.toLocaleString('zh-CN', { maximumFractionDigits: 2 }),
                        '累计结余'
                      ]) as unknown as never
                    }
                    labelFormatter={(_label, payload) => {
                      const item = payload?.[0]?.payload as { bucket?: string }
                      return item?.bucket || ''
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="v"
                    stroke="hsl(var(--primary))"
                    strokeWidth={2}
                    fill="url(#homeHeroGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                暂无交易
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function ScopeSwitcher({
  value,
  onChange
}: {
  value: HeroScope
  onChange: (v: HeroScope) => void
}) {
  return (
    <div className="inline-flex rounded-lg border border-border/60 bg-background/60 p-0.5 backdrop-blur-sm">
      {SCOPE_OPTIONS.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors ${
              active
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

function HeroStat({
  icon,
  label,
  children
}: {
  icon: React.ReactNode
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-border/40 bg-background/50 px-3 py-2 backdrop-blur-sm">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      {children}
    </div>
  )
}
