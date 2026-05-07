import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@beecount/ui'

type ConfirmDialogProps = {
  open: boolean
  title: string
  description: string
  confirmText?: string
  cancelText?: string
  /** confirm 按钮的样式变体,默认 'destructive'(对齐删除/危险操作语义)。 */
  confirmVariant?: 'destructive' | 'default'
  /** 操作执行中:disable 两个按钮,防止双击 / 重复触发。 */
  loading?: boolean
  onCancel: () => void
  onConfirm: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  confirmVariant = 'destructive',
  loading = false,
  onCancel,
  onConfirm
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && !loading && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" disabled={loading} onClick={onCancel}>
            {cancelText}
          </Button>
          <Button variant={confirmVariant} disabled={loading} onClick={onConfirm}>
            {confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
