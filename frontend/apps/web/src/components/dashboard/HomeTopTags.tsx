import { useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@beecount/ui'

import type { WorkspaceTag } from '@beecount/api-client'
import { Amount } from '@beecount/web-features'

interface Props {
  tags: WorkspaceTag[]
  currency?: string
  onClickTag?: (name: string) => void
}

/**
 * 使用最多的标签 Top 5。按 `tx_count` 降序，右侧显示笔数 + 当年支出金额。
 * bar 宽度相对第一名归一，一眼看出头部和尾部的差距。
 */
export function HomeTopTags({ tags, currency = 'CNY', onClickTag }: Props) {
  const top = useMemo(() => {
    const withStats = tags
      .map((t) => ({
        id: t.id,
        name: t.name,
        color: t.color || '#94a3b8',
        count: t.tx_count ?? 0,
        expense: t.expense_total ?? 0
      }))
      .filter((t) => t.count > 0)
      .sort((a, b) => b.count - a.count)
      .slice(0, 5)
    const maxCount = withStats[0]?.count ?? 0
    return { list: withStats, maxCount }
  }, [tags])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">热门标签 Top 5</CardTitle>
      </CardHeader>
      <CardContent>
        {top.list.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
            暂无标签使用记录
          </div>
        ) : (
          <ul className="space-y-2.5">
            {top.list.map((t, i) => {
              const pct = top.maxCount > 0 ? (t.count / top.maxCount) * 100 : 0
              return (
                <li
                  key={t.id || `${t.name}-${i}`}
                  className={`group relative flex items-center gap-3 ${
                    onClickTag ? 'cursor-pointer' : ''
                  }`}
                  onClick={() => onClickTag?.(t.name)}
                >
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-bold text-white shadow-sm"
                    style={{ background: t.color }}
                    aria-hidden
                  >
                    #
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{t.name}</span>
                      <div className="shrink-0 text-xs text-muted-foreground">
                        <span className="font-mono font-semibold tabular-nums">
                          {t.count}
                        </span>{' '}
                        笔
                      </div>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted/60">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${pct}%`,
                            background: t.color
                          }}
                        />
                      </div>
                      {t.expense > 0 ? (
                        <Amount
                          value={t.expense}
                          currency={currency}
                          size="xs"
                          tone="muted"
                          className="shrink-0"
                        />
                      ) : null}
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
