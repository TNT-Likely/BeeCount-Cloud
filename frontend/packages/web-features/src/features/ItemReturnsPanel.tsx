import { useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
  useT,
} from '@beecount/ui'

import type { ReadItemReturn, WorkspaceCategory } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { ItemReturnForm } from '../forms'
import { itemReturnDefaults } from '../forms'

/**
 * 0021 归物面板(物品归还/退货退款,App 端 issue #465 的「退款」诉求)。
 *
 * 状态流:pending(待归还)→ returned(已归还待退款)→ refunded(已退款入账),
 * 任意状态可 cancelled(已取消)。面板提供「下一步」按钮按当前状态推进,
 * 实际 PATCH 由 page 层 onAdvanceStatus 完成。
 */

export type ItemReturnAccountOption = {
  id: string
  name: string
}

type ItemReturnsPanelProps = {
  returns: readonly ReadItemReturn[]
  accountOptions: readonly ItemReturnAccountOption[]
  /** 分类候选(workspace categories,给 picker 用)。 */
  categoryOptions: readonly WorkspaceCategory[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  form: ItemReturnForm
  onFormChange: (next: ItemReturnForm) => void
  onSubmit: () => Promise<boolean> | boolean
  /** 状态推进:page 层 PATCH status。 */
  onAdvanceStatus: (
    row: ReadItemReturn,
    nextStatus: ItemReturnForm['status'],
  ) => Promise<void> | void
  onDelete: (row: ReadItemReturn) => Promise<void> | void
  canManage: boolean
}

/** pending → returned → refunded 的下一档;cancelled/refunded 无下一档。 */
const NEXT_STATUS: Record<string, ItemReturnForm['status'] | undefined> = {
  pending: 'returned',
  returned: 'refunded',
  refunded: undefined,
  cancelled: undefined
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  pending: 'secondary',
  returned: 'default',
  refunded: 'outline',
  cancelled: 'destructive'
}

function dateOnly(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 10)
}

export function ItemReturnsPanel({
  returns,
  accountOptions,
  categoryOptions,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onAdvanceStatus,
  onDelete,
  canManage
}: ItemReturnsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadItemReturn | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [advancingId, setAdvancingId] = useState<string | null>(null)

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.id, a.name)
    return map
  }, [accountOptions])

  const handleOpenCreate = () => {
    onFormChange(itemReturnDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (row: ReadItemReturn) => {
    onFormChange({
      editingId: row.id,
      name: row.name || '',
      original_tx_id: row.original_tx_id || '',
      original_amount:
        row.original_amount === null || row.original_amount === undefined
          ? ''
          : String(row.original_amount),
      refund_amount: String(row.refund_amount ?? 0),
      happened_at: dateOnly(row.happened_at),
      reason: row.reason || '',
      status: (row.status as ItemReturnForm['status']) || 'pending',
      account_id: row.account_id || '',
      account_name: row.account_name || (row.account_id ? accountNameById.get(row.account_id) || '' : ''),
      category_id: row.category_id || '',
      category_name: row.category_name || '',
      note: row.note || ''
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const handleAdvance = async (row: ReadItemReturn) => {
    const next = NEXT_STATUS[row.status]
    if (!next) return
    setAdvancingId(row.id)
    try {
      await onAdvanceStatus(row, next)
    } finally {
      setAdvancingId(null)
    }
  }

  const resolveAccountName = (row: ReadItemReturn) =>
    row.account_name || (row.account_id ? accountNameById.get(row.account_id) || '' : '')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('itemReturns.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('itemReturns.button.create')}
        </Button>
      </div>

      {returns.length === 0 ? (
        <EmptyState
          icon={<span className="material-symbols-outlined text-2xl">assignment_return</span>}
          title={t('itemReturns.empty')}
          description={t('itemReturns.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {returns.map((row) => {
            const next = NEXT_STATUS[row.status]
            return (
              <div
                key={row.id}
                className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <span
                    aria-hidden
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  >
                    <span className="material-symbols-outlined text-2xl">assignment_return</span>
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold">{row.name}</span>
                      <Badge variant={STATUS_VARIANT[row.status] || 'outline'}>
                        {t(`itemReturns.status.${row.status}`)}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {dateOnly(row.happened_at)}
                      {resolveAccountName(row) ? ` · ${resolveAccountName(row)}` : ''}
                      {row.category_name ? ` · ${row.category_name}` : ''}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <Amount value={row.refund_amount} currency={currency} size="md" bold tone="default" />
                    {row.original_amount ? (
                      <div className="mt-0.5 text-[11px] text-muted-foreground line-through">
                        <Amount value={row.original_amount} currency={currency} size="xs" tone="muted" />
                      </div>
                    ) : null}
                  </div>
                </div>

                {row.reason || row.note ? (
                  <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">
                    {[row.reason, row.note].filter(Boolean).join(' · ')}
                  </p>
                ) : null}

                <div className="mt-3 flex items-center justify-end gap-2">
                  {next ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!canManage || advancingId === row.id}
                      onClick={() => void handleAdvance(row)}
                    >
                      {t(`itemReturns.advance.${next}`)}
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => handleOpenEdit(row)}>
                    {t('common.edit')}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setPendingDelete(row)}>
                    {t('common.delete')}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('itemReturns.button.update') : t('itemReturns.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('itemReturns.field.name')}</Label>
              <Input
                value={form.name}
                placeholder={t('itemReturns.field.namePlaceholder')}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('itemReturns.field.refundAmount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={form.refund_amount}
                  onChange={(e) => onFormChange({ ...form, refund_amount: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('itemReturns.field.originalAmount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder={t('itemReturns.field.optional')}
                  value={form.original_amount}
                  onChange={(e) => onFormChange({ ...form, original_amount: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('itemReturns.field.happenedAt')}</Label>
                <Input
                  type="date"
                  value={form.happened_at}
                  onChange={(e) => onFormChange({ ...form, happened_at: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('itemReturns.field.status')}</Label>
                <Select
                  value={form.status}
                  onValueChange={(value) =>
                    onFormChange({ ...form, status: value as ItemReturnForm['status'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pending">{t('itemReturns.status.pending')}</SelectItem>
                    <SelectItem value="returned">{t('itemReturns.status.returned')}</SelectItem>
                    <SelectItem value="refunded">{t('itemReturns.status.refunded')}</SelectItem>
                    <SelectItem value="cancelled">{t('itemReturns.status.cancelled')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('itemReturns.field.account')}</Label>
                <Select
                  // Radix SelectItem 不允许 value="",sentinel "__none__" = 不选。
                  value={form.account_id || '__none__'}
                  onValueChange={(value) => {
                    if (value === '__none__') {
                      onFormChange({ ...form, account_id: '', account_name: '' })
                      return
                    }
                    const hit = accountOptions.find((a) => a.id === value)
                    onFormChange({ ...form, account_id: value, account_name: hit?.name || '' })
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('itemReturns.field.accountPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">
                      <span className="text-muted-foreground">{t('itemReturns.field.accountNone')}</span>
                    </SelectItem>
                    {accountOptions.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('itemReturns.field.category')}</Label>
                <button
                  type="button"
                  onClick={() => setCategoryPickerOpen(true)}
                  className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                >
                  <span className={`truncate ${form.category_name ? '' : 'text-muted-foreground'}`}>
                    {form.category_name || t('itemReturns.field.categoryPlaceholder')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('itemReturns.field.reason')}</Label>
              <Input
                value={form.reason}
                placeholder={t('itemReturns.field.reasonPlaceholder')}
                onChange={(e) => onFormChange({ ...form, reason: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('itemReturns.field.note')}</Label>
              <Textarea
                rows={2}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage} onClick={() => void handleSubmit()}>
              {form.editingId ? t('itemReturns.button.update') : t('itemReturns.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind="expense"
        rows={categoryOptions}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || null}
        title={t('itemReturns.field.categoryPlaceholder')}
        onSelect={(cat) =>
          onFormChange({
            ...form,
            category_id: cat.id,
            category_name: cat.name
          })
        }
        onClear={() => onFormChange({ ...form, category_id: '', category_name: '' })}
        clearLabel={t('itemReturns.field.categoryNone')}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('itemReturns.delete.title')}
        description={t('itemReturns.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
