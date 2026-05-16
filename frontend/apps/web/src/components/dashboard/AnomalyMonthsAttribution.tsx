import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import type { WorkspaceAnalyticsAnomalyMonth } from '@beecount/api-client'
import { Amount } from '@beecount/web-features'

interface Props {
  /** server `/workspace/analytics?scope=year` 返回的 anomaly_months。 */
  anomalyMonths: WorkspaceAnalyticsAnomalyMonth[]
  /** 月份够不够算 baseline。已发生月份 < 3 时传 false → 显示"数据不足"。 */
  hasEnoughMonths: boolean
  currency?: string
}

/**
 * 异常月份归因卡片 —— 算法见 .docs/dashboard-anomaly-budget/plan.md §2.
 *
 * 解决"我想看今年哪几个月花得多 + 为什么超"。每行一个异常月:
 *   - 月份 + 该月支出 + "比月均 X 高 N%"
 *   - 主因 chip:`购物 800(超月均 5×)` / `教育 223(本月独有)`
 *
 * 状态:
 *   - anomalyMonths.length > 0:正常列出
 *   - empty 但 hasEnoughMonths:显示"今年没有明显异常 ✓"
 *   - empty 且 !hasEnoughMonths:显示"数据不足"
 */
export function AnomalyMonthsAttribution({
  anomalyMonths,
  hasEnoughMonths,
  currency = 'CNY'
}: Props) {
  return (
    <Card className="bc-panel overflow-hidden">
      <AnomalyMonthsBody
        anomalyMonths={anomalyMonths}
        hasEnoughMonths={hasEnoughMonths}
        currency={currency}
        withHeader
      />
    </Card>
  )
}

/**
 * 异常归因内容(header + 列表)。可独立嵌入 popover。`withHeader` 控制
 * 是否渲染 CardHeader/CardContent 包装。
 */
export function AnomalyMonthsBody({
  anomalyMonths,
  hasEnoughMonths,
  currency = 'CNY',
  withHeader = false,
}: Props & { withHeader?: boolean }) {
  const t = useT()

  const empty = anomalyMonths.length === 0
  const emptyMsg = hasEnoughMonths
    ? t('home.anomaly.empty')
    : t('home.anomaly.insufficient')

  // "2026-05" → "5"
  const monthNum = (bucket: string) => {
    const parts = bucket.split('-')
    if (parts.length < 2) return bucket
    const m = parseInt(parts[1], 10)
    return Number.isFinite(m) ? String(m) : parts[1]
  }
  const fmtAmount = (v: number) =>
    v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  const fmtPct = (v: number) => `${Math.round(v * 100)}%`
  const fmtMult = (v: number) => {
    if (v >= 10) return Math.round(v).toString()
    return v.toFixed(1).replace(/\.0$/, '')
  }

  const header = (
    <div className="flex flex-row items-end justify-between gap-2">
      <CardTitle className="text-base">{t('home.anomaly.title')}</CardTitle>
      <span className="text-[11px] text-muted-foreground">
        {t('home.anomaly.hint')}
      </span>
    </div>
  )

  const body = empty ? (
    <div className="flex h-24 items-center justify-center text-xs text-muted-foreground">
      {emptyMsg}
    </div>
  ) : (
    <ul className="space-y-3">
      {anomalyMonths.map((m) => (
        <li
          key={m.bucket}
          className="rounded-lg border border-border/40 bg-muted/20 px-3 py-2.5"
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="inline-flex items-baseline gap-2">
              <span className="text-sm font-semibold">
                {t('home.anomaly.monthLabel').replace(
                  '{month}',
                  monthNum(m.bucket)
                )}
              </span>
              <Amount
                value={m.expense}
                currency={currency}
                size="sm"
                tone="negative"
                bold
                className="inline"
              />
            </span>
            <span className="text-[11px] text-muted-foreground">
              {t('home.anomaly.deviation')
                .replace('{baseline}', fmtAmount(m.baseline))
                .replace('{pct}', fmtPct(m.deviation_pct))}
            </span>
          </div>
          {m.top_attributions.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {m.top_attributions.map((att, idx) => {
                const amount = fmtAmount(att.amount)
                let label: string
                if (att.multiplier == null) {
                  label = t('home.anomaly.attributionUnique')
                    .replace('{cat}', att.category_name)
                    .replace('{amount}', amount)
                } else if (att.multiplier >= 1.5) {
                  label = t('home.anomaly.attributionMultiplier')
                    .replace('{cat}', att.category_name)
                    .replace('{amount}', amount)
                    .replace('{mult}', fmtMult(att.multiplier))
                } else {
                  label = t('home.anomaly.attributionFlat')
                    .replace('{cat}', att.category_name)
                    .replace('{amount}', amount)
                }
                return (
                  <span
                    key={`${m.bucket}-${idx}`}
                    className="inline-flex items-center rounded-full bg-expense/10 px-2 py-0.5 text-[11px] text-expense"
                  >
                    {label}
                  </span>
                )
              })}
            </div>
          )}
        </li>
      ))}
    </ul>
  )

  if (withHeader) {
    return (
      <>
        <CardHeader>{header}</CardHeader>
        <CardContent>{body}</CardContent>
      </>
    )
  }
  return (
    <div className="space-y-3">
      {header}
      {body}
    </div>
  )
}
