import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { WorkspaceAccount } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useLocale, useT } from '@beecount/ui'
import { convertPositiveAssetTotalsToBase } from '@beecount/web-features'
import { useMemo } from 'react'

import { usePrimaryCurrencyRates } from '../../hooks/usePrimaryCurrencyRates'
import { formatCompactTick } from '../../i18n/format'

interface Props {
  accounts: WorkspaceAccount[]
}

// 与 AccountsPanel 里的 TRADABLE / VALUATION 分组 + 颜色一致。
const TYPE_META: Record<string, { color: string; group: 'asset' | 'liability' }> = {
  cash: { color: '#10b981', group: 'asset' },
  bank_card: { color: '#3b82f6', group: 'asset' },
  credit_card_overpayment: { color: '#ef4444', group: 'asset' },
  credit_card: { color: '#ef4444', group: 'liability' },
  alipay: { color: '#06b6d4', group: 'asset' },
  wechat: { color: '#22c55e', group: 'asset' },
  other: { color: '#64748b', group: 'asset' },
  real_estate: { color: '#8b5cf6', group: 'asset' },
  vehicle: { color: '#f59e0b', group: 'asset' },
  investment: { color: '#ec4899', group: 'asset' },
  insurance: { color: '#14b8a6', group: 'asset' },
  social_fund: { color: '#84cc16', group: 'asset' },
  loan: { color: '#dc2626', group: 'liability' },
  // 资产构成包含定期存款(#):持有中本金作为独立资产类别,与账户余额不重叠
  // (本金创建时已自动从原账户扣出,不会重复计)。
  time_deposit: { color: '#0ea5e9', group: 'asset' }
}

export function AssetCompositionDonut({ accounts }: Props) {
  const t = useT()
  const { locale } = useLocale()
  const chinese = locale.startsWith('zh')
  // 跨币种折算(#104):账户是工作区全局的,可能混着 CNY/USD/SGD。旧实现直接把
  // 各账户 balance 按 1:1 裸加($1000 当 ¥1000,issue #104 的 11,500 就是这么来的)。
  // 现在统一走 assetAggregation 铁律:按币种切分 → 每币种类型小计 × 汇率折进
  // 主币种 → 缺失汇率的整币种剔除(missing 出脚注),绝不按 1 折入。
  const { effectiveBase, singleCurrency, needsBase, rates, rateOverrides, loading } =
    usePrimaryCurrencyRates(accounts)
  const { totals, missing, negativeAssets } = useMemo(
    () => convertPositiveAssetTotalsToBase(accounts, effectiveBase, rates, rateOverrides),
    [accounts, effectiveBase, rates, rateOverrides],
  )
  const allRows = Array.from(totals.entries())
    .map(([type, signed]) => ({
      type,
      signed,
      value: Math.abs(signed),
      label: type === 'credit_card_overpayment' ? t('accounts.credit.overpayment') : TYPE_META[type] ? t(`accountType.${type}` as never) : type,
      color: TYPE_META[type]?.color || '#94a3b8',
      group: TYPE_META[type]?.group || 'asset'
    }))
  // 扇区与分母都只包含正余额资产头寸，包括信用卡溢缴款。
  const data = allRows
    // 只含「正余额」资产:负债(信用卡/贷款)与透支为负的资产都不进扇区/图例。
    .filter((d) => d.signed > 0 && d.group === 'asset')
    .sort((a, b) => b.value - a.value)
  // 百分比分母用「展示项之和」而非带符号的 totalAsset —— 否则有透支资产(signed<0)时
  // totalAsset 被负值压低,会让单项百分比虚高甚至 >100%。
  const shownAssetTotal = data.reduce((s, d) => s + d.value, 0)

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
        {accounts.length === 0 ? (
          <div className="flex h-56 items-center justify-center text-xs text-muted-foreground">
            {t('home.assetComp.empty')}
          </div>
        ) : loading ? (
          <div className="flex h-56 items-center justify-center text-xs text-muted-foreground">
            {t('home.assetComp.loading')}
          </div>
        ) : needsBase ? (
          // 多币种但未设主币种:与资产页汇总卡同款引导,绝不猜币种按 1 折算。
          <div className="flex h-56 flex-col items-center justify-center gap-1 px-6 text-center">
            <p className="text-sm font-medium">{t('accounts.needBaseCurrency.title')}</p>
            <p className="text-xs text-muted-foreground">{t('accounts.needBaseCurrency.desc')}</p>
          </div>
        ) : data.length === 0 ? (
          <div className="flex h-56 items-center justify-center text-xs text-muted-foreground">
            {t('home.assetComp.empty')}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-[200px_1fr]">
            <div className="relative h-56">
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
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{t('accounts.positiveAssets')}</div>
                {/* 多币种折算视图:与资产页汇总卡同款 ≈ 前缀 + 目标币种码。 */}
                <div className="flex items-baseline gap-0.5">
                  {!singleCurrency ? (
                    <span className="font-mono text-[10px] text-muted-foreground">≈</span>
                  ) : null}
                  <div className="text-sm font-bold">{fmt(shownAssetTotal)}</div>
                  {!singleCurrency ? (
                    <span className="text-[10px] text-muted-foreground">{effectiveBase}</span>
                  ) : null}
                </div>
                {negativeAssets < 0 ? <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {t('accounts.negativeAssets', { value: fmt(negativeAssets) })}</div> : null}
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
        {/* 缺失汇率的币种被剔除而非 1:1 折入,必须告知用户少了哪些(与资产页同口径)。 */}
        {!singleCurrency && !needsBase && missing.length > 0 ? (
          <p className="mt-2 text-[10px] text-muted-foreground">
            {t('accounts.converted.missing', { currencies: missing.join(' / ') })}
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}
