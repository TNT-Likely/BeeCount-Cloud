import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import type { ReadTag, WorkspaceTag } from '@beecount/api-client'

import { TagSelector } from './TagSelector'

type TagPickerDialogProps = {
  open: boolean
  onClose: () => void
  /** 候选标签列表。组件内部再做名字 dedup + 搜索过滤。 */
  tags: readonly (ReadTag | WorkspaceTag)[]
  /** 当前选中的标签名字。 */
  selectedNames: readonly string[]
  /** 标签选择变化(立即变,**不**等用户点确认 — 编辑过程中跟 selector 互动)。 */
  onChange: (names: string[]) => void
  title?: string
  /** "清空所有标签"按钮,允许快捷重置。不传则不显示。 */
  onClearAll?: () => void
}

/**
 * 标签多选 dialog —— 把 [TagSelector] 包成弹窗,给 transaction form / 其他
 * 需要"先打开 dialog 再勾选"的场景用。Selector 自己已经支持搜索 + chip 多选,
 * dialog 只补 footer "清空 / 完成" 两个动作。
 */
export function TagPickerDialog({
  open,
  onClose,
  tags,
  selectedNames,
  onChange,
  title,
  onClearAll,
}: TagPickerDialogProps) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title ?? t('tags.title')}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto">
          <TagSelector tags={tags} selectedNames={selectedNames} onChange={onChange} />
        </div>
        <DialogFooter>
          {onClearAll ? (
            <Button variant="outline" onClick={onClearAll}>
              {t('tags.button.reset')}
            </Button>
          ) : null}
          <Button onClick={onClose}>{t('dialog.confirm')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
