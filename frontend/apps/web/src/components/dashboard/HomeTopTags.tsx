import { useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'

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
  const t = useT()
  const top = useMemo(() => {
    // 内层 map 回调故意不叫 t 避免和上面 useT() 的 t 冲突
    const withStats = tags
      .map((tag) => ({
        id: tag.id,
        name: tag.name,
        color: tag.color || '#94a3b8',
        count: tag.tx_count ?? 0,
        expense: tag.expense_total ?? 0
      }))
      .filter((tag) => tag.count > 0)
      .sort((a, b) => b.count - a.count)
      .slice(0, 5)
    const maxCount = withStats[0]?.count ?? 0
    return { list: withStats, maxCount }
  }, [tags])

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">{t('home.topTags.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        {top.list.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
            {t('home.topTags.empty')}
          </div>
        ) : (
          <ul className="space-y-2.5">
            {top.list.map((tag, i) => {
              const pct = top.maxCount > 0 ? (tag.count / top.maxCount) * 100 : 0
              return (
                <li
                  key={tag.id || `${tag.name}-${i}`}
                  className={`group relative flex items-center gap-3 ${
                    onClickTag ? 'cursor-pointer' : ''
                  }`}
                  onClick={() => onClickTag?.(tag.name)}
                >
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-bold text-white shadow-sm"
                    style={{ background: tag.color }}
                    aria-hidden
                  >
                    #
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{tag.name}</span>
                      <div className="shrink-0 text-xs text-muted-foreground">
                        <span className="font-mono font-semibold tabular-nums">
                          {tag.count}
                        </span>{' '}
                        {t('home.topTags.countUnit')}
                      </div>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted/60">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${pct}%`,
                            background: tag.color
                          }}
                        />
                      </div>
                      {tag.expense > 0 ? (
                        <Amount
                          value={tag.expense}
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
