import { Area, AreaChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { ReadLedger } from '@beecount/api-client'

interface Props {
  ledgers: ReadLedger[]
  monthSeries?: Array<{ bucket: string; income: number; expense: number; balance: number }>
}

function currencyLabel(ledgers: ReadLedger[]): string {
  const first = ledgers.find((l) => l.currency)
  return first?.currency || 'CNY'
}

/**
 * Hero 横幅：主净值大号数字 + 本月收支副标题 + 右侧迷你 sparkline。
 * 视觉采用主题色渐变 + 磨砂玻璃感，作为 dashboard 的视觉锚。
 */
export function OverviewHero({ ledgers, monthSeries }: Props) {
  const currency = currencyLabel(ledgers)
  const totalBalance = ledgers.reduce((a, l) => a + l.balance, 0)
  const monthIncome = (monthSeries || []).reduce((a, it) => a + (it.income || 0), 0)
  const monthExpense = (monthSeries || []).reduce((a, it) => a + (it.expense || 0), 0)

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const trendData = (monthSeries || []).slice(-30).map((it, i) => ({
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
            净资产总览
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-xs text-muted-foreground">{currency}</span>
            <span
              className={`text-4xl font-black tracking-tight sm:text-5xl ${
                totalBalance >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'
              }`}
            >
              {fmt(totalBalance)}
            </span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              本月收入 {currency} {fmt(monthIncome)}
            </span>
            <span className="inline-flex items-center gap-1 text-rose-600 dark:text-rose-400">
              <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
              本月支出 {currency} {fmt(monthExpense)}
            </span>
            <span className="text-xs text-muted-foreground">
              跨 {ledgers.length} 个账本合计
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
                  formatter={((v: number) => [fmt(v), '净值']) as unknown as never}
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
              暂无趋势数据
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
