import { useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@beecount/ui'

import type { WorkspaceAccount } from '@beecount/api-client'
import { Amount } from '@beecount/web-features'

interface Props {
  accounts: WorkspaceAccount[]
  currency?: string
}

/**
 * 活跃账户 Top 5。按 `tx_count` 降序，排除估值类账户（real_estate / vehicle /
 * investment / insurance / social_fund / loan）——它们只有初始余额估值，不
 * 参与日常记账，放进"活跃度"排行会干扰视觉。
 */
const EXCLUDE_TYPES = new Set([
  'real_estate',
  'vehicle',
  'investment',
  'insurance',
  'social_fund',
  'loan'
])

// 账户类型 → 品牌 SVG 路径（与 AccountsPanel 一致，无导出引用降低耦合）
const TYPE_ICON_URL: Record<string, string> = {
  cash: '/icons/account/cash.svg',
  bank_card: '/icons/account/bank_card.svg',
  credit_card: '/icons/account/credit_card.svg',
  alipay: '/icons/account/alipay.svg',
  wechat: '/icons/account/wechat.svg',
  other: '/icons/account/other_account.svg'
}
const TYPE_COLORS: Record<string, string> = {
  cash: '#10b981',
  bank_card: '#3b82f6',
  credit_card: '#ef4444',
  alipay: '#06b6d4',
  wechat: '#22c55e',
  other: '#64748b'
}

export function HomeTopAccounts({ accounts, currency = 'CNY' }: Props) {
  const top = useMemo(() => {
    const withStats = accounts
      .filter((a) => !EXCLUDE_TYPES.has(a.account_type || ''))
      .map((a) => ({
        id: a.id,
        name: a.name,
        type: a.account_type || 'other',
        count: a.tx_count ?? 0,
        balance: a.balance ?? a.initial_balance ?? 0,
        expense: a.expense_total ?? 0
      }))
      .filter((a) => a.count > 0)
      .sort((a, b) => b.count - a.count)
      .slice(0, 5)
    const maxCount = withStats[0]?.count ?? 0
    return { list: withStats, maxCount }
  }, [accounts])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">活跃账户 Top 5</CardTitle>
      </CardHeader>
      <CardContent>
        {top.list.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
            暂无活跃账户
          </div>
        ) : (
          <ul className="space-y-2.5">
            {top.list.map((a, i) => {
              const pct = top.maxCount > 0 ? (a.count / top.maxCount) * 100 : 0
              const iconUrl = TYPE_ICON_URL[a.type] || TYPE_ICON_URL.other
              const color = TYPE_COLORS[a.type] || '#64748b'
              return (
                <li key={a.id || `${a.name}-${i}`} className="flex items-center gap-3">
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
                    style={{ background: `${color}18`, border: `1px solid ${color}40` }}
                    aria-hidden
                  >
                    <img
                      src={iconUrl}
                      alt=""
                      width={20}
                      height={20}
                      className="select-none"
                      draggable={false}
                    />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{a.name}</span>
                      <div className="shrink-0 text-xs text-muted-foreground">
                        <span className="font-mono font-semibold tabular-nums">
                          {a.count}
                        </span>{' '}
                        笔
                      </div>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted/60">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${pct}%`, background: color }}
                        />
                      </div>
                      <Amount
                        value={a.balance}
                        currency={currency}
                        size="xs"
                        tone={a.balance < 0 ? 'negative' : 'muted'}
                        className="shrink-0"
                      />
                    </div>
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
