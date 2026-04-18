import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { ReadAccount } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'

interface Props {
  accounts: ReadAccount[]
}

// 与 AccountsPanel 里的 TRADABLE / VALUATION 分组 + 颜色一致。
const TYPE_META: Record<string, { color: string; group: 'asset' | 'liability' }> = {
  cash: { color: '#10b981', group: 'asset' },
  bank_card: { color: '#3b82f6', group: 'asset' },
  credit_card: { color: '#ef4444', group: 'liability' },
  alipay: { color: '#06b6d4', group: 'asset' },
  wechat: { color: '#22c55e', group: 'asset' },
  other: { color: '#64748b', group: 'asset' },
  real_estate: { color: '#8b5cf6', group: 'asset' },
  vehicle: { color: '#f59e0b', group: 'asset' },
  investment: { color: '#ec4899', group: 'asset' },
  insurance: { color: '#14b8a6', group: 'asset' },
  social_fund: { color: '#84cc16', group: 'asset' },
  loan: { color: '#dc2626', group: 'liability' }
}

export function AssetCompositionDonut({ accounts }: Props) {
  const t = useT()
  const totals = new Map<string, number>()
  for (const a of accounts) {
    const key = a.account_type || 'other'
    const amount = Math.abs(a.initial_balance ?? 0)
    totals.set(key, (totals.get(key) || 0) + amount)
  }
  const data = Array.from(totals.entries())
    .map(([type, value]) => ({
      type,
      value,
      label: TYPE_META[type] ? t(`accountType.${type}` as never) : type,
      color: TYPE_META[type]?.color || '#94a3b8',
      group: TYPE_META[type]?.group || 'asset'
    }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value)

  const totalAsset = data.filter((d) => d.group === 'asset').reduce((s, d) => s + d.value, 0)
  const totalLiability = data.filter((d) => d.group === 'liability').reduce((s, d) => s + d.value, 0)

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">{t('home.assetComp.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
            {t('home.assetComp.empty')}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-[200px_1fr]">
            <div className="relative h-48">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={data}
                    dataKey="value"
                    nameKey="label"
                    innerRadius={52}
                    outerRadius={80}
                    paddingAngle={2}
                    strokeWidth={2}
                    stroke="hsl(var(--background))"
                  >
                    {data.map((d) => (
                      <Cell key={d.type} fill={d.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: 'hsl(var(--popover))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: 6,
                      fontSize: 12
                    }}
                    formatter={((v: number) => fmt(v)) as unknown as never}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{t('home.assetComp.totalAsset')}</div>
                <div className="text-sm font-bold">{fmt(totalAsset)}</div>
                {totalLiability > 0 ? (
                  <div className="mt-0.5 text-[10px] text-rose-500">{t('home.assetComp.liability').replace('{value}', fmt(totalLiability))}</div>
                ) : null}
              </div>
            </div>
            <ul className="space-y-1.5">
              {data.map((d) => {
                const total = totalAsset + totalLiability
                const pct = total > 0 ? (d.value / total) * 100 : 0
                return (
                  <li key={d.type} className="flex items-center gap-2 text-sm">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: d.color }} />
                    <span className="flex-1 truncate">{d.label}</span>
                    <span className="font-mono tabular-nums text-xs text-muted-foreground">
                      {pct.toFixed(1)}%
                    </span>
                    <span className="w-20 text-right font-mono tabular-nums">{fmt(d.value)}</span>
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
