import { useMemo } from 'react'
import { Handshake } from 'lucide-react'

import type { CounterpartyStat } from '@beecount/api-client'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { Amount } from '@beecount/web-features'

export type CounterpartyRankScope = 'month' | 'year' | 'all'

interface Props {
  /** 后端 /counterparty-stats 的 items。null = 还没拉到;[] = 拉到了但没借款往来。 */
  items: CounterpartyStat[] | null
  currency?: string
  scope: CounterpartyRankScope
  onScopeChange?: (scope: CounterpartyRankScope) => void
  /** 点「记借款」→ 跳借款页。没传则不显示该按钮。 */
  onManage?: () => void
}

/**
 * 交易对手维度排行(平帐视角)—— 跟 HomeMemberRanks 同一套卡片视觉。
 *
 * 口径(跟后端 counterparty-stats 一致):
 *   - 以借款(0023)的 counterparty 归组;
 *   - 每个对手汇总 借出(lent) / 借入(borrowed);
 *   - 净额 net = 借出 - 借入:
 *       >0 → 对方净欠我(我是净债权人)
 *       <0 → 我净欠对方
 *   这就是「借入借出对充」后的平帐结果 —— 同一人身上我借出 + 借进互相抵消,
 *   剩下的净额就是还该跟他算清的数目。
 */
const BAR_COLORS = [
  '#6366f1',
  '#0ea5e9',
  '#10b981',
  '#f59e0b',
  '#ef4444',
  '#8b5cf6',
  '#14b8a6',
  '#ec4899',
]

export function HomeCounterpartyRanks({
  items,
  currency = 'CNY',
  scope,
  onScopeChange,
  onManage,
}: Props) {
  const t = useT()

  const { list, maxAbsNet } = useMemo(() => {
    const rows = (items || [])
      .map((row) => ({
        raw: row,
        name: row.counterparty,
        lent: row.lent_total || 0,
        borrowed: row.borrowed_total || 0,
        net: row.net || 0,
        count: row.loan_count || 0,
      }))
      .filter((row) => row.lent > 0 || row.borrowed > 0)
      .sort((a, b) => Math.abs(b.net) - Math.abs(a.net))
      .slice(0, 8)
    const maxAbs = rows.reduce((m, row) => Math.max(m, Math.abs(row.net)), 0)
    return { list: rows, maxAbsNet: maxAbs }
  }, [items])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Handshake className="h-4 w-4 text-muted-foreground" aria-hidden />
          {t('home.counterparty.title')}
        </CardTitle>
        <div className="flex items-center gap-1">
          <div className="flex items-center gap-0.5 rounded-md bg-muted/50 p-0.5">
            {(
              [
                ['all', t('home.counterparty.scopeAll')],
                ['month', t('home.memberRanks.scopeMonth')],
                ['year', t('home.memberRanks.scopeYear')],
              ] as const
            ).map(([key, label]) => (
              <Button
                key={key}
                type="button"
                size="sm"
                variant={scope === key ? 'secondary' : 'ghost'}
                className="h-6 px-2 text-[11px]"
                onClick={() => onScopeChange?.(key)}
              >
                {label}
              </Button>
            ))}
          </div>
          {onManage ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              onClick={onManage}
            >
              {t('home.counterparty.manage')}
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        {list.length === 0 ? (
          <div className="flex h-32 items-center justify-center px-4 text-center text-xs text-muted-foreground">
            {t('home.counterparty.empty')}
          </div>
        ) : (
          <ul className="space-y-2.5">
            {list.map((row, i) => {
              const pct = maxAbsNet > 0 ? (Math.abs(row.net) / maxAbsNet) * 100 : 0
              const color = BAR_COLORS[i % BAR_COLORS.length]
              const owesMe = row.net > 0
              return (
                <li key={row.name} className="flex items-center gap-3">
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-bold text-white shadow-sm"
                    style={{ background: color }}
                    aria-hidden
                  >
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{row.name}</span>
                      <Badge
                        variant={owesMe ? 'default' : 'outline'}
                        className={`shrink-0 px-1.5 py-0 text-[10px] font-normal ${
                          owesMe ? 'bg-income/15 text-income' : 'text-expense'
                        }`}
                      >
                        {owesMe
                          ? t('home.counterparty.netOwesMe')
                          : t('home.counterparty.netIOwe')}
                      </Badge>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted/60">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${pct}%`, background: color }}
                        />
                      </div>
                      <span
                        className={`shrink-0 font-mono tabular-nums text-[11px] ${
                          owesMe ? 'text-income' : 'text-expense'
                        }`}
                      >
                        {t('home.counterparty.net')}
                        <Amount
                          value={Math.abs(row.net)}
                          currency={currency}
                          size="xs"
                          showCurrency
                          tone={owesMe ? 'positive' : 'negative'}
                        />
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-3 text-[11px] text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        {t('home.counterparty.lent')}
                        <Amount value={row.lent} currency={currency} size="xs" tone="positive" />
                      </span>
                      <span className="inline-flex items-center gap-1">
                        {t('home.counterparty.borrowed')}
                        <Amount value={row.borrowed} currency={currency} size="xs" tone="negative" />
                      </span>
                      <span className="font-mono tabular-nums">
                        {row.count}
                        {t('home.counterparty.countUnit')}
                      </span>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          {t('home.counterparty.hint')}
        </p>
      </CardContent>
    </Card>
  )
}
