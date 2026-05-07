import type {
  AttachmentRef,
  WorkspaceCategory,
  WorkspaceTag,
  WorkspaceTransaction,
} from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import { CategoryIcon, TransactionList } from '@beecount/web-features'
import { Edit3 } from 'lucide-react'

interface Props {
  category: WorkspaceCategory | null
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  iconPreviewUrlByFileId?: Record<string, string>
  /** 是否能管理(决定 Edit 按钮可用) */
  canManage?: boolean
  onClose: () => void
  onLoadMore: (categorySyncId: string, offset: number) => void
  onEdit: (category: WorkspaceCategory) => void
  onPreviewAttachment?: (refs: AttachmentRef[], startIndex: number) => Promise<void>
}

/**
 * 分类详情弹窗 — 头部图标 + 分类名 + 类型标识,中部该分类下的交易列表
 * (compact + 无限滚动),底部 Edit / Delete 入口。
 *
 * 结构跟 AccountDetailDialog / TagDetailDialog 对齐,区别:
 *  - 没有金额聚合统计(分类的"总收入/支出"已在 CategoriesPanel 列表里展示,
 *    详情页重复展示意义不大)
 *  - 顶部突出图标 + kind 类型徽章,让用户一眼看出分类语义
 */
export function CategoryDetailDialog({
  category,
  transactions,
  total,
  offset,
  loading,
  tags,
  iconPreviewUrlByFileId,
  canManage = true,
  onClose,
  onLoadMore,
  onEdit,
  onPreviewAttachment,
}: Props) {
  const t = useT()
  const kindLabel = category
    ? category.kind === 'expense'
      ? t('enum.txType.expense')
      : category.kind === 'income'
        ? t('enum.txType.income')
        : t('enum.txType.transfer')
    : ''
  const kindClass =
    category?.kind === 'expense'
      ? 'text-expense'
      : category?.kind === 'income'
        ? 'text-income'
        : 'text-muted-foreground'

  return (
    <Dialog open={Boolean(category)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-3">
            {category ? (
              <span className="flex h-9 w-9 items-center justify-center rounded-md bg-muted/50">
                <CategoryIcon
                  icon={category.icon}
                  iconType={category.icon_type}
                  iconCloudFileId={category.icon_cloud_file_id}
                  iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                  size={20}
                  className="text-foreground"
                />
              </span>
            ) : null}
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-base">{category?.name || ''}</span>
              <span className={`text-[11px] font-normal uppercase tracking-widest ${kindClass}`}>
                {kindLabel}
                {category?.parent_name ? ` · ${category.parent_name}` : ''}
              </span>
            </div>
          </DialogTitle>
        </DialogHeader>

        {category ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="grid grid-cols-2 gap-3 border-b border-border/60 bg-muted/20 px-6 py-3 text-center">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {t('detail.stats.txCount')}
                </div>
                <div className="mt-0.5 text-xl font-bold tabular-nums">
                  {category.tx_count ?? total}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {t('detail.category.level')}
                </div>
                <div className="mt-0.5 text-sm font-medium">
                  {category.level ?? '—'}
                </div>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
              <TransactionList
                items={transactions}
                tags={tags}
                variant="compact"
                loading={loading}
                hasMore={transactions.length < total}
                onLoadMore={() => {
                  if (!loading && category) onLoadMore(category.id, offset)
                }}
                onPreviewAttachment={onPreviewAttachment}
                emptyTitle={t('detail.category.empty.title')}
                emptyDescription={t('detail.category.empty.desc')}
              />
            </div>

            <div className="flex flex-row items-center justify-end gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
              <Button variant="outline" size="sm" onClick={onClose}>
                {t('dialog.cancel')}
              </Button>
              <Button
                size="sm"
                disabled={!canManage}
                onClick={() => onEdit(category)}
              >
                <Edit3 className="mr-1 h-3.5 w-3.5" />
                {t('common.edit')}
              </Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
