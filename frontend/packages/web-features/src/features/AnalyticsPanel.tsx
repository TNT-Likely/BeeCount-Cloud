import { useMemo } from 'react'

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'
import type { WorkspaceAnalytics } from '@beecount/api-client'

import { formatAmountCny } from '../format'

type AnalyticsPanelProps = {
  data: WorkspaceAnalytics | null
}

export function AnalyticsPanel({ data }: AnalyticsPanelProps) {
  const t = useT()

  const summary = data?.summary || {
    transaction_count: 0,
    income_total: 0,
    expense_total: 0,
    balance: 0
  }
  const series = data?.series || []
  const categoryRanks = data?.category_ranks || []
  const hasSeries = series.length > 0
  const chartWidth = 960
  const chartHeight = 300
  const padding = 24
  const plotWidth = chartWidth - padding * 2
  const plotHeight = chartHeight - padding * 2
  const allValues = series.flatMap((item) => [item.income, item.expense, item.balance])
  const minValue = allValues.length > 0 ? Math.min(...allValues) : 0
  const maxValue = allValues.length > 0 ? Math.max(...allValues) : 0
  const yMin = Math.min(minValue, 0)
  const yMax = Math.max(maxValue, 0)
  const ySpan = yMax - yMin || 1

  const linePath = (key: 'income' | 'expense' | 'balance') => {
    if (series.length === 0) return ''
    return series
      .map((point, index) => {
        const x =
          series.length <= 1
            ? padding + plotWidth / 2
            : padding + (index / (series.length - 1)) * plotWidth
        const y = padding + ((yMax - point[key]) / ySpan) * plotHeight
        const prefix = index === 0 ? 'M' : 'L'
        return `${prefix}${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(' ')
  }

  const axisTicks = [yMax, yMax - ySpan / 2, yMin]

  const cards = useMemo(
    () => [
      {
        key: 'count',
        label: t('analytics.summary.count'),
        value: `${summary.transaction_count}`
      },
      {
        key: 'income',
        label: t('analytics.summary.income'),
        value: formatAmountCny(summary.income_total)
      },
      {
        key: 'expense',
        label: t('analytics.summary.expense'),
        value: formatAmountCny(summary.expense_total)
      },
      {
        key: 'balance',
        label: t('analytics.summary.balance'),
        value: formatAmountCny(summary.balance)
      }
    ],
    [summary.balance, summary.expense_total, summary.income_total, summary.transaction_count, t]
  )

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((item) => (
          <Card key={item.key} className="bc-panel">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">{item.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-semibold tracking-tight">{item.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="bc-panel">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>{t('analytics.trend.title')}</CardTitle>
          {data?.range?.period ? <Badge variant="secondary">{data.range.period}</Badge> : null}
        </CardHeader>
        <CardContent>
          {hasSeries ? (
            <div className="space-y-3">
              <div className="overflow-x-auto">
                <svg
                  className="h-[320px] min-w-[760px] w-full rounded-md border border-border/70 bg-muted/20"
                  viewBox={`0 0 ${chartWidth} ${chartHeight}`}
                  preserveAspectRatio="none"
                >
                  <line
                    x1={padding}
                    y1={padding + ((yMax - 0) / ySpan) * plotHeight}
                    x2={padding + plotWidth}
                    y2={padding + ((yMax - 0) / ySpan) * plotHeight}
                    stroke="hsl(var(--border))"
                    strokeWidth="1"
                    strokeDasharray="4 3"
                  />
                  {axisTicks.map((tick) => {
                    const y = padding + ((yMax - tick) / ySpan) * plotHeight
                    return (
                      <g key={tick}>
                        <line
                          x1={padding}
                          y1={y}
                          x2={padding + plotWidth}
                          y2={y}
                          stroke="hsl(var(--border))"
                          strokeWidth="1"
                          opacity="0.35"
                        />
                        <text
                          x={6}
                          y={y + 4}
                          fill="hsl(var(--muted-foreground))"
                          fontSize="11"
                        >
                          {formatAmountCny(tick)}
                        </text>
                      </g>
                    )
                  })}
                  <path d={linePath('income')} fill="none" stroke="hsl(142 71% 45%)" strokeWidth="2.5" />
                  <path d={linePath('expense')} fill="none" stroke="hsl(0 84% 60%)" strokeWidth="2.5" />
                  <path d={linePath('balance')} fill="none" stroke="hsl(var(--primary))" strokeWidth="2.5" />
                </svg>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded-md border border-border/70 bg-background px-3 py-2 text-sm">
                  <p className="text-xs text-muted-foreground">{t('analytics.summary.income')}</p>
                  <p className="font-medium text-[hsl(142_71%_45%)]">{formatAmountCny(summary.income_total)}</p>
                </div>
                <div className="rounded-md border border-border/70 bg-background px-3 py-2 text-sm">
                  <p className="text-xs text-muted-foreground">{t('analytics.summary.expense')}</p>
                  <p className="font-medium text-[hsl(0_84%_60%)]">{formatAmountCny(summary.expense_total)}</p>
                </div>
                <div className="rounded-md border border-border/70 bg-background px-3 py-2 text-sm">
                  <p className="text-xs text-muted-foreground">{t('analytics.summary.balance')}</p>
                  <p className="font-medium text-primary">{formatAmountCny(summary.balance)}</p>
                </div>
              </div>
              <div className="overflow-x-auto">
                <div className="grid min-w-[760px] grid-cols-12 gap-2 text-xs text-muted-foreground">
                  {series.map((point) => (
                    <div key={point.bucket} className="truncate rounded border border-border/60 bg-background px-2 py-1">
                      {point.bucket}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-md border border-dashed border-border/80 py-12 text-center text-sm text-muted-foreground">
              {t('table.empty')}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardHeader>
          <CardTitle>{t('analytics.rank.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('analytics.rank.category')}</TableHead>
                  <TableHead>{t('analytics.rank.total')}</TableHead>
                  <TableHead>{t('analytics.rank.count')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {categoryRanks.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={3} className="py-10 text-center text-sm text-muted-foreground">
                      {t('table.empty')}
                    </TableCell>
                  </TableRow>
                ) : null}
                {categoryRanks.map((item) => (
                  <TableRow key={item.category_name} className="odd:bg-muted/20">
                    <TableCell>{item.category_name}</TableCell>
                    <TableCell>{formatAmountCny(item.total)}</TableCell>
                    <TableCell>{item.tx_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
