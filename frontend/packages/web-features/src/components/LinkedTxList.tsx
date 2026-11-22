import { useMemo, useState } from 'react'

import { useT } from '@beecount/ui'
import { Button, Input } from '@beecount/ui'

import { Amount } from './Amount'
import { formatIsoDateTimeLocal } from '../format'

/**
 * 关联交易流水(0046)。
 *
 * 凡是「业务实体引用了某条交易流水」的地方(物品 / 借款 / 还款 / 储蓄罐 /
 * 定期存款 / 报销),详情页都要把这条流水**可读地**展示出来 —— 不能甩一串
 * syncId 给用户。
 *
 * 渲染规格与交易流水行(TransactionRow)保持一致:
 *   左上 主标题 = 分类名,备注挂括号(有备注时);
 *   右上 金额   = 收支语义色 + 紧凑格式;
 *   左下 meta  = 时间 · 账户;
 * 关联的流水被删除时,整行置灰并标注「已删除」。
 */
export type LinkedTx = {
  sync_id: string
  tx_type: string
  amount: number
  happened_at: string | null
  note: string | null
  category_name: string | null
  category_kind?: string | null
  account_name: string | null
  currency_code?: string | null
  exclude_from_stats?: boolean
}

type LinkedTxListProps = {
  /** 关联流水列表(后端 tx_sync_details) */
  details: LinkedTx[] | null | undefined
  /** 行标题显示模式:category=分类优先(默认),note=备注优先 */
  noteDisplayMode?: 'category' | 'note'
  /** 每行的额外徽标(react node),如「本金」「利息」「存入」 */
  renderBadge?: (tx: LinkedTx) => React.ReactNode
  /** 点击关联流水行时回调(打开交易详情) */
  onSelect?: (tx: LinkedTx) => void
  /** 无数据时的提示;null = 不渲染空态 */
  emptyText?: string | null
  className?: string
  /** 是否启用搜索框(默认 true) */
  searchable?: boolean
}

function txTypeLabel(txType: string): string {
  if (txType === 'income') return '收入'
  if (txType === 'transfer') return '转账'
  if (txType === 'refund') return '退款'
  return '支出'
}

export function LinkedTxList({
  details,
  noteDisplayMode = 'category',
  renderBadge,
  onSelect,
  emptyText = null,
  className,
  searchable = true,
}: LinkedTxListProps) {
  const t = useT()
  const rows = details ?? []
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter((tx) => {
      const hay = [
        tx.note ?? '',
        tx.category_name ?? '',
        tx.account_name ?? '',
        String(tx.amount ?? ''),
      ]
        .join(' ')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [rows, query])

  if (rows.length === 0) {
    return emptyText ? (
      <p className="px-1 py-2 text-xs text-muted-foreground">{emptyText}</p>
    ) : null
  }

  return (
    <div className={className}>
      {searchable && rows.length > 3 ? (
        <div className="mb-1.5">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('common.search') as string}
            className="h-7 text-xs"
          />
        </div>
      ) : null}
      <ul className="divide-y rounded border border-border/60">
      {filtered.map((tx) => {
        const deleted = !tx.happened_at
        const categoryText = tx.category_name || txTypeLabel(tx.tx_type)
        const noteText = tx.note ?? ''
        // 与 TransactionRow 一致:备注优先模式下备注当主标题,否则分类当主标题、备注挂括号。
        const primary =
          noteDisplayMode === 'note' && tx.category_name && noteText
            ? noteText
            : categoryText
        const parenNote =
          noteDisplayMode === 'note' && tx.category_name && noteText
            ? null
            : noteText || null
        const isIncome = tx.tx_type === 'income'
        const isTransfer = tx.tx_type === 'transfer'

        return (
          <li
            key={tx.sync_id}
            className={`px-2.5 py-2 ${deleted ? 'opacity-50' : ''} ${onSelect ? 'cursor-pointer hover:bg-accent/30' : ''}`}
            onClick={() => !deleted && onSelect?.(tx)}
          >
            <div
              className="grid gap-x-3"
              style={{ gridTemplateColumns: '1fr auto', gridTemplateRows: 'auto 1fr' }}
            >
              {/* 左上:主标题 + 括号备注 + 徽标 */}
              <div className="flex min-w-0 items-center gap-2 self-start">
                <span className="truncate text-sm font-medium">{primary}</span>
                {parenNote ? (
                  <span className="truncate text-xs text-muted-foreground">
                    ({parenNote})
                  </span>
                ) : null}
                {deleted ? (
                  <span
                    className="shrink-0 text-[11px] text-muted-foreground"
                    title={tx.sync_id}
                  >
                    {t('common.deleted')}
                  </span>
                ) : null}
                {renderBadge ? renderBadge(tx) : null}
              </div>

              {/* 右上:金额(收支语义色,同交易流水行) */}
              <div className="flex shrink-0 items-center justify-end self-start">
                <Amount
                  value={tx.amount}
                  currency={tx.currency_code}
                  compact={false}
                  bold
                  size="sm"
                  tone={
                    isIncome ? 'positive' : isTransfer ? 'muted' : 'negative'
                  }
                />
              </div>

              {/* 左下:时间 · 账户 */}
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 self-end text-xs text-muted-foreground">
                {tx.happened_at ? (
                  <span className="font-mono tabular-nums">
                    {formatIsoDateTimeLocal(tx.happened_at)}
                  </span>
                ) : null}
                {tx.account_name && tx.account_name !== '-' ? (
                  <span className="truncate">· {tx.account_name}</span>
                ) : null}
              </div>
            </div>
          </li>
        )
      })}
      </ul>
    </div>
  )
}
