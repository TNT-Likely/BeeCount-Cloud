import { useMemo } from 'react'
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

// 固定取最近 N 个月。原来有 6 / 12 / 24 期切换,但 server.series 通常只
// 返当年数据,切到 24 期没新内容,反而让用户疑惑"按钮没用",直接拿掉。
// 12 是"完整年度"的天然窗口,看得到季节性又不拥挤。
const TREND_WINDOW = 12

/**
 * 月度收支走势 — 最近 12 期柱图,叠加净额折线,直观看出某个月是赚到了还是
 * 入不敷出。
 *
 * 数据源是后端已经聚合好的 `analyticsData.series`(YYYY-MM bucket),前端只
 * 切片 + 计算 balance(不依赖 server.balance 字段,保险用 income-expense 算)。
 */
export function MonthlyTrendBars({ data }: Props) {
  const t = useT()

  const slice = useMemo(() => {
    const tail = data.slice(-TREND_WINDOW)
    return tail.map((d) => ({
      ...d,
      balance: Number.isFinite(d.balance) ? d.balance : (d.income ?? 0) - (d.expense ?? 0),
    }))
  }, [data])

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })

  // 12 期 x 轴 label 直接用月份末位(MM),完整 bucket 由 tooltip 提供
  const xTickFormatter = (bucket: string): string => {
    const parts = bucket.split('-')
    if (parts.length >= 2) return parts.slice(1).join('-')
    return bucket
  }

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">{t('home.trendBars.title')}</CardTitle>
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
                  interval={0}
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
