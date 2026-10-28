import { useMemo } from 'react'
import { Users } from 'lucide-react'

import type { MemberShareStat } from '@beecount/api-client'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { Amount } from '@beecount/web-features'

export type MemberRankScope = 'month' | 'year'

interface Props {
  /** 后端 /member-share-stats 的 items。null = 还没拉到;[] = 拉到了但没数据。 */
  items: MemberShareStat[] | null
  currency?: string
  scope: MemberRankScope
  onScopeChange?: (scope: MemberRankScope) => void
  /** 点「管理成员」→ 跳成员页。没传则不显示该按钮。 */
  onManage?: () => void
}

/**
 * 成员维度 Top 排行 —— 跟 HomeTopTags / HomeTopAccounts 同一套卡片视觉:
 * bar 宽度相对第一名归一,右侧显示金额 + 笔数。
 *
 * 口径(跟后端 member-share-stats 一致):
 *   - 有分摊的账单 → 按分摊金额归到对应成员(kind='member')
 *   - 没分摊的账单 → 按记账人归集(kind='user',行内给「记账人」淡标提示)
 * 所以两类行会混排,hybrid 口径下占比分母是同一个总支出,可直接横向比较。
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

export function HomeMemberRanks({
  items,
  currency = 'CNY',
  scope,
  onScopeChange,
  onManage,
}: Props) {
  const t = useT()

  const { list, maxExpense, total } = useMemo(() => {
    const rows = (items || [])
      .map((row) => ({
        raw: row,
        id: row.id,
        name: row.name,
        kind: row.kind,
        expense: row.expense_total || 0,
        income: row.income_total || 0,
        count: row.tx_count || 0,
      }))
      // 只显示"有支出"的人 —— 纯收入行 / 全零行对"谁花了多少"没有信息量,
      // 而且会把 bar 的分母稀释。收入在行尾单独小字展示。
      .filter((row) => row.expense > 0)
      .sort((a, b) => b.expense - a.expense)
      .slice(0, 8)
    const totalExpense = rows.reduce((sum, row) => sum + row.expense, 0)
    return { list: rows, maxExpense: rows[0]?.expense ?? 0, total: totalExpense }
  }, [items])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Users className="h-4 w-4 text-muted-foreground" aria-hidden />
          {t('home.memberRanks.title')}
        </CardTitle>
        <div className="flex items-center gap-1">
          {/* 本月 / 本年切换 —— 跟 analytics 的 scope 口径一致 */}
          <div className="flex items-center gap-0.5 rounded-md bg-muted/50 p-0.5">
            <Button
              type="button"
              size="sm"
              variant={scope === 'month' ? 'secondary' : 'ghost'}
              className="h-6 px-2 text-[11px]"
              onClick={() => onScopeChange?.('month')}
            >
              {t('home.memberRanks.scopeMonth')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant={scope === 'year' ? 'secondary' : 'ghost'}
              className="h-6 px-2 text-[11px]"
              onClick={() => onScopeChange?.('year')}
            >
              {t('home.memberRanks.scopeYear')}
            </Button>
          </div>
          {onManage ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[11px]"
              onClick={onManage}
            >
              {t('home.memberRanks.manage')}
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        {list.length === 0 ? (
          <div className="flex h-32 items-center justify-center px-4 text-center text-xs text-muted-foreground">
            {t('home.memberRanks.empty')}
          </div>
        ) : (
          <ul className="space-y-2.5">
            {list.map((row, i) => {
              const pct = maxExpense > 0 ? (row.expense / maxExpense) * 100 : 0
              const share = total > 0 ? Math.round((row.expense / total) * 1000) / 10 : 0
              const color = BAR_COLORS[i % BAR_COLORS.length]
              return (
                <li key={`${row.kind}:${row.id}`} className="flex items-center gap-3">
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-bold text-white shadow-sm"
                    style={{ background: color }}
                    aria-hidden
                  >
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <span className="truncate text-sm font-medium">{row.name}</span>
                        {/* kind='user' = 未分摊账单按记账人归集的兜底桶,
                            给个淡标让"这不是真·成员"这件事可见。 */}
                        {row.kind === 'user' ? (
                          <Badge
                            variant="outline"
                            className="shrink-0 px-1 py-0 text-[10px] font-normal"
                          >
                            {t('home.memberRanks.byRecorder')}
                          </Badge>
                        ) : null}
                      </span>
                      <div className="shrink-0 text-xs text-muted-foreground">
                        <span className="font-mono font-semibold tabular-nums">
                          {row.count}
                        </span>{' '}
                        {t('home.memberRanks.countUnit')}
                        <span className="ml-1.5 font-mono tabular-nums">{share}%</span>
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
                        value={row.expense}
                        currency={currency}
                        size="xs"
                        tone="muted"
                        className="shrink-0"
                      />
                    </div>
                    {row.income > 0 ? (
                      <div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                        <span>{t('analytics.memberRank.income')}</span>
                        <Amount
                          value={row.income}
                          currency={currency}
                          size="xs"
                          tone="positive"
                        />
                      </div>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          {t('home.memberRanks.hint')}
        </p>
      </CardContent>
    </Card>
  )
}
