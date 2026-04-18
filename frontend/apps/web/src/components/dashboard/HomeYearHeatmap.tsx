import { useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@beecount/ui'

import type { WorkspaceAnalyticsSeriesItem } from '@beecount/api-client'
import { Amount } from '@beecount/web-features'

interface Props {
  /** year scope 的 series，bucket 是 YYYY-MM。 */
  yearSeries?: WorkspaceAnalyticsSeriesItem[]
  currency?: string
}

/**
 * 12 个月支出热力条：用当月支出的相对大小染色，把"今年哪几个月花得最多"
 * 一眼能看出。对比之下 MonthlyTrendBars 只展示最近 6 期，这里补齐整年。
 */
export function HomeYearHeatmap({ yearSeries, currency = 'CNY' }: Props) {
  const data = useMemo(() => {
    const year = new Date().getFullYear()
    const byBucket = new Map<string, { income: number; expense: number }>()
    for (const it of yearSeries || []) {
      byBucket.set(it.bucket, { income: it.income || 0, expense: it.expense || 0 })
    }
    const rows = []
    let maxExpense = 0
    for (let m = 0; m < 12; m += 1) {
      const key = `${year}-${String(m + 1).padStart(2, '0')}`
      const rec = byBucket.get(key) || { income: 0, expense: 0 }
      if (rec.expense > maxExpense) maxExpense = rec.expense
      rows.push({
        monthIndex: m,
        monthLabel: `${m + 1}月`,
        income: rec.income,
        expense: rec.expense,
        balance: rec.income - rec.expense
      })
    }
    return { rows, maxExpense, year }
  }, [yearSeries])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-end justify-between">
        <CardTitle className="text-base">{data.year} 年月度支出热力</CardTitle>
        <span className="text-[11px] text-muted-foreground">颜色越深 = 支出越大</span>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-6 gap-2 sm:grid-cols-12">
          {data.rows.map((row) => {
            const pct = data.maxExpense > 0 ? row.expense / data.maxExpense : 0
            // 色温：从透明到饱和玫红
            const bg =
              pct === 0
                ? 'rgba(148,163,184,0.12)' // 无数据月
                : `hsl(0 72% 60% / ${Math.max(0.18, pct).toFixed(2)})`
            const isCurrent =
              row.monthIndex === new Date().getMonth() &&
              data.year === new Date().getFullYear()
            return (
              <div
                key={row.monthIndex}
                className={`group relative flex aspect-square flex-col items-center justify-center rounded-lg border ${
                  isCurrent ? 'border-primary ring-1 ring-primary/40' : 'border-border/40'
                }`}
                style={{ background: bg }}
                title={`${row.monthLabel} · 支出 ${row.expense.toFixed(2)}`}
              >
                <span
                  className={`text-[11px] font-semibold ${
                    pct > 0.5 ? 'text-white' : 'text-foreground'
                  }`}
                >
                  {row.monthLabel}
                </span>
                {row.expense > 0 ? (
                  <Amount
                    value={row.expense}
                    currency={currency}
                    size="xs"
                    bold
                    className={`mt-0.5 leading-none ${
                      pct > 0.5 ? 'text-white' : 'text-muted-foreground'
                    }`}
                  />
                ) : (
                  <span className="mt-0.5 text-[10px] text-muted-foreground">—</span>
                )}

                {/* hover 时详情 tooltip（纯 CSS，避免额外依赖） */}
                <div className="pointer-events-none absolute -top-1 left-1/2 z-10 hidden w-max -translate-x-1/2 -translate-y-full rounded-md border border-border/60 bg-popover px-2 py-1 text-[11px] shadow-lg group-hover:block">
                  <div className="font-semibold">{row.monthLabel}</div>
                  <div className="text-emerald-600 dark:text-emerald-400">
                    收入 {row.income.toFixed(2)}
                  </div>
                  <div className="text-rose-600 dark:text-rose-400">
                    支出 {row.expense.toFixed(2)}
                  </div>
                  <div>
                    结余{' '}
                    <span
                      className={
                        row.balance >= 0
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-rose-600 dark:text-rose-400'
                      }
                    >
                      {row.balance.toFixed(2)}
                    </span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
