import { useMemo, useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'

interface SeriesItem {
  bucket: string
  expense: number
  income: number
  balance: number
}

interface Props {
  data: SeriesItem[]
}

type RangeOption = 6 | 12 | 24

/**
 * 月度收支走势 — 支持切换 6 / 12 / 24 期,叠加一条净额折线,直观看出某个月
 * 是真的赚到了还是入不敷出。
 *
 * 数据源是后端已经聚合好的 `analyticsData.series`(YYYY-MM bucket),前端只
 * 切片 + 计算 balance(不依赖 server.balance 字段,保险用 income-expense 算)。
 */
export function MonthlyTrendBars({ data }: Props) {
  const t = useT()
  const [range, setRange] = useState<RangeOption>(12)

  const slice = useMemo(() => {
    // 取最近 N 期。balance 如果 server 没给就用 income - expense 算。
    const tail = data.slice(-range)
    return tail.map((d) => ({
      ...d,
      balance: Number.isFinite(d.balance) ? d.balance : (d.income ?? 0) - (d.expense ?? 0),
    }))
  }, [data, range])

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })

  // 12 / 24 期时 x 轴标签会挤 — 用月份末位作为 tick,完整 bucket 在 tooltip 看。
  const xTickFormatter = (bucket: string): string => {
    if (range <= 6) return bucket
    // YYYY-MM → 取 MM,或者把 YYYY 也截短
    const parts = bucket.split('-')
    if (parts.length >= 2) return parts.slice(1).join('-')
    return bucket
  }

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">{t('home.trendBars.title')}</CardTitle>
        <RangeToggle value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {slice.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
            {t('home.trendBars.empty')}
          </div>
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={slice} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                <XAxis
                  dataKey="bucket"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                  stroke="hsl(var(--border))"
                  tickFormatter={xTickFormatter}
                  interval={range >= 24 ? 1 : 0}
                />
                <YAxis
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                  stroke="hsl(var(--border))"
                  tickFormatter={(v) => (Math.abs(v) >= 10000 ? `${(v / 10000).toFixed(1)}${t('home.trendBars.10kUnit')}` : String(v))}
                />
                <Tooltip
                  contentStyle={{
                    background: 'hsl(var(--popover))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: 6,
                    fontSize: 12
                  }}
                  cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
                  formatter={((v: number, name: string) => {
                    const label =
                      name === 'income'
                        ? t('home.trendBars.income')
                        : name === 'expense'
                          ? t('home.trendBars.expense')
                          : name === 'balance'
                            ? t('home.trendBars.balance')
                            : name
                    return [fmt(v), label]
                  }) as unknown as never}
                />
                <Legend
                  iconType="circle"
                  wrapperStyle={{ fontSize: 11 }}
                  formatter={(v: string) =>
                    v === 'income'
                      ? t('home.trendBars.income')
                      : v === 'expense'
                        ? t('home.trendBars.expense')
                        : v === 'balance'
                          ? t('home.trendBars.balance')
                          : v
                  }
                />
                {/* income / expense 柱子用 token 跟随用户配色偏好。balance 折线
                    用 primary 色 — 跟主题色绑定,亮暗模式都看得清。 */}
                <Bar
                  dataKey="income"
                  fill="rgb(var(--income-rgb))"
                  radius={[4, 4, 0, 0]}
                />
                <Bar
                  dataKey="expense"
                  fill="rgb(var(--expense-rgb))"
                  radius={[4, 4, 0, 0]}
                />
                <Line
                  type="monotone"
                  dataKey="balance"
                  stroke="hsl(var(--primary))"
                  strokeWidth={2}
                  dot={{ fill: 'hsl(var(--primary))', r: 3 }}
                  activeDot={{ r: 5 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * 期数切换按钮组 — 6 / 12 / 24 三档,模仿 segmented control。
 * 6 期是"近半年快速一瞥",12 期是"完整年度",24 期是"两年趋势 / 同比"。
 */
function RangeToggle({
  value,
  onChange,
}: {
  value: RangeOption
  onChange: (v: RangeOption) => void
}) {
  const t = useT()
  const options: RangeOption[] = [6, 12, 24]
  return (
    <div className="inline-flex items-center rounded-md border border-border/60 bg-muted/40 p-0.5 text-[11px]">
      {options.map((opt) => {
        const active = opt === value
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            className={`rounded px-2.5 py-1 transition ${
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t('home.trendBars.range', { count: opt })}
          </button>
        )
      })}
    </div>
  )
}
