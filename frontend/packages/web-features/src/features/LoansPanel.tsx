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

import type {
  LoanRepayment,
  LoanRepaymentCreatePayload,
  ReadLoan,
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { LoanForm } from '../forms'
import { loanDefaults } from '../forms'
import { loanProgress, validateLoanInput } from '../lib/loans'

/**
 * 0023 借款面板(0032 增强)。
 *
 * 借款是独立实体(不挂在交易上):direction 区分借入/借出,counterparty 是交易对手。
 * 还款进度 repay_progress 与逾期 days_to_due / is_overdue 由服务端按今天现算,
 * 前端直接展示。点击卡片打开「详情」,详情里可「登记还款」——本金走自选银行卡、
 * 不进收支统计,利息记为正常支出,并自动生成对应的交易流水(与交易流水同形态)。
 * 「标记已结清」按钮已移除:结清状态由还款明细汇总(还清 → repaid)自动驱动。
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
  onDelete: (row: ReadLoan) => Promise<void> | void
  onRegisterRepayment: (
    row: ReadLoan,
    payload: LoanRepaymentCreatePayload,
  ) => Promise<boolean> | boolean
  onDeleteRepayment: (row: ReadLoan, repaymentId: string) => Promise<void> | void
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

function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

function genRepaymentId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return `rp-${c.randomUUID()}`
  return `rp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function LoansPanel({
  loans,
  accountOptions,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  onRegisterRepayment,
  onDeleteRepayment,
  canManage,
}: LoansPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadLoan | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 详情弹窗:用 id 派生,保证登记/撤销还款后随列表刷新同步。
  const [detailId, setDetailId] = useState<string | null>(null)
  const detailRow = useMemo(
    () => loans.find((r) => r.id === detailId) || null,
    [loans, detailId],
  )

  // 登记还款弹窗状态
  const [registerOpen, setRegisterOpen] = useState(false)
  const [rpAmount, setRpAmount] = useState('')
  const [rpInterest, setRpInterest] = useState('')
  const [rpAccountId, setRpAccountId] = useState('__none__')
  const [rpDate, setRpDate] = useState(todayStr())
  const [rpNote, setRpNote] = useState('')
  const [repaying, setRepaying] = useState(false)
  const [rpError, setRpError] = useState<string | null>(null)

  const [pendingDeleteRepayment, setPendingDeleteRepayment] = useState<LoanRepayment | null>(null)
  const [deletingRepayment, setDeletingRepayment] = useState(false)

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
      status: (row.status as LoanForm['status']) || 'active',
      account_id: row.account_id || '',
      account_name:
        row.account_name || (row.account_id ? accountNameById.get(row.account_id) || '' : ''),
      note: row.note || '',
    })
    setDetailId(null)
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

  const openRegister = () => {
    setRpAmount('')
    setRpInterest('')
    setRpAccountId('__none__')
    setRpDate(todayStr())
    setRpNote('')
    setRpError(null)
    setRegisterOpen(true)
  }

  const handleRegisterSubmit = async () => {
    if (!detailRow) return
    const amount = Number(rpAmount || 0)
    if (!Number.isFinite(amount) || amount <= 0) {
      setRpError(t('loans.repayment.amountRequired'))
      return
    }
    if (rpAccountId === '__none__') {
      setRpError(t('loans.repayment.accountRequired'))
      return
    }
    const remaining = (detailRow.principal || 0) - (detailRow.repaid_amount || 0)
    if (amount > remaining + 1e-9) {
      setRpError(t('loans.repayment.exceedsRemaining'))
      return
    }
    const interest = Number(rpInterest || 0)
    const acctName = accountNameById.get(rpAccountId) || ''
    const payload: LoanRepaymentCreatePayload = {
      repayment_id: genRepaymentId(),
      amount,
      interest_amount: Number.isFinite(interest) && interest > 0 ? interest : 0,
      repay_account_id: rpAccountId,
      repay_account_name: acctName || null,
      happened_at: rpDate,
      note: rpNote.trim() || null,
    }
    setRepaying(true)
    try {
      const ok = await onRegisterRepayment(detailRow, payload)
      if (ok) setRegisterOpen(false)
    } finally {
      setRepaying(false)
    }
  }

  const handleConfirmDeleteRepayment = async () => {
    if (!detailRow || !pendingDeleteRepayment) return
    setDeletingRepayment(true)
    try {
      await onDeleteRepayment(detailRow, pendingDeleteRepayment.id)
      setPendingDeleteRepayment(null)
    } finally {
      setDeletingRepayment(false)
    }
  }

  const errorText = formError ? t(`loans.error.${formError}`) : null
  const repayments = detailRow?.repayments || []
  const detailRemaining =
    (detailRow?.principal || 0) - (detailRow?.repaid_amount || 0)

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
            return (
              <div
                key={row.id}
                onClick={() => setDetailId(row.id)}
                className="cursor-pointer rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
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
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!canManage}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleOpenEdit(row)
                    }}
                  >
                    {t('common.edit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!canManage}
                    onClick={(e) => {
                      e.stopPropagation()
                      setPendingDelete(row)
                    }}
                  >
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

      {/* 详情 dialog */}
      <Dialog open={detailRow !== null} onOpenChange={(open) => { if (!open) setDetailId(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              <span className="flex items-center gap-2">
                {detailRow ? detailRow.counterparty || t('loans.untitled') : ''}
                {detailRow ? (
                  <Badge variant={STATUS_VARIANT[detailRow.status] || 'outline'}>
                    {t(`loans.status.${detailRow.status}`)}
                  </Badge>
                ) : null}
              </span>
            </DialogTitle>
          </DialogHeader>
          {detailRow ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-[11px] text-muted-foreground">{t('loans.detail.principal')}</div>
                  <Amount value={detailRow.principal} currency={currency} size="md" bold tone="default" />
                </div>
                <div>
                  <div className="text-[11px] text-muted-foreground">{t('loans.detail.remaining')}</div>
                  <Amount value={detailRemaining} currency={currency} size="md" bold tone="default" />
                </div>
                <div>
                  <div className="text-[11px] text-muted-foreground">{t('loans.field.direction')}</div>
                  <div>{t(`loans.direction.${detailRow.direction}`)}</div>
                </div>
                <div>
                  <div className="text-[11px] text-muted-foreground">{t('loans.field.account')}</div>
                  <div>{resolveAccountName(detailRow) || t('loans.field.accountNone')}</div>
                </div>
                <div>
                  <div className="text-[11px] text-muted-foreground">{t('loans.field.startedAt')}</div>
                  <div>{dateOnly(detailRow.started_at)}</div>
                </div>
                <div>
                  <div className="text-[11px] text-muted-foreground">{t('loans.field.dueAt')}</div>
                  <div>{detailRow.due_at ? dateOnly(detailRow.due_at) : '—'}</div>
                </div>
              </div>

              {detailRow.note ? (
                <p className="text-xs text-muted-foreground">{detailRow.note}</p>
              ) : null}

              {/* 还款记录 */}
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">{t('loans.detail.repayments')}</span>
                  {canManage ? (
                    <Button size="sm" variant="outline" onClick={openRegister}>
                      {t('loans.detail.registerRepayment')}
                    </Button>
                  ) : null}
                </div>
                {repayments.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{t('loans.detail.emptyRepayments')}</p>
                ) : (
                  <div className="space-y-2">
                    {repayments.map((rp) => (
                      <div
                        key={rp.id}
                        className="rounded-lg border border-border/60 bg-muted/30 p-3"
                      >
                        <div className="flex items-center justify-between">
                          <div className="text-sm">
                            <span className="font-medium">
                              {t('loans.repayment.amount')}: {Number(rp.amount || 0).toFixed(2)}
                            </span>
                            {Number(rp.interestAmount || 0) > 0 ? (
                              <span className="ml-2 text-muted-foreground">
                                {t('loans.repayment.interestAmount')}: {Number(rp.interestAmount || 0).toFixed(2)}
                              </span>
                            ) : null}
                          </div>
                          {canManage ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setPendingDeleteRepayment(rp)}
                            >
                              {t('loans.repayment.delete')}
                            </Button>
                          ) : null}
                        </div>
                        <div className="mt-0.5 text-[11px] text-muted-foreground">
                          {dateOnly(rp.happenedAt)}
                          {rp.repayAccountName ? ` · ${rp.repayAccountName}` : ''}
                        </div>
                        {rp.note ? (
                          <p className="mt-1 text-xs text-muted-foreground">{rp.note}</p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <DialogFooter>
                {canManage ? (
                  <Button
                    variant="outline"
                    onClick={() => {
                      handleOpenEdit(detailRow)
                    }}
                  >
                    {t('common.edit')}
                  </Button>
                ) : null}
                <Button variant="outline" onClick={() => setDetailId(null)}>
                  {t('dialog.close')}
                </Button>
              </DialogFooter>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* 登记还款 dialog */}
      <Dialog open={registerOpen} onOpenChange={setRegisterOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('loans.detail.registerRepayment')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('loans.repayment.amount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={rpAmount}
                  onChange={(e) => setRpAmount(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('loans.repayment.interestAmount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={rpInterest}
                  onChange={(e) => setRpInterest(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('loans.repayment.account')}</Label>
              <Select value={rpAccountId} onValueChange={setRpAccountId}>
                <SelectTrigger>
                  <SelectValue placeholder={t('loans.repayment.accountPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">
                    <span className="text-muted-foreground">{t('loans.repayment.accountPlaceholder')}</span>
                  </SelectItem>
                  {accountOptions.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('loans.repayment.happenedAt')}</Label>
                <Input type="date" value={rpDate} onChange={(e) => setRpDate(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>{t('loans.field.currency')}</Label>
                <Input value={currency} disabled />
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('loans.repayment.note')}</Label>
              <Textarea rows={2} value={rpNote} onChange={(e) => setRpNote(e.target.value)} />
            </div>

            <p className="text-[11px] text-muted-foreground">
              {t('loans.repayment.principalHint')}
              {Number(rpInterest || 0) > 0 ? ` ${t('loans.repayment.interestHint')}` : ''}
            </p>

            {rpError ? <p className="text-xs text-destructive">{rpError}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={repaying} onClick={() => setRegisterOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={repaying || !canManage} onClick={() => void handleRegisterSubmit()}>
              {t('loans.detail.registerRepayment')}
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

      <ConfirmDialog
        open={pendingDeleteRepayment !== null}
        onCancel={() => {
          if (!deletingRepayment) setPendingDeleteRepayment(null)
        }}
        onConfirm={() => void handleConfirmDeleteRepayment()}
        loading={deletingRepayment}
        title={t('loans.repayment.delete')}
        description={t('loans.repayment.deleteConfirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
