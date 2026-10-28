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

import type { ReadLoan } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { LoanForm } from '../forms'
import { loanDefaults } from '../forms'
import {
  loanProgress,
  nextLoanStatus,
  validateLoanInput,
} from '../lib/loans'

/**
 * 0023 借款面板。
 *
 * 借款是独立实体(不挂在交易上):direction 区分借入/借出,counterparty 是交易对手。
 * 还款进度 repay_progress 与逾期 days_to_due / is_overdue 由服务端按今天现算,
 * 前端直接展示。「下一步」把 active 推进到 repaid(实际 PATCH 由 page 层完成)。
 * 保存前用 validateLoanInput 做与服务端同规则的预校验。
 */

export type LoanAccountOption = {
  id: string
  name: string
}

type LoansPanelProps = {
  loans: readonly ReadLoan[]
  accountOptions: readonly LoanAccountOption[]
  currency: string
  form: LoanForm
  onFormChange: (next: LoanForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onAdvanceStatus: (row: ReadLoan, nextStatus: 'repaid') => Promise<void> | void
  onDelete: (row: ReadLoan) => Promise<void> | void
  canManage: boolean
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  active: 'secondary',
  repaid: 'default',
  cancelled: 'destructive',
}

function dateOnly(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 10)
}

export function LoansPanel({
  loans,
  accountOptions,
  currency,
  form,
  onFormChange,
  onSubmit,
  onAdvanceStatus,
  onDelete,
  canManage,
}: LoansPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadLoan | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [advancingId, setAdvancingId] = useState<string | null>(null)

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.id, a.name)
    return map
  }, [accountOptions])

  const resolveAccountName = (row: ReadLoan) =>
    row.account_name ||
    (row.account_id ? accountNameById.get(row.account_id) || '' : '')

  const formError = validateLoanInput({
    counterparty: form.counterparty,
    principal: Number(form.principal),
    interestRate: Number(form.interest_rate),
    startedAt: form.started_at,
    dueAt: form.due_at || null,
    repaidAmount: Number(form.repaid_amount),
  })

  const handleOpenCreate = () => {
    onFormChange(loanDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (row: ReadLoan) => {
    onFormChange({
      editingId: row.id,
      direction: (row.direction as LoanForm['direction']) || 'borrowed',
      counterparty: row.counterparty || '',
      principal: String(row.principal ?? 0),
      currency: row.currency || '',
      interest_rate: String(row.interest_rate ?? 0),
      started_at: dateOnly(row.started_at),
      due_at: row.due_at ? dateOnly(row.due_at) : '',
      repaid_amount: String(row.repaid_amount ?? 0),
      status: (row.status as LoanForm['status']) || 'active',
      account_id: row.account_id || '',
      account_name:
        row.account_name || (row.account_id ? accountNameById.get(row.account_id) || '' : ''),
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

  const handleAdvance = async (row: ReadLoan) => {
    const next = nextLoanStatus(row.status)
    if (next !== 'repaid') return
    setAdvancingId(row.id)
    try {
      await onAdvanceStatus(row, next)
    } finally {
      setAdvancingId(null)
    }
  }

  const errorText = formError ? t(`loans.error.${formError}`) : null

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('loans.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('loans.button.create')}
        </Button>
      </div>

      {loans.length === 0 ? (
        <EmptyState
          icon={<span className="material-symbols-outlined text-2xl">handshake</span>}
          title={t('loans.empty')}
          description={t('loans.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {loans.map((row) => {
            const progress = loanProgress(row.repaid_amount, row.principal)
            const next = nextLoanStatus(row.status)
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
                    <span className="material-symbols-outlined text-2xl">handshake</span>
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold">
                        {row.counterparty || t('loans.untitled')}
                      </span>
                      <Badge variant={STATUS_VARIANT[row.status] || 'outline'}>
                        {t(`loans.status.${row.status}`)}
                      </Badge>
                      <Badge variant="outline">
                        {t(`loans.direction.${row.direction}`)}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {dateOnly(row.started_at)}
                      {row.due_at ? ` → ${dateOnly(row.due_at)}` : ''}
                      {resolveAccountName(row) ? ` · ${resolveAccountName(row)}` : ''}
                      {row.is_overdue ? ` · ${t('loans.overdue')}` : ''}
                    </div>
                    {/* 还款进度:已还 / 本金 */}
                    <div className="mt-2">
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary transition-all"
                          style={{ width: `${Math.round(progress.ratio * 100)}%` }}
                        />
                      </div>
                      <div className="mt-1 text-[11px] text-muted-foreground">
                        {t('loans.progress')
                          .replace('{repaid}', (row.repaid_amount ?? 0).toFixed(2))
                          .replace('{principal}', String(row.principal))}
                      </div>
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <Amount value={row.principal} currency={currency} size="md" bold tone="default" />
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {t('loans.field.interestRateShort').replace('{rate}', ((row.interest_rate ?? 0) * 100).toFixed(2))}
                    </div>
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
                      {t('loans.advance.repaid')}
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
              {form.editingId ? t('loans.button.update') : t('loans.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('loans.field.direction')}</Label>
                <Select
                  value={form.direction}
                  onValueChange={(value) =>
                    onFormChange({ ...form, direction: value as LoanForm['direction'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="borrowed">{t('loans.direction.borrowed')}</SelectItem>
                    <SelectItem value="lent">{t('loans.direction.lent')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('loans.field.status')}</Label>
                <Select
                  value={form.status}
                  onValueChange={(value) =>
                    onFormChange({ ...form, status: value as LoanForm['status'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">{t('loans.status.active')}</SelectItem>
                    <SelectItem value="repaid">{t('loans.status.repaid')}</SelectItem>
                    <SelectItem value="cancelled">{t('loans.status.cancelled')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('loans.field.counterparty')}</Label>
              <Input
                value={form.counterparty}
                placeholder={t('loans.field.counterpartyPlaceholder')}
                onChange={(e) => onFormChange({ ...form, counterparty: e.target.value })}
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('loans.field.principal')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={form.principal}
                  onChange={(e) => onFormChange({ ...form, principal: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('loans.field.interestRate')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={form.interest_rate}
                  onChange={(e) => onFormChange({ ...form, interest_rate: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('loans.field.startedAt')}</Label>
                <Input
                  type="date"
                  value={form.started_at}
                  onChange={(e) => onFormChange({ ...form, started_at: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('loans.field.dueAt')}</Label>
                <Input
                  type="date"
                  value={form.due_at}
                  onChange={(e) => onFormChange({ ...form, due_at: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('loans.field.repaidAmount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={form.repaid_amount}
                  onChange={(e) => onFormChange({ ...form, repaid_amount: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('loans.field.account')}</Label>
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
                    <SelectValue placeholder={t('loans.field.accountPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">
                      <span className="text-muted-foreground">{t('loans.field.accountNone')}</span>
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
              <Label>{t('loans.field.note')}</Label>
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
              {form.editingId ? t('loans.button.update') : t('loans.button.create')}
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
        title={t('loans.delete.title')}
        description={t('loans.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
