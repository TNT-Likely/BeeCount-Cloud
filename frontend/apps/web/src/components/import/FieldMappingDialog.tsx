import { useEffect, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'
import type { ImportFieldMapping } from '@beecount/api-client'

interface Props {
  open: boolean
  headers: string[]
  /** server 推断的默认 mapping —— 用作"重置"目标 */
  suggestedMapping: ImportFieldMapping
  /** 当前生效的 mapping */
  currentMapping: ImportFieldMapping
  saving?: boolean
  onClose: () => void
  /** 用户点「应用并预览」后调,父级走 preview API */
  onApply: (next: ImportFieldMapping) => void
}

const REQUIRED_FIELDS: Array<keyof ImportFieldMapping> = [
  'tx_type',
  'amount',
  'happened_at',
  'category_name',
]

const OPTIONAL_FIELDS: Array<keyof ImportFieldMapping> = [
  'subcategory_name',
  'account_name',
  'from_account_name',
  'to_account_name',
  'note',
]

const NONE_SENTINEL = '__none__'

/**
 * 字段映射 Dialog —— 默认隐藏,从预览页「编辑映射」按钮触发。
 *
 * 简化版(产品反馈):去掉「tags 多列合并」+ 三个 transformer 选项
 * (datetime_format / strip_currency / expense_is_negative)。这些 power
 * options 普通用户用不上,留给 server 默认值;真正需要时通过 Excel 预处理。
 */
export function FieldMappingDialog({
  open,
  headers,
  suggestedMapping,
  currentMapping,
  saving = false,
  onClose,
  onApply,
}: Props) {
  const t = useT()
  const [draft, setDraft] = useState<ImportFieldMapping>(currentMapping)

  // 每次打开 dialog 重新同步 draft 到当前生效 mapping
  useEffect(() => {
    if (open) setDraft(currentMapping)
  }, [open, currentMapping])

  const setField = (field: keyof ImportFieldMapping, value: string | null) => {
    setDraft((prev) => ({ ...prev, [field]: value || null }))
  }

  const apply = () => {
    onApply(draft)
    onClose()
  }
  const reset = () => setDraft({ ...suggestedMapping })

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !saving && onClose()}>
      <DialogContent className="max-w-lg gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="text-base">{t('import.mapping.title')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 px-6 py-5">
          <p className="text-[11px] text-muted-foreground">
            {t('import.mapping.hint')}
          </p>
          <div className="space-y-2.5">
            {REQUIRED_FIELDS.map((f) => (
              <FieldRow
                key={f}
                field={f}
                label={t(`import.mapping.field.${f}`)}
                required
                value={draft[f] as string | null}
                headers={headers}
                disabled={saving}
                onChange={(v) => setField(f, v)}
              />
            ))}
            {OPTIONAL_FIELDS.map((f) => (
              <FieldRow
                key={f}
                field={f}
                label={t(`import.mapping.field.${f}`)}
                value={draft[f] as string | null}
                headers={headers}
                disabled={saving}
                onChange={(v) => setField(f, v)}
              />
            ))}
          </div>
        </div>

        <DialogFooter className="flex flex-row items-center justify-between gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
          <Button size="sm" variant="ghost" onClick={reset} disabled={saving}>
            {t('import.mapping.reset')}
          </Button>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={onClose} disabled={saving}>
              {t('common.cancel')}
            </Button>
            <Button size="sm" onClick={apply} disabled={saving}>
              {t('import.mapping.applyAndPreview')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function FieldRow({
  field,
  label,
  required,
  value,
  headers,
  disabled,
  onChange,
}: {
  field: keyof ImportFieldMapping
  label: string
  required?: boolean
  value: string | null
  headers: string[]
  disabled?: boolean
  onChange: (next: string | null) => void
}) {
  const selected = value || NONE_SENTINEL
  return (
    <div className="grid grid-cols-[120px_1fr] items-center gap-2">
      <Label className="text-[11px]" htmlFor={`map-${field}`}>
        {label}
        {required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </Label>
      <Select
        value={selected}
        onValueChange={(v) => onChange(v === NONE_SENTINEL ? null : v)}
        disabled={disabled}
      >
        <SelectTrigger className="h-8 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE_SENTINEL}>—</SelectItem>
          {headers.map((h) => (
            <SelectItem key={h} value={h}>
              {h}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
