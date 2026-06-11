import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { WorkspaceAccount } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useLocale, useT } from '@beecount/ui'

import { formatCompactTick } from '../../i18n/format'

interface Props {
  accounts: WorkspaceAccount[]
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
  const { locale } = useLocale()
  const chinese = locale.startsWith('zh')
  // 按类型**带符号**累加(与 assetAggregation 的负债符号口径一致:欠款为负、
  // 溢缴为正,透支资产为负),饼图分段才对类型合计取 abs 当体量 —— 绝不逐账户
  // abs,否则同类型内正负互抵的账户会被虚增。
  const totals = new Map<string, number>()
  for (const a of accounts) {
    const key = a.account_type || 'other'
    // 用 balance(= initial_balance + 净流水)而非 initial_balance。用户常常
    // 把初始余额留 0,靠日常记账累积现金/微信/支付宝等账户流水 —— 若只看
    // initial_balance,donut 会全空;资产页走 balance 兜底所以正常。
    const raw = typeof a.balance === 'number' && a.balance !== null
      ? a.balance
      : a.initial_balance ?? 0
    totals.set(key, (totals.get(key) || 0) + raw)
  }
  const allRows = Array.from(totals.entries())
    .map(([type, signed]) => ({
      type,
      signed,
      value: Math.abs(signed),
      label: TYPE_META[type] ? t(`accountType.${type}` as never) : type,
      color: TYPE_META[type]?.color || '#94a3b8',
      group: TYPE_META[type]?.group || 'asset'
    }))
  // 「资产构成」扇区/图例只含资产类:负债(信用卡/贷款)不进饼图,只在中心脚注体现。
  // 与 App 端 asset_composition_chart 一致(资产构成 = 纯资产,不含负债)。
  const data = allRows
    // 只含「正余额」资产:负债(信用卡/贷款)与透支为负的资产都不进扇区/图例。
    .filter((d) => d.signed > 0 && d.group === 'asset')
    .sort((a, b) => b.value - a.value)
  // 百分比分母用「展示项之和」而非带符号的 totalAsset —— 否则有透支资产(signed<0)时
  // totalAsset 被负值压低,会让单项百分比虚高甚至 >100%。
  const shownAssetTotal = data.reduce((s, d) => s + d.value, 0)

  // 中心数字与 App 口径一致:总资产 = 资产类带符号合计;负债脚注 = |负债类带符号合计|。
  const totalAsset = allRows.filter((d) => d.group === 'asset').reduce((s, d) => s + d.signed, 0)
  const totalLiability = Math.abs(
    allRows.filter((d) => d.group === 'liability').reduce((s, d) => s + d.signed, 0)
  )

  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
  // 图例金额用压缩格式(如 128.5万),避免大额撑破固定宽列;精确值在 hover 扇区的 Tooltip。
  const compact = (v: number) => formatCompactTick(v, { chinese, wanUnit: t('common.unit.10k') })

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
                // 分母用展示项之和(见上方 shownAssetTotal 注释),保证各项百分比合计 = 100%。
                const pct = shownAssetTotal > 0 ? (d.value / shownAssetTotal) * 100 : 0
                return (
                  <li key={d.type} className="flex items-center gap-2 text-sm">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: d.color }} />
                    <span className="flex-1 truncate">{d.label}</span>
                    <span className="font-mono tabular-nums text-xs text-muted-foreground">
                      {pct.toFixed(1)}%
                    </span>
                    <span className="w-20 text-right font-mono tabular-nums">{compact(d.value)}</span>
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
