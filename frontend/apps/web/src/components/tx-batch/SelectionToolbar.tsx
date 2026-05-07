import { Button, useT } from '@beecount/ui'
import { CheckSquare, Download, Trash2, X } from 'lucide-react'

interface Props {
  selectedCount: number
  totalCount: number
  /** 是否所有当前可见行都已选中 —— 决定全选按钮的 label / 行为。 */
  allVisibleSelected: boolean
  saving?: boolean
  onToggleAllVisible: () => void
  onDelete: () => void
  onExport: () => void
  onExit: () => void
}

/**
 * 桌面端批量选择 toolbar —— 列表上方 sticky,显示已选数量 + 操作按钮 + 退出。
 *
 * 设计:.docs/web-tx-batch-actions.md §2.2
 *
 * 移动端通过外层 `hidden md:flex` 完全不渲染,保证小屏既不暴露入口也没切换
 * 状态的可能。空 selection (count=0) 时操作按钮 disabled,引导用户先选行。
 */
export function SelectionToolbar({
  selectedCount,
  totalCount,
  allVisibleSelected,
  saving = false,
  onToggleAllVisible,
  onDelete,
  onExport,
  onExit,
}: Props) {
  const t = useT()
  const noneSelected = selectedCount === 0
  return (
    <div className="hidden flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs md:flex">
      <span className="font-medium text-foreground">
        {t('txBatch.selectedCount', { count: selectedCount })}
      </span>
      <span className="text-muted-foreground">
        / {t('txBatch.totalCount', { count: totalCount })}
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        <Button
          size="sm"
          variant="ghost"
          className="h-7"
          onClick={onToggleAllVisible}
          disabled={saving}
        >
          <CheckSquare className="mr-1 h-3.5 w-3.5" />
          {allVisibleSelected
            ? t('txBatch.deselectAllVisible')
            : t('txBatch.selectAllVisible')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7"
          onClick={onExport}
          disabled={noneSelected || saving}
        >
          <Download className="mr-1 h-3.5 w-3.5" />
          {t('txBatch.exportCsv')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={onDelete}
          disabled={noneSelected || saving}
        >
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          {t('txBatch.delete')}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7"
          onClick={onExit}
          disabled={saving}
          aria-label={t('common.cancel') as string}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}
