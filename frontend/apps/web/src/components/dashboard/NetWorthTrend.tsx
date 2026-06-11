import { useMemo, useState } from 'react'
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle, useLocale, useT } from '@beecount/ui'
import type { NetWorthHistory } from '@beecount/api-client'

import { formatCompactTick } from '../../i18n/format'

type Line = 'net_worth' | 'assets' | 'liabilities'

/**
 * 净资产趋势 — 最近 12 期回算净值序列的单线面积图,顶部可在净资产 / 总资产 /
 * 总负债三条线之间切换。数据源是后端 net-worth-history 端点(回算每月累积),
 * 前端只切片末 12 期。多币种账本下历史净值为各币种原值相加(未折算),命中时
 * 在卡片底部脚注提示。
 *
 * embedded=true:不包外层 Card(供嵌入折算卡 CardContent 内,避免卡中卡),
 * 标题+线切换那行降级为普通 div;embedded=false(默认)维持自带 Card 的现状。
 */
export function NetWorthTrend({
  data,
  embedded = false
}: {
  data: NetWorthHistory | null
  embedded?: boolean
}) {
  const t = useT()
  const { locale } = useLocale()
  const chinese = locale.startsWith('zh')
  const [line, setLine] = useState<Line>('net_worth')

  const slice = useMemo(() => (data?.series ?? []).slice(-12), [data])
  const xTick = (b: string) => { const p = b.split('-'); return p.length >= 2 ? p[1] : b }

  // 标题 + 走势线切换。embedded 下用普通 div(等价 CardHeader 的 flex 布局),
  // 默认下由外层 CardHeader/CardTitle 套壳,故这里仅给标题文字样式、不再套标签。
  const lineToggle = (
    <div className="flex gap-1">
      {(['net_worth', 'assets', 'liabilities'] as Line[]).map((ln) => (
        <button key={ln} type="button" onClick={() => setLine(ln)}
          className={`rounded-full px-2 py-0.5 text-[11px] ${line === ln
            ? 'bg-primary/15 text-primary' : 'text-muted-foreground'}`}>
          {t(`home.netWorthTrend.${ln}`)}
        </button>
      ))}
    </div>
  )

  const header = embedded ? (
    <div className="flex flex-row items-center justify-between gap-2">
      <span className="text-base font-semibold">{t('home.netWorthTrend.title')}</span>
      {lineToggle}
    </div>
  ) : (
    lineToggle
  )

  const body = slice.length < 2 ? (
    <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
      {t('home.netWorthTrend.empty')}
    </div>
  ) : (
    <>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={slice} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="bucket" tickFormatter={xTick} interval={0}
              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
              stroke="hsl(var(--border))" />
            <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
              stroke="hsl(var(--border))"
              tickFormatter={(v) => formatCompactTick(v, { chinese, wanUnit: t('common.unit.10k') })} />
            <Tooltip contentStyle={{ background: 'hsl(var(--popover))',
              border: '1px solid hsl(var(--border))', borderRadius: 6, fontSize: 12 }}
              formatter={((v: number) => [v.toLocaleString(undefined,
                { maximumFractionDigits: 0 }), t(`home.netWorthTrend.${line}`)]) as unknown as never} />
            <Area type="monotone" dataKey={line} stroke="hsl(var(--primary))" strokeWidth={2}
              fill="hsl(var(--primary) / 0.12)" dot={false} activeDot={{ r: 4 }} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {data?.multi_currency ? (
        <p className="pt-2 text-[11px] text-muted-foreground">{t('home.netWorthTrend.note')}</p>
      ) : null}
    </>
  )

  if (embedded) {
    return <div className="space-y-2">{header}{body}</div>
  }

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-base">{t('home.netWorthTrend.title')}</CardTitle>
        {header}
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  )
}
