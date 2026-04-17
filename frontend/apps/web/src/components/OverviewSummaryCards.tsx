import { Card, CardContent } from '@beecount/ui'
import type { ReadLedger } from '@beecount/api-client'

interface Props {
  ledgers: ReadLedger[]
  currency: string
}

/** 总览顶部统计卡片。聚合所有账本的数据做"一眼看全"的数字+视觉层次。 */
export function OverviewSummaryCards({ ledgers, currency }: Props) {
  const totals = ledgers.reduce(
    (acc, l) => {
      acc.income += l.income_total
      acc.expense += l.expense_total
      acc.balance += l.balance
      acc.txCount += l.transaction_count
      return acc
    },
    { income: 0, expense: 0, balance: 0, txCount: 0 }
  )

  const fmt = (v: number) =>
    `${currency} ${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

  const cards: { label: string; value: string; hint: string; tint: string }[] = [
    {
      label: '净值 (收入−支出)',
      value: fmt(totals.balance),
      hint: '全部账本合计',
      tint:
        totals.balance >= 0
          ? 'from-emerald-400/25 to-emerald-400/5 text-emerald-700 dark:text-emerald-300'
          : 'from-rose-400/25 to-rose-400/5 text-rose-700 dark:text-rose-300'
    },
    {
      label: '总支出',
      value: fmt(totals.expense),
      hint: `${totals.txCount} 笔交易`,
      tint: 'from-rose-400/25 to-rose-400/5 text-rose-700 dark:text-rose-300'
    },
    {
      label: '总收入',
      value: fmt(totals.income),
      hint: `${ledgers.length} 个账本`,
      tint: 'from-emerald-400/25 to-emerald-400/5 text-emerald-700 dark:text-emerald-300'
    },
    {
      label: '账本',
      value: String(ledgers.length),
      hint: ledgers.length > 0 ? '点击卡片切换' : '尚未创建账本',
      tint: 'from-sky-400/25 to-sky-400/5 text-sky-700 dark:text-sky-300'
    }
  ]

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <Card
          key={card.label}
          className="relative overflow-hidden border-border/60"
        >
          <div
            className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${card.tint} opacity-70`}
            aria-hidden
          />
          <CardContent className="relative pt-5">
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {card.label}
            </div>
            <div className="mt-1.5 text-2xl font-semibold tracking-tight">{card.value}</div>
            <div className="mt-1 text-xs text-muted-foreground">{card.hint}</div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
