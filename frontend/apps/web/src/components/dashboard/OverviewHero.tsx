import { Area, AreaChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { ReadAccount, ReadLedger } from '@beecount/api-client'
import { useT } from '@beecount/ui'
import { accountBalance } from '@beecount/web-features'

interface Props {
  ledgers: ReadLedger[]
  accounts?: ReadAccount[]
  /** 收支序列，用于右侧 sparkline。范围跟服务端当前 scope 保持一致。 */
  periodSeries?: Array<{ bucket: string; income: number; expense: number; balance: number }>
  /** scope 的起止金额，避免重复把 series 再汇一次（也避免 series 空但 summary 有数的情况）。 */
  periodSummary?: { income_total: number; expense_total: number }
  /** 当前 scope 的 label —— 由调用方根据 i18n 决定 */
  periodLabel?: string
}

function currencyLabel(ledgers: ReadLedger[]): string {
  const first = ledgers.find((l) => l.currency)
  return first?.currency || 'CNY'
}

/**
 * Hero 横幅：净值 = Σ 账户带符号余额（复用 assetAggregation 的负债符号口径单点：
 * 欠款为负自然扣减、溢缴为正计入）+ 周期收支副标题 + 右侧迷你 sparkline。
 * 之前这里手搓了一份聚合：资产负债全取 `Math.abs(initial_balance)` 再相减 ——
 * 既丢了符号（透支/溢缴全算错）也没吃 server 聚合后的 balance，跟资产页对不上。
 * 已知债：这里仍把所有账户裸加（未按币种切分），多币种用户的 hero 净值口径
 * 留待多币种 dashboard 折算一并解决，不在负债符号修复范围内。
 */
export function OverviewHero({
  ledgers,
  accounts,
  periodSeries,
  periodSummary,
  periodLabel
}: Props) {
  const t = useT()
  const scopeLabel = periodLabel ?? t('home.scope.year')
  const currency = currencyLabel(ledgers)
  const totalBalance = (accounts || []).reduce((sum, a) => sum + accountBalance(a), 0)
  // 优先吃后端给的 summary（无论 series 空不空都是权威值）；fallback 到 series 聚合。
  const periodIncome =
    periodSummary?.income_total ??
    (periodSeries || []).reduce((a, it) => a + (it.income || 0), 0)
  const periodExpense =
    periodSummary?.expense_total ??
    (periodSeries || []).reduce((a, it) => a + (it.expense || 0), 0)

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const trendData = (periodSeries || []).slice(-30).map((it, i) => ({
    idx: i,
    v: it.balance
  }))

  return (
    <div className="relative overflow-hidden rounded-2xl border border-primary/30">
      <div
        className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/25 via-primary/5 to-transparent"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -right-16 -top-12 h-48 w-48 rounded-full bg-primary/30 blur-3xl"
        aria-hidden
      />
      <div className="relative grid gap-4 p-6 md:grid-cols-[1.3fr_1fr]">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
            {t('overview.hero.netWorth')}
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-xs text-muted-foreground">{currency}</span>
            <span
              className={`text-4xl font-black tracking-tight sm:text-5xl ${
                totalBalance >= 0 ? 'text-income' : 'text-expense'
              }`}
            >
              {fmt(totalBalance)}
            </span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
            <span className="inline-flex items-center gap-1 text-income">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              {t('overview.hero.scopeIncome')
                .replace('{scope}', scopeLabel)
                .replace('{value}', `${currency} ${fmt(periodIncome)}`)}
            </span>
            <span className="inline-flex items-center gap-1 text-expense">
              <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
              {t('overview.hero.scopeExpense')
                .replace('{scope}', scopeLabel)
                .replace('{value}', `${currency} ${fmt(periodExpense)}`)}
            </span>
            <span className="text-xs text-muted-foreground">
              {t('overview.hero.ledgerCount').replace('{count}', String(ledgers.length))}
            </span>
          </div>
        </div>

        <div className="h-28 min-w-0">
          {trendData.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trendData} margin={{ left: 0, right: 0, top: 4, bottom: 0 }}>
                <defs>
                  <linearGradient id="heroGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.55} />
                    <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0.02} />
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
                  formatter={((v: number) => [fmt(v), t('overview.hero.netWorthShort')]) as unknown as never}
                />
                <Area
                  type="monotone"
                  dataKey="v"
                  stroke="hsl(var(--primary))"
                  strokeWidth={2}
                  fill="url(#heroGrad)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
              {t('overview.hero.noTrend')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
