import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@beecount/ui'

interface SeriesItem {
  bucket: string
  expense: number
  income: number
  balance: number
}

interface Props {
  data: SeriesItem[]
}

export function MonthlyTrendBars({ data }: Props) {
  // 取最近 6 期。backend 的 bucket 已经是 YYYY-MM 或 YYYY-MM-DD。
  const slice = data.slice(-6)

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">近 6 期收支</CardTitle>
      </CardHeader>
      <CardContent>
        {slice.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
            暂无交易数据
          </div>
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={slice} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                <XAxis
                  dataKey="bucket"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                  stroke="hsl(var(--border))"
                />
                <YAxis
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                  stroke="hsl(var(--border))"
                  tickFormatter={(v) => (Math.abs(v) >= 10000 ? `${(v / 10000).toFixed(1)}万` : String(v))}
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
                    const label = name === 'income' ? '收入' : name === 'expense' ? '支出' : name
                    return [fmt(v), label]
                  }) as unknown as never}
                />
                <Legend
                  iconType="circle"
                  wrapperStyle={{ fontSize: 11 }}
                  formatter={(v: string) => (v === 'income' ? '收入' : '支出')}
                />
                <Bar dataKey="income" fill="#10b981" radius={[4, 4, 0, 0]} />
                <Bar dataKey="expense" fill="#ef4444" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
