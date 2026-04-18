import { ArrowDownLeft, ArrowUpRight, TrendingDown, TrendingUp } from 'lucide-react'

import { Amount } from '@beecount/web-features'

interface Props {
  monthIncome: number
  monthExpense: number
  prevMonthIncome: number
  prevMonthExpense: number
  monthTxCount: number
  currency?: string
}

/**
 * 本月 4 格关键指标：收入 / 支出 / 结余 / 笔数，每个卡片带"对比上月"的百分比
 * 徽章。上涨绿 / 下跌红 / 持平灰。视觉上用渐变 + 数据徽章做"炫酷卡片"。
 *
 * 输入参数：
 * - 本月 / 上月的 income / expense total（从 analytics series 里筛出来）
 * - 本月笔数（直接从 summary 聚合）
 */
export function HomeMonthMetrics({
  monthIncome,
  monthExpense,
  prevMonthIncome,
  prevMonthExpense,
  monthTxCount,
  currency = 'CNY'
}: Props) {
  const monthBalance = monthIncome - monthExpense
  const prevBalance = prevMonthIncome - prevMonthExpense

  type Card = {
    key: string
    label: string
    value: number
    prev: number | null
    positiveWhenUp: boolean
    icon: React.ReactNode
    accent: string
    bg: string
    ring: string
    isCount?: boolean
  }
  const cards: Card[] = [
    {
      key: 'income',
      label: '本月收入',
      value: monthIncome,
      prev: prevMonthIncome,
      positiveWhenUp: true,
      icon: <ArrowDownLeft className="h-4 w-4" />,
      accent: 'emerald',
      bg: 'from-emerald-500/20 via-emerald-400/5 to-transparent',
      ring: 'ring-emerald-500/30'
    },
    {
      key: 'expense',
      label: '本月支出',
      value: monthExpense,
      prev: prevMonthExpense,
      positiveWhenUp: false,
      icon: <ArrowUpRight className="h-4 w-4" />,
      accent: 'rose',
      bg: 'from-rose-500/20 via-rose-400/5 to-transparent',
      ring: 'ring-rose-500/30'
    },
    {
      key: 'balance',
      label: '本月结余',
      value: monthBalance,
      prev: prevBalance,
      positiveWhenUp: true,
      icon: <TrendingUp className="h-4 w-4" />,
      accent: 'sky',
      bg: 'from-sky-500/20 via-sky-400/5 to-transparent',
      ring: 'ring-sky-500/30'
    },
    {
      key: 'count',
      label: '本月笔数',
      value: monthTxCount,
      prev: null,
      positiveWhenUp: true,
      icon: <TrendingDown className="h-4 w-4" />,
      accent: 'amber',
      bg: 'from-amber-500/20 via-amber-400/5 to-transparent',
      ring: 'ring-amber-500/30',
      isCount: true
    }
  ]

  return (
    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => {
        const delta = card.prev === null ? null : card.value - card.prev
        const pct =
          card.prev === null || card.prev === 0
            ? null
            : ((card.value - card.prev) / Math.abs(card.prev)) * 100
        const trendPositive =
          delta === null ? null : card.positiveWhenUp ? delta >= 0 : delta <= 0
        return (
          <div
            key={card.key}
            className={`group relative overflow-hidden rounded-xl border bg-card p-4 shadow-sm ring-1 transition-all hover:-translate-y-0.5 hover:shadow-md ${card.ring}`}
          >
            <div
              className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${card.bg}`}
              aria-hidden
            />
            <div className="pointer-events-none absolute -right-8 -top-8 h-20 w-20 rounded-full bg-white/10 blur-2xl" aria-hidden />

            <div className="relative flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
                <span className={`inline-flex h-6 w-6 items-center justify-center rounded-md bg-${card.accent}-500/20 text-${card.accent}-600 dark:text-${card.accent}-400`}>
                  {card.icon}
                </span>
                {card.label}
              </span>
              {trendPositive !== null && pct !== null ? (
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${
                    trendPositive
                      ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                      : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                  }`}
                >
                  {pct >= 0 ? '+' : ''}
                  {pct.toFixed(1)}%
                </span>
              ) : null}
            </div>

            {card.isCount ? (
              <div className="relative mt-2 font-mono text-3xl font-bold tabular-nums leading-tight">
                {(card.value as number).toLocaleString('zh-CN')}
                <span className="ml-1 text-sm font-normal text-muted-foreground">笔</span>
              </div>
            ) : (
              <Amount
                value={card.value as number}
                currency={currency}
                showCurrency
                bold
                size="3xl"
                tone={
                  card.key === 'balance'
                    ? (card.value as number) >= 0
                      ? 'positive'
                      : 'negative'
                    : 'default'
                }
                className="relative mt-2 block leading-tight"
              />
            )}

            {card.prev !== null ? (
              <div className="relative mt-1 text-[11px] text-muted-foreground">
                上月{' '}
                {card.isCount ? (
                  <span className="font-mono tabular-nums">
                    {(card.prev as number).toLocaleString('zh-CN')}
                  </span>
                ) : (
                  <Amount
                    value={card.prev as number}
                    currency={currency}
                    size="xs"
                    tone="muted"
                    className="inline"
                  />
                )}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
