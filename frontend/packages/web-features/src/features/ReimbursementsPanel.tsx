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

import type { ReadReimbursement } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { ReimbursementForm } from '../forms'
import { reimbursementDefaults } from '../forms'
import {
  nextReimbursementStatus,
  validateReimbursementInput,
} from '../lib/reimbursements'

/**
 * 0023 报销面板。
 *
 * 报销是独立实体:title 事由、payer 垫付方、reimburser 给付方。
 * 「下一步」把 pending 推进到 reimbursed(实际 PATCH 由 page 层完成)。
 * 保存前用 validateReimbursementInput 做与服务端同规则的预校验。
 */

export type ReimbursementAccountOption = {
  id: string
  name: string
}

type ReimbursementsPanelProps = {
  reimbursements: readonly ReadReimbursement[]
  accountOptions: readonly ReimbursementAccountOption[]
  currency: string
  form: ReimbursementForm
  onFormChange: (next: ReimbursementForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onAdvanceStatus: (
    row: ReadReimbursement,
    nextStatus: 'reimbursed',
  ) => Promise<void> | void
  onDelete: (row: ReadReimbursement) => Promise<void> | void
  canManage: boolean
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  pending: 'secondary',
  reimbursed: 'default',
  cancelled: 'destructive',
}

function dateOnly(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 10)
}

export function ReimbursementsPanel({
  reimbursements,
  accountOptions,
  currency,
  form,
  onFormChange,
  onSubmit,
  onAdvanceStatus,
  onDelete,
  canManage,
}: ReimbursementsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadReimbursement | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [advancingId, setAdvancingId] = useState<string | null>(null)

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.id, a.name)
    return map
  }, [accountOptions])

  const resolveAccountName = (row: ReadReimbursement) =>
    row.account_name ||
    (row.account_id ? accountNameById.get(row.account_id) || '' : '')

  const formError = validateReimbursementInput({
    title: form.title,
    amount: Number(form.amount),
  })

  const handleOpenCreate = () => {
    onFormChange(reimbursementDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (row: ReadReimbursement) => {
    onFormChange({
      editingId: row.id,
      title: row.title || '',
      payer: row.payer || '',
      reimburser: row.reimburser || '',
      amount: String(row.amount ?? 0),
      currency: row.currency || '',
      category_id: row.category_id || '',
      category_name: row.category_name || '',
      happened_at: dateOnly(row.happened_at),
      status: (row.status as ReimbursementForm['status']) || 'pending',
      account_id: row.account_id || '',
      account_name:
        row.account_name || (row.account_id ? accountNameById.get(row.account_id) || '' : ''),
      related_tx_id: row.related_tx_id || '',
      note: row.note || '',
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    if (formError) return
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

  const handleAdvance = async (row: ReadReimbursement) => {
    const next = nextReimbursementStatus(row.status)
    if (next !== 'reimbursed') return
    setAdvancingId(row.id)
    try {
      await onAdvanceStatus(row, next)
    } finally {
      setAdvancingId(null)
    }
  }

  const errorText = formError ? t(`reimbursements.error.${formError}`) : null

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('reimbursements.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('reimbursements.button.create')}
        </Button>
      </div>

      {reimbursements.length === 0 ? (
        <EmptyState
          icon={<span className="material-symbols-outlined text-2xl">receipt_long</span>}
          title={t('reimbursements.empty')}
          description={t('reimbursements.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {reimbursements.map((row) => {
            const next = nextReimbursementStatus(row.status)
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
                    <span className="material-symbols-outlined text-2xl">receipt_long</span>
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold">
                        {row.title || t('reimbursements.untitled')}
                      </span>
                      <Badge variant={STATUS_VARIANT[row.status] || 'outline'}>
                        {t(`reimbursements.status.${row.status}`)}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {dateOnly(row.happened_at)}
                      {row.payer ? ` · ${t('reimbursements.field.payer')}: ${row.payer}` : ''}
                      {row.reimburser ? ` · ${t('reimbursements.field.reimburser')}: ${row.reimburser}` : ''}
                      {resolveAccountName(row) ? ` · ${resolveAccountName(row)}` : ''}
                      {row.category_name ? ` · ${row.category_name}` : ''}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <Amount value={row.amount} currency={currency} size="md" bold tone="default" />
                  </div>
                </div>

                {row.note ? (
                  <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{row.note}</p>
                ) : null}

                <div className="mt-3 flex items-center justify-end gap-2">
                  {next ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!canManage || advancingId === row.id}
                      onClick={() => void handleAdvance(row)}
                    >
                      {t('reimbursements.advance.reimbursed')}
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
              {form.editingId ? t('reimbursements.button.update') : t('reimbursements.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('reimbursements.field.title')}</Label>
              <Input
                value={form.title}
                placeholder={t('reimbursements.field.titlePlaceholder')}
                onChange={(e) => onFormChange({ ...form, title: e.target.value })}
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('reimbursements.field.payer')}</Label>
                <Input
                  value={form.payer}
                  placeholder={t('reimbursements.field.payerPlaceholder')}
                  onChange={(e) => onFormChange({ ...form, payer: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('reimbursements.field.reimburser')}</Label>
                <Input
                  value={form.reimburser}
                  placeholder={t('reimbursements.field.reimburserPlaceholder')}
                  onChange={(e) => onFormChange({ ...form, reimburser: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('reimbursements.field.amount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={form.amount}
                  onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('reimbursements.field.happenedAt')}</Label>
                <Input
                  type="date"
                  value={form.happened_at}
                  onChange={(e) => onFormChange({ ...form, happened_at: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('reimbursements.field.status')}</Label>
                <Select
                  value={form.status}
                  onValueChange={(value) =>
                    onFormChange({ ...form, status: value as ReimbursementForm['status'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="pending">{t('reimbursements.status.pending')}</SelectItem>
                    <SelectItem value="reimbursed">{t('reimbursements.status.reimbursed')}</SelectItem>
                    <SelectItem value="cancelled">{t('reimbursements.status.cancelled')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('reimbursements.field.account')}</Label>
                <Select
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
                    <SelectValue placeholder={t('reimbursements.field.accountPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">
                      <span className="text-muted-foreground">{t('reimbursements.field.accountNone')}</span>
                    </SelectItem>
                    {accountOptions.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('reimbursements.field.relatedTxId')}</Label>
              <Input
                value={form.related_tx_id}
                placeholder={t('reimbursements.field.relatedTxIdPlaceholder')}
                onChange={(e) => onFormChange({ ...form, related_tx_id: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('reimbursements.field.note')}</Label>
              <Textarea
                rows={2}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            {errorText ? (
              <p className="text-xs text-destructive">{errorText}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={submitting || !canManage || formError !== null}
              onClick={() => void handleSubmit()}
            >
              {form.editingId ? t('reimbursements.button.update') : t('reimbursements.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('reimbursements.delete.title')}
        description={t('reimbursements.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
