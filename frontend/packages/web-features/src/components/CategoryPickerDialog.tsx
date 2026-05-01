import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import type { WorkspaceCategory } from '@beecount/api-client'

import { CategorySelector } from './CategorySelector'

type CategoryPickerDialogProps = {
  open: boolean
  onClose: () => void
  kind: 'expense' | 'income'
  /** 候选行(已经按 use case 预过滤,例如父级选取时只传 level=1 + tx_count=0)。 */
  rows: readonly WorkspaceCategory[]
  iconPreviewUrlByFileId?: Record<string, string>
  /** 当前选中的 syncId,会高亮 + 自动展开父级。 */
  selectedId?: string | null
  /** 标题文案,例如"选择父级分类" / "选择交易分类"。 */
  title: string
  /** 选中分类时回调。如果场景允许"取消选择",见 onClear。 */
  onSelect: (category: WorkspaceCategory) => void
  /**
   * 当传入时,Dialog footer 显示"无父分类 / 未分类"按钮,点击调
   * onClear() 并关闭 dialog。父级选取场景需要,交易分类选取也常需要(允许
   * 不挑分类直接保存)。
   */
  onClear?: () => void
  /** onClear 按钮的文案,例如 t('common.none') / t('categories.parent.none')。 */
  clearLabel?: string
  emptyText?: string
  /** 网格列数,默认 4。 */
  columns?: number
}

/**
 * 选择分类 dialog —— 把 [CategorySelector] 包装成弹窗。
 *
 * 给两类场景复用:
 *   1. 编辑分类时选父级(传 onClear / clearLabel = "无父分类")
 *   2. 编辑交易时选分类(传 onClear / clearLabel = "未分类")
 *
 * Selector 自身的交互(父级有子分类点击 → 展开;无子分类 / 子级 → 选中)
 * 完全对齐 mobile category_selector_dialog,跨端体验一致。
 */
export function CategoryPickerDialog({
  open,
  onClose,
  kind,
  rows,
  iconPreviewUrlByFileId,
  selectedId,
  title,
  onSelect,
  onClear,
  clearLabel,
  emptyText,
  columns = 4,
}: CategoryPickerDialogProps) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {/* `px-1 py-2` 给 CategorySelector 留出 ring 溢出空间 —— 选中态用
            `ring-2`(2px 外扩),还有 hasChildren 父级右下角徽章 `-bottom-1
            -right-1`(4px 外扩),裸 overflow 容器会把第一行 ring 顶部裁掉
            (2026-04 用户反馈)。`-mx-1` 抵消 padding 的水平偏移,保持网格
            视觉对齐。 */}
        <div className="-mx-1 max-h-[60vh] overflow-y-auto px-1 py-2">
          <CategorySelector
            kind={kind}
            rows={rows}
            selectedId={selectedId}
            iconPreviewUrlByFileId={iconPreviewUrlByFileId}
            columns={columns}
            emptyText={emptyText}
            onSelect={(cat) => {
              onSelect(cat)
              onClose()
            }}
          />
        </div>
        <DialogFooter>
          {onClear ? (
            <Button
              variant="outline"
              onClick={() => {
                onClear()
                onClose()
              }}
            >
              {clearLabel ?? t('common.none')}
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
