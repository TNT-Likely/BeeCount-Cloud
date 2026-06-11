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
 */
export function NetWorthTrend({ data }: { data: NetWorthHistory | null }) {
  const t = useT()
  const { locale } = useLocale()
  const chinese = locale.startsWith('zh')
  const [line, setLine] = useState<Line>('net_worth')

  const slice = useMemo(() => (data?.series ?? []).slice(-12), [data])
  const xTick = (b: string) => { const p = b.split('-'); return p.length >= 2 ? p[1] : b }

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-base">{t('home.netWorthTrend.title')}</CardTitle>
        <div className="flex gap-1">
          {(['net_worth', 'assets', 'liabilities'] as Line[]).map((ln) => (
            <button key={ln} onClick={() => setLine(ln)}
              className={`rounded-full px-2 py-0.5 text-[11px] ${line === ln
                ? 'bg-primary/15 text-primary' : 'text-muted-foreground'}`}>
              {t(`home.netWorthTrend.${ln}`)}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {slice.length < 2 ? (
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
        )}
      </CardContent>
    </Card>
  )
}
