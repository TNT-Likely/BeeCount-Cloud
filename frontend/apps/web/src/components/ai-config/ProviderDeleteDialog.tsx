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

import { type AICapabilityBinding, type AIProvider } from '@beecount/api-client'

import { capabilitiesBoundTo } from '../../lib/aiConfigMerge'

interface Props {
  open: boolean
  target: AIProvider | null
  binding: AICapabilityBinding | null
  saving?: boolean
  onConfirm: () => void
  onClose: () => void
}

/**
 * 删除 provider 二次确认 —— 跟 mobile delete 路径行为对齐。删除已绑定的会
 * 显式提示:fallback 哪些能力到「智谱GLM」。
 */
export function ProviderDeleteDialog({
  open,
  target,
  binding,
  saving = false,
  onConfirm,
  onClose,
}: Props) {
  const t = useT()
  if (!target) return null

  const boundCaps = capabilitiesBoundTo(binding, target.id)
  const capLabels = boundCaps
    .map((c) =>
      c === 'text'
        ? t('ai.binding.text')
        : c === 'vision'
          ? t('ai.binding.vision')
          : t('ai.binding.speech'),
    )
    .join('、')

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !saving && onClose()}>
      <DialogContent className="max-w-md gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            {t('ai.editor.providers.delete.title', { name: target.name })}
          </DialogTitle>
        </DialogHeader>

        <div className="px-6 py-5 text-sm">
          {boundCaps.length > 0 ? (
            <p className="text-foreground">
              {t('ai.editor.providers.delete.bodyBound', { capabilities: capLabels })}
            </p>
          ) : (
            <p className="text-muted-foreground">
              {t('ai.editor.providers.delete.body')}
            </p>
          )}
        </div>

        <DialogFooter className="border-t border-border/60 bg-muted/20 px-6 py-3">
          <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>
            {t('common.cancel')}
          </Button>
          <Button size="sm" variant="destructive" onClick={onConfirm} disabled={saving}>
            {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t('common.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
