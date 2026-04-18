import { useEffect, useRef } from 'react'

import type { AttachmentRef, ReadTag, ReadTransaction } from '@beecount/api-client'
import { EmptyState, useT } from '@beecount/ui'

import {
  TransactionRow,
  TransactionRowVariant,
  buildTagColorMap
} from './TransactionRow'

interface Props {
  items: ReadTransaction[]
  /** 如果传了 `tags` 会从中算 tag → color，TransactionRow 的 badge 就会上色。 */
  tags?: Array<Pick<ReadTag, 'name' | 'color'>>
  variant?: TransactionRowVariant
  loading?: boolean
  /** 还有更多要拉时设为 true，组件会监听底部 sentinel 触发 onLoadMore。 */
  hasMore?: boolean
  onLoadMore?: () => void
  canManage?: boolean
  onEdit?: (row: ReadTransaction) => void
  onDelete?: (row: ReadTransaction) => void
  onPreviewAttachment?: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
  /** @deprecated 新布局把附件简化成 📎 chip，不再需要缩略图预解析 URL。保留
   *  prop 是为了避免旧调用点报类型错误，内部不再消费。 */
  resolveAttachmentPreviewUrl?: (ref: AttachmentRef) => Promise<string | null>
  onClickTag?: (tagName: string) => void
  /** 外层列表 wrapper className，比如弹窗里加 max-h + overflow-y-auto。 */
  className?: string
  emptyTitle?: string
  emptyDescription?: string
}

/**
 * 可复用的交易列表容器：
 *  - 一组 TransactionRow 垂直堆叠，行间细分割线。
 *  - 支持无限滚动：底部 sentinel + IntersectionObserver；只有 `hasMore`
 *    为 true 且有 `onLoadMore` 才启用。
 *  - 支持 "compact" variant：弹窗场景用，信息更紧凑。
 */
export function TransactionList({
  items,
  tags,
  variant = 'default',
  loading = false,
  hasMore = false,
  onLoadMore,
  canManage = true,
  onEdit,
  onDelete,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  onClickTag,
  className,
  emptyTitle,
  emptyDescription
}: Props) {
  const t = useT()
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const tagColorByName = tags ? buildTagColorMap(tags) : undefined

  useEffect(() => {
    if (!hasMore || !onLoadMore) return
    const target = sentinelRef.current
    if (!target) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            onLoadMore()
          }
        }
      },
      { rootMargin: '80px' }
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [hasMore, onLoadMore])

  return (
    <div className={className}>
      {items.length === 0 && !loading ? (
        <EmptyState
          icon={
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                 strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="18" rx="2" />
              <path d="M3 10h18" />
              <path d="M8 14h4" />
            </svg>
          }
          title={emptyTitle || t('table.empty')}
          description={emptyDescription || ''}
        />
      ) : (
        <ul className="divide-y divide-border/50">
          {items.map((row) => (
            <li key={row.id}>
              <TransactionRow
                row={row}
                variant={variant}
                tagColorByName={tagColorByName}
                onEdit={onEdit}
                onDelete={onDelete}
                canManage={canManage}
                onPreviewAttachment={onPreviewAttachment}
                onClickTag={onClickTag}
              />
            </li>
          ))}
        </ul>
      )}

      {/* sentinel for infinite scroll */}
      {hasMore ? <div ref={sentinelRef} className="h-1 w-full" aria-hidden /> : null}

      {loading ? (
        <div className="flex items-center justify-center py-4 text-xs text-muted-foreground">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-muted-foreground/60 border-t-transparent" />
          <span className="ml-2">{t('txList.loading')}</span>
        </div>
      ) : null}

      {/* "没有更多了" 只在无限滚动模式下（显式传了 onLoadMore 说明上层在用
          无限滚动）显示；分页模式（外层用 Prev/Next 控件）里显示会误导用户
          —— 明明还有下一页。 */}
      {onLoadMore && !hasMore && items.length > 0 && !loading ? (
        <div className="py-3 text-center text-[11px] text-muted-foreground">
          {t('txList.noMore')}
        </div>
      ) : null}
    </div>
  )
}
