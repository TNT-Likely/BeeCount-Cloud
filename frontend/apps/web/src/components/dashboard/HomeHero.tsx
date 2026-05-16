import { useMemo, useState } from 'react'
import { Area, AreaChart, ResponsiveContainer, Tooltip } from 'recharts'
import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  Receipt
} from 'lucide-react'

import type {
  ReadBudget,
  ReadLedger,
  WorkspaceAnalyticsAnomalyMonth,
  WorkspaceAnalyticsSeriesItem,
  WorkspaceAnalyticsSummary,
  WorkspaceLedgerCounts
} from '@beecount/api-client'
import { Amount, type BudgetUsage } from '@beecount/web-features'
import { useT } from '@beecount/ui'

import { HeroInsightsRow } from './HeroInsightsRow'

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
  /** 当前账本预算配置 + 当周期 used,空数组 → chip 不显示。 */
  budgets?: ReadBudget[]
  budgetUsageById?: Record<string, BudgetUsage>
  /** 异常月份(scope=year analytics 返回),空数组 + hasEnoughMonths=true 显示 ✓ */
  anomalyMonths?: WorkspaceAnalyticsAnomalyMonth[]
  hasEnoughMonthsForAnomaly?: boolean
}

// 三个 scope 的 label/hint 在组件里 t() 时动态查,这里只留 value 列表
const SCOPE_VALUES: HeroScope[] = ['month', 'year', 'all']

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
  ledgerCounts,
  budgets,
  budgetUsageById,
  anomalyMonths,
  hasEnoughMonthsForAnomaly
}: Props) {
  const t = useT()
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
  const scopeLabel = t(`home.scope.${scope}`)
  const scopeBalanceHint = t(`home.scope.${scope}.hint`)

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
    // overflow-visible:hero 内的 InsightsRow chip 用 popover 浮出详情,需要
    // 越出 hero 边界。装饰光斑挪到内层 overflow-hidden 子层去 clip。
    <div
      className="relative rounded-2xl border border-primary/30"
      style={{
        background:
          'linear-gradient(135deg, hsl(var(--primary)/0.18) 0%, hsl(var(--primary)/0.04) 55%, transparent 100%)'
      }}
    >
      {/* 装饰光斑容器 — 单独 overflow-hidden + inset-0 + rounded-2xl 跟父
           容器对齐,光斑不漏到 hero 外。popover absolute 定位在 grid 容器
           内,跟这层平级,不被 clip。 */}
      <div
        className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl"
        aria-hidden
      >
        <div className="absolute -right-20 -top-20 h-72 w-72 rounded-full bg-primary/30 blur-3xl" />
        <div className="absolute -left-24 bottom-0 h-56 w-56 rounded-full bg-primary/15 blur-3xl" />
      </div>

      {/* 窄屏 p-4 / 桌面 p-6:mobile 视口下 hero 体积大,内容只占一列时
           24px padding 显得很空,16px 紧凑但不挤。grid gap 同步收一点。 */}
      <div className="relative grid gap-4 p-4 sm:gap-5 sm:p-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="min-w-0">
          {/* 顶部：账本名 + 三视角切换 */}
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
                <CalendarDays className="h-3 w-3" />
                {t('home.scope.current')} · {scopeLabel}
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
              label={t('home.hero.income').replace('{scope}', scopeLabel)}
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
              label={t('home.hero.expense').replace('{scope}', scopeLabel)}
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
              label={t('home.hero.count')}
            >
              <div className="mt-0.5 font-mono text-xl font-bold tabular-nums leading-tight">
                {txCount.toLocaleString()}
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  {t('home.hero.countUnit')}
                </span>
              </div>
            </HeroStat>
            <HeroStat
              icon={<CalendarDays className="h-3.5 w-3.5 text-sky-500" />}
              label={t('home.hero.days')}
            >
              <div className="mt-0.5 font-mono text-xl font-bold tabular-nums leading-tight">
                {days.toLocaleString()}
                <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                  {t('home.hero.daysUnit')}
                </span>
              </div>
            </HeroStat>
          </div>

          {/* 预算 + 异常归因 chip — 关键回顾信息一行带过,hover 出详情 */}
          <HeroInsightsRow
            budgets={budgets || []}
            budgetUsageById={budgetUsageById || {}}
            anomalyMonths={anomalyMonths || []}
            hasEnoughMonths={!!hasEnoughMonthsForAnomaly}
            currency={currency}
          />
        </div>

        {/* 右侧：sparkline，随 scope 变 */}
        <div className="flex min-h-[220px] flex-col gap-2 rounded-xl border border-border/40 bg-background/40 p-3 backdrop-blur-sm">
          <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-muted-foreground">
            <span>{t('home.hero.trend').replace('{scope}', scopeLabel)}</span>
            {trendData.length > 0 ? (
              <span className="font-mono tabular-nums">
                {trendData.length}
                {scope === 'month'
                  ? t('home.hero.trendUnit.day')
                  : scope === 'year'
                    ? t('home.hero.trendUnit.month')
                    : t('home.hero.trendUnit.period')}
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
                        v.toLocaleString(undefined, { maximumFractionDigits: 2 }),
                        t('home.hero.balanceAccum')
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
                {t('home.hero.noTx')}
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
  const t = useT()
  return (
    <div className="inline-flex rounded-lg border border-border/60 bg-background/60 p-0.5 backdrop-blur-sm">
      {SCOPE_VALUES.map((scope) => {
        const active = scope === value
        return (
          <button
            key={scope}
            type="button"
            onClick={() => onChange(scope)}
            className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors ${
              active
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t(`home.scope.${scope}`)}
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
