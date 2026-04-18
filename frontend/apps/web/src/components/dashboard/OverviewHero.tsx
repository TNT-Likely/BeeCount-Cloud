import { Area, AreaChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { ReadAccount, ReadLedger } from '@beecount/api-client'

interface Props {
  ledgers: ReadLedger[]
  accounts?: ReadAccount[]
  /** 收支序列，用于右侧 sparkline。范围跟服务端当前 scope 保持一致。 */
  periodSeries?: Array<{ bucket: string; income: number; expense: number; balance: number }>
  /** scope 的起止金额，避免重复把 series 再汇一次（也避免 series 空但 summary 有数的情况）。 */
  periodSummary?: { income_total: number; expense_total: number }
  /** 当前 scope 的中文 label —— "今年" / "本月" / "全部" */
  periodLabel?: string
}

function currencyLabel(ledgers: ReadLedger[]): string {
  const first = ledgers.find((l) => l.currency)
  return first?.currency || 'CNY'
}

// 负债类账户（信用卡、贷款）计入负债，其余全算资产。与 AccountsPanel 的
// VALUATION_TYPES + LIABILITY_TYPES 保持一致。
const LIABILITY_TYPES = new Set(['credit_card', 'loan'])

/**
 * Hero 横幅：净值 = 资产 - 负债（按账户余额聚合）+ 周期收支副标题 + 右侧
 * 迷你 sparkline。之前用 ledger.balance 汇总 (收入-支出) 做"净资产"实际是
 * 现金流净额，和"资产-负债"不是一回事，数字跟下方资产环形图对不上。
 */
export function OverviewHero({
  ledgers,
  accounts,
  periodSeries,
  periodSummary,
  periodLabel = '今年'
}: Props) {
  const currency = currencyLabel(ledgers)
  let assetTotal = 0
  let liabilityTotal = 0
  for (const a of accounts || []) {
    const bal = Math.abs(a.initial_balance ?? 0)
    if (LIABILITY_TYPES.has(a.account_type || '')) {
      liabilityTotal += bal
    } else {
      assetTotal += bal
    }
  }
  const totalBalance = assetTotal - liabilityTotal
  // 优先吃后端给的 summary（无论 series 空不空都是权威值）；fallback 到 series 聚合。
  const periodIncome =
    periodSummary?.income_total ??
    (periodSeries || []).reduce((a, it) => a + (it.income || 0), 0)
  const periodExpense =
    periodSummary?.expense_total ??
    (periodSeries || []).reduce((a, it) => a + (it.expense || 0), 0)

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

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
            净值（资产 - 负债）
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
              {periodLabel}收入 {currency} {fmt(periodIncome)}
            </span>
            <span className="inline-flex items-center gap-1 text-expense">
              <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
              {periodLabel}支出 {currency} {fmt(periodExpense)}
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
