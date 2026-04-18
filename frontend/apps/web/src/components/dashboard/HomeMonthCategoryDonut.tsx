import { useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'

import type { WorkspaceAnalyticsCategoryRank } from '@beecount/api-client'
import { Amount } from '@beecount/web-features'

interface Props {
  /** 本月支出类别排行（scope=month&metric=expense 返回的 category_ranks）。 */
  ranks: WorkspaceAnalyticsCategoryRank[]
  currency?: string
}

/**
 * 本月支出分类占比环。SVG conic-gradient 做分段，Top 5 各一段，之外合并为
 * "其他"。参考 `AccountsPanel.AssetsCompositionMini`，同样风格避免引入 recharts
 * 分段饼图的多余依赖。
 */
export function HomeMonthCategoryDonut({ ranks, currency = 'CNY' }: Props) {
  const t = useT()
  const otherLabel = t('home.monthDonut.other')
  const { slices, total } = useMemo(() => {
    const sorted = ranks
      .slice()
      .sort((a, b) => b.total - a.total)
      .filter((r) => r.total > 0)
    const top = sorted.slice(0, 5)
    const rest = sorted.slice(5)
    const restTotal = rest.reduce((s, r) => s + r.total, 0)
    const restCount = rest.reduce((s, r) => s + r.tx_count, 0)
    const all = top.map((r) => ({
      name: r.category_name || t('home.monthDonut.uncategorized'),
      total: r.total,
      count: r.tx_count
    }))
    if (restTotal > 0) {
      all.push({ name: otherLabel, total: restTotal, count: restCount })
    }
    const sum = all.reduce((s, r) => s + r.total, 0)
    return { slices: all, total: sum }
  }, [ranks, otherLabel, t])

  // 配色：前 5 用 BeeCount mobile 常用调色盘，"其他"用中性灰
  const PALETTE = ['#ef4444', '#f59e0b', '#3b82f6', '#10b981', '#a855f7']
  const OTHER_COLOR = '#94a3b8'

  const conic = useMemo(() => {
    if (total <= 0) return 'hsl(var(--muted))'
    let acc = 0
    const stops: string[] = []
    slices.forEach((s, i) => {
      const color = s.name === otherLabel ? OTHER_COLOR : PALETTE[i % PALETTE.length]
      const start = (acc / total) * 100
      acc += s.total
      const end = (acc / total) * 100
      stops.push(`${color} ${start.toFixed(3)}% ${end.toFixed(3)}%`)
    })
    return `conic-gradient(from -90deg, ${stops.join(',')})`
  }, [slices, total, otherLabel])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-end justify-between">
        <CardTitle className="text-base">{t('home.monthDonut.title')}</CardTitle>
        <span className="text-[11px] text-muted-foreground">
          {t('home.monthDonut.total')}{' '}
          <Amount
            value={total}
            currency={currency}
            size="xs"
            tone="negative"
            bold
            className="inline"
          />
        </span>
      </CardHeader>
      <CardContent>
        {slices.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
            {t('home.monthDonut.empty')}
          </div>
        ) : (
          <div className="flex items-center gap-5">
            <div className="relative h-40 w-40 shrink-0">
              <div
                className="absolute inset-0 rounded-full"
                style={{ background: conic }}
                aria-hidden
              />
              <div className="absolute inset-[18%] rounded-full bg-card" aria-hidden />
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {t('home.monthDonut.center')}
                </div>
                <Amount
                  value={total}
                  currency={currency}
                  size="sm"
                  bold
                  tone="negative"
                  className="mt-0.5"
                />
                <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {t('home.monthDonut.categoryCount').replace('{count}', String(slices.length))}
                </div>
              </div>
            </div>
            <ul className="min-w-0 flex-1 space-y-1.5">
              {slices.map((s, i) => {
                const color =
                  s.name === otherLabel ? OTHER_COLOR : PALETTE[i % PALETTE.length]
                const pct = total > 0 ? (s.total / total) * 100 : 0
                return (
                  <li key={`${s.name}-${i}`} className="flex items-center gap-2 text-xs">
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-sm"
                      style={{ background: color }}
                      aria-hidden
                    />
                    <span className="flex-1 truncate">{s.name}</span>
                    <span className="text-muted-foreground font-mono tabular-nums">
                      {pct.toFixed(1)}%
                    </span>
                    <Amount
                      value={s.total}
                      currency={currency}
                      size="xs"
                      className="w-20 text-right"
                    />
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
