import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import { AlertTriangle, Loader2 } from 'lucide-react'

interface Props {
  open: boolean
  count: number
  /** 仅展示用 —— 已选交易合计金额(支出 - 收入,转账不计)。 */
  totalAmount?: number
  saving: boolean
  onConfirm: () => void
  onClose: () => void
}

/**
 * 批量删除二次确认 —— 强提示「永久删除」+ destructive 按钮。
 * 设计:.docs/web-tx-batch-actions.md §2.4(单笔删除也无 undo,这里跟随)。
 */
export function BatchDeleteDialog({
  open,
  count,
  totalAmount,
  saving,
  onConfirm,
  onClose,
}: Props) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(v) => !v && !saving && onClose()}>
      <DialogContent className="max-w-md gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            {t('txBatch.confirm.title')}
          </DialogTitle>
        </DialogHeader>
        <div className="px-6 py-5 text-sm">
          <p className="text-foreground">
            {t('txBatch.confirm.body', { count })}
          </p>
          {typeof totalAmount === 'number' && totalAmount !== 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              {t('txBatch.confirm.totalAmount', {
                amount: totalAmount.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                }),
              })}
            </p>
          ) : null}
          <p className="mt-3 text-xs text-destructive">
            {t('txBatch.confirm.cannotUndo')}
          </p>
        </div>
        <DialogFooter className="border-t border-border/60 bg-muted/20 px-6 py-3">
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={saving}
          >
            {t('common.cancel')}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={onConfirm}
            disabled={saving}
          >
            {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t('txBatch.confirm.delete', { count })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
