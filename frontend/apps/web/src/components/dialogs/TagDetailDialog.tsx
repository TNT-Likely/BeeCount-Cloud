import type { ReadTag, WorkspaceTag, WorkspaceTransaction } from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT
} from '@beecount/ui'
import { TransactionList } from '@beecount/web-features'

interface TagStats {
  count: number
  income: number
  expense: number
}

interface Props {
  tag: ReadTag | null
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  tagStatsById: Record<string, TagStats>
  onClose: () => void
  onLoadMore: (tagSyncId: string, offset: number) => void
  onPreviewAttachment?: (ctx: unknown) => void
  resolveAttachmentPreviewUrl?: (att: unknown) => string | null
}

/** 点标签卡片弹出的详情:顶部标签色块 + 统计摘要 + 该标签下交易列表(无限加载)。 */
export function TagDetailDialog({
  tag,
  transactions,
  total,
  offset,
  loading,
  tags,
  tagStatsById,
  onClose,
  onLoadMore,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
}: Props) {
  const t = useT()
  const stats = tag ? tagStatsById[tag.id] : null
  return (
    <Dialog open={Boolean(tag)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-2">
            <span
              className="flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold text-white"
              style={{ background: tag?.color || '#94a3b8' }}
            >
              #
            </span>
            <span className="truncate">{tag?.name || ''}</span>
          </DialogTitle>
        </DialogHeader>
        {tag ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {stats ? (
              <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
                <StatCell label={t('detail.stats.txCount')} value={stats.count} bold />
                <StatCell
                  label={t('detail.stats.accumExpense')}
                  value={stats.expense}
                  tone="expense"
                />
                <StatCell
                  label={t('detail.stats.accumIncome')}
                  value={stats.income}
                  tone="income"
                />
              </div>
            ) : null}

            <div className="min-h-0 flex-1 overflow-y-auto">
              <TransactionList
                items={transactions}
                tags={tags}
                variant="compact"
                loading={loading}
                hasMore={transactions.length < total}
                onLoadMore={() => {
                  if (!loading) onLoadMore(tag.id, offset)
                }}
                onPreviewAttachment={onPreviewAttachment as never}
                resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                emptyTitle={t('transactions.empty.forTag.title')}
                emptyDescription={t('transactions.empty.forTag.desc')}
              />
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function StatCell({
  label,
  value,
  tone,
  bold,
}: {
  label: string
  value: number
  tone?: 'income' | 'expense'
  bold?: boolean
}) {
  const colorClass =
    tone === 'income' ? 'text-income' : tone === 'expense' ? 'text-expense' : ''
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={`mt-0.5 font-mono ${bold ? 'text-xl' : 'text-base'} font-bold tabular-nums ${colorClass}`}
      >
        {bold
          ? value
          : value.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2
            })}
      </div>
    </div>
  )
}
