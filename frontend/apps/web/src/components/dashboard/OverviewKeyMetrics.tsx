import { ArrowDown, ArrowUp, ReceiptText, Wallet } from 'lucide-react'

import type { WorkspaceAnalyticsSummary } from '@beecount/api-client'

interface Props {
  summary?: WorkspaceAnalyticsSummary
}

/**
 * 4 张关键指标卡片：今年收入 / 今年支出 / 净流 / 交易笔数。
 * 每张卡片带不同色系的渐变边框 + 柔光。数字大号，图标与颜色对应语义。
 */
export function OverviewKeyMetrics({ summary }: Props) {
  const income = summary?.income_total ?? 0
  const expense = summary?.expense_total ?? 0
  const balance = summary?.balance ?? income - expense
  const txCount = summary?.transaction_count ?? 0

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const cards = [
    {
      key: 'income',
      label: '今年收入',
      value: fmt(income),
      icon: <ArrowDown className="h-4 w-4 text-income" />,
      glow: 'from-income/30 via-income/5 to-transparent',
      border: 'border-income/30'
    },
    {
      key: 'expense',
      label: '今年支出',
      value: fmt(expense),
      icon: <ArrowUp className="h-4 w-4 text-expense" />,
      glow: 'from-expense/30 via-expense/5 to-transparent',
      border: 'border-expense/30'
    },
    {
      key: 'balance',
      label: '净流',
      value: fmt(balance),
      icon: <Wallet className="h-4 w-4 text-sky-500" />,
      glow: 'from-sky-400/30 via-sky-400/5 to-transparent',
      border: 'border-sky-400/30',
      highlight: balance >= 0 ? 'text-income' : 'text-expense'
    },
    {
      key: 'count',
      label: '交易笔数',
      value: txCount.toString(),
      icon: <ReceiptText className="h-4 w-4 text-amber-500" />,
      glow: 'from-amber-400/30 via-amber-400/5 to-transparent',
      border: 'border-amber-400/30'
    }
  ] as const

  return (
    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <div
          key={card.key}
          className={`relative overflow-hidden rounded-xl border ${card.border} bg-card p-4`}
        >
          <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${card.glow}`} />
          <div className="relative flex items-start justify-between">
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                {card.icon}
                {card.label}
              </div>
              <div
                className={`font-mono text-2xl font-bold tabular-nums ${
                  'highlight' in card && card.highlight ? card.highlight : 'text-foreground'
                }`}
              >
                {card.value}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
