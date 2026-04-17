import { Card, CardContent, CardHeader, CardTitle } from '@beecount/ui'

interface Rank {
  category_name: string
  total: number
  tx_count: number
}

type Variant = 'expense' | 'income'

interface Props {
  ranks: Rank[]
  variant?: Variant
  title?: string
  onClickCategory?: (name: string) => void
}

export function TopCategoriesList({ ranks, variant = 'expense', title, onClickCategory }: Props) {
  const top = ranks.slice(0, 5)
  const maxTotal = Math.max(1, ...top.map((r) => r.total))

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const isExpense = variant === 'expense'
  const barClass = isExpense
    ? 'bg-gradient-to-r from-rose-400 to-rose-600 group-hover:from-rose-500 group-hover:to-rose-700'
    : 'bg-gradient-to-r from-emerald-400 to-emerald-600 group-hover:from-emerald-500 group-hover:to-emerald-700'
  const defaultTitle = isExpense ? '支出 Top 5' : '收入 Top 5'
  const emptyLabel = isExpense ? '暂无支出数据' : '暂无收入数据'

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">{title || defaultTitle}</CardTitle>
      </CardHeader>
      <CardContent>
        {top.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
            {emptyLabel}
          </div>
        ) : (
          <ul className="space-y-2.5">
            {top.map((r, i) => {
              const pct = (r.total / maxTotal) * 100
              return (
                <li
                  key={r.category_name}
                  className={`group ${onClickCategory ? 'cursor-pointer' : ''}`}
                  onClick={() => onClickCategory?.(r.category_name)}
                >
                  <div className="flex items-center justify-between text-sm">
                    <span className="inline-flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-muted text-[10px] font-semibold">
                        {i + 1}
                      </span>
                      <span className="font-medium">{r.category_name || '(未分类)'}</span>
                      <span className="text-[11px] text-muted-foreground">{r.tx_count} 笔</span>
                    </span>
                    <span className="font-mono tabular-nums text-sm">{fmt(r.total)}</span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted/50">
                    <div
                      className={`h-full rounded-full transition-all ${barClass}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
