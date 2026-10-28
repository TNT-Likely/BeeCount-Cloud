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

import type { ReadTimeDeposit, ReadTransaction } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { TimeDepositForm } from '../forms'
import { timeDepositDefaults } from '../forms'
import { ratePercentFromWire } from '../lib/timeDepositRate'
import { payoutPlansTotal } from '../lib/timeDepositPayoutPlans'

/**
 * 0021 定期存款面板(App 端 issue #80 诉求)。
 *
 * 列表展示本金/利率/期限/到期日 + 服务端现算的 expected_interest /
 * days_remaining / is_matured;编辑弹窗管全部字段;holding/matured 状态
 * 额外提供「支取」动作(withdrawn_at + actual_interest,走 onWithdraw 回调)。
 */

export type TimeDepositAccountOption = {
  id: string
  name: string
}

type TimeDepositsPanelProps = {
  deposits: readonly ReadTimeDeposit[]
  /** 账户下拉候选(workspace accounts,去空名)。 */
  accountOptions: readonly TimeDepositAccountOption[]
  /** 列表金额展示用的默认币种(单条存款自带 currency 时优先用自己的)。 */
  currency: string
  form: TimeDepositForm
  onFormChange: (next: TimeDepositForm) => void
  /** 返回 false 时保持 dialog 打开(校验失败由 page 层 toast)。 */
  onSubmit: () => Promise<boolean> | boolean
  /** 支取:page 层 PATCH status=withdrawn + withdrawnAt + actualInterest。 */
  onWithdraw: (
    deposit: ReadTimeDeposit,
    payload: { withdrawn_at: string; actual_interest: number | null },
  ) => Promise<void> | void
  onDelete: (deposit: ReadTimeDeposit) => Promise<void> | void
  /** 0030 利息按键:点一下打开「新建收入交易」并预填到这笔存款(形成利息流水)。 */
  onAddInterest: (deposit: ReadTimeDeposit) => void
  /** 0030 拉某笔存款的关联交易(用于卡片内展开的关联交易列表)。 */
  onLoadDepositTransactions: (depositId: string) => Promise<ReadTransaction[]>
  canManage: boolean
}

/** 输入口径:年利率按「3.25 = 3.25%」,wire 是小数 → 回显 *100。 */
function rateFromWire(rate: number): string {
  return ratePercentFromWire(rate)
}

/** 'YYYY-MM-DD' / ISO datetime → 'YYYY-MM-DD'。 */
function dateOnly(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 10)
}

/** ISO datetime → <input type="datetime-local"> 需要的本地值 'YYYY-MM-DDTHH:mm'。 */
function toLocalInputValue(value: string | null | undefined): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 展示用:日期 + 时间(仅当时间不是 00:00 时才带时间,避免历史数据显示一堆 00:00)。 */
function dateTimeDisplay(value: string | null | undefined): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return dateOnly(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return d.getHours() || d.getMinutes() ? `${day} ${pad(d.getHours())}:${pad(d.getMinutes())}` : day
}

/** 0031:start + N 个月 → 同后端 _add_months 口径(月末溢出归当月最后一天);保留 'THH:mm'。 */
function addMonthsToDate(startDate: string, months: number): string {
  if (!startDate) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(startDate)
  if (!m) return ''
  const y = Number(m[1])
  const mo = Number(m[2])
  const d = Number(m[3])
  const time = m[4] ? `T${m[4]}:${m[5]}` : ''
  const total = y * 12 + (mo - 1) + months
  const ny = Math.floor(total / 12)
  const nm = (total % 12) + 1
  const lastDay = new Date(ny, nm, 0).getDate()
  const nd = Math.min(d, lastDay)
  return `${ny}-${String(nm).padStart(2, '0')}-${String(nd).padStart(2, '0')}${time}`
}

/** 0031:按当前 term/start 现算到期日,使「改期限即改到期/利息」即时可见。 */
function maturityFromForm(form: TimeDepositForm): string {
  return addMonthsToDate(form.start_date, Number(form.term_months) || 12)
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  holding: 'default',
  matured: 'secondary',
  withdrawn: 'outline'
}

export function TimeDepositsPanel({
  deposits,
  accountOptions,
  currency,
  form,
  onFormChange,
  onSubmit,
  onWithdraw,
  onDelete,
  onAddInterest,
  onLoadDepositTransactions,
  canManage
}: TimeDepositsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadTimeDeposit | null>(null)
  const [deleting, setDeleting] = useState(false)
  // 支取 dialog:null = 关闭;否则是对应存款 + 预填值
  const [withdrawTarget, setWithdrawTarget] = useState<{
    deposit: ReadTimeDeposit
    withdrawn_at: string
    actual_interest: string
  } | null>(null)
  const [withdrawing, setWithdrawing] = useState(false)
  // 0030 关联交易展开:单卡展开(一次只看一笔存款的利息流水)。
  const [expandedDepositId, setExpandedDepositId] = useState<string | null>(null)
  const [linkedRows, setLinkedRows] = useState<ReadTransaction[]>([])
  const [linkedLoading, setLinkedLoading] = useState(false)
  const toggleLinked = async (deposit: ReadTimeDeposit) => {
    if (expandedDepositId === deposit.id) {
      setExpandedDepositId(null)
      setLinkedRows([])
      return
    }
    setExpandedDepositId(deposit.id)
    setLinkedLoading(true)
    setLinkedRows([])
    try {
      const rows = await onLoadDepositTransactions(deposit.id)
      setLinkedRows(rows)
    } catch {
      setLinkedRows([])
    } finally {
      setLinkedLoading(false)
    }
  }

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.id, a.name)
    return map
  }, [accountOptions])

  const handleOpenCreate = () => {
    onFormChange(timeDepositDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (deposit: ReadTimeDeposit) => {
    onFormChange({
      editingId: deposit.id,
      principal: String(deposit.principal),
      currency: deposit.currency || 'CNY',
      annual_rate: rateFromWire(deposit.annual_rate),
      term_months: String(deposit.term_months || 12),
      start_date: toLocalInputValue(deposit.start_date),
      maturity_date:
        toLocalInputValue(deposit.maturity_date) ||
        addMonthsToDate(toLocalInputValue(deposit.start_date), deposit.term_months || 12),
      status: (deposit.status as TimeDepositForm['status']) || 'holding',
      account_id: deposit.account_id || '',
      account_name: deposit.account_name || (deposit.account_id ? accountNameById.get(deposit.account_id) || '' : ''),
      auto_renew: Boolean(deposit.auto_renew),
      interest_mode: (deposit.interest_mode as TimeDepositForm['interest_mode']) || 'simple',
      note: deposit.note || '',
      withdrawn_at: dateOnly(deposit.withdrawn_at),
      actual_interest:
        deposit.actual_interest === null || deposit.actual_interest === undefined
          ? ''
          : String(deposit.actual_interest),
      // 到息计划(0028,多笔):编辑时原样回填,提交时由转换器过滤无效行。
      payout_plans: (deposit.payout_plans || []).map((plan) => ({
        date: plan.date || '',
        amount: plan.amount === null || plan.amount === undefined ? '' : String(plan.amount),
        note: plan.note || ''
      }))
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

  const handleOpenWithdraw = (deposit: ReadTimeDeposit) => {
    setWithdrawTarget({
      deposit,
      withdrawn_at: new Date().toISOString().slice(0, 10),
      actual_interest:
        deposit.expected_interest > 0 ? String(Math.round(deposit.expected_interest * 100) / 100) : ''
    })
  }

  const handleConfirmWithdraw = async () => {
    if (!withdrawTarget) return
    setWithdrawing(true)
    try {
      const raw = withdrawTarget.actual_interest.trim()
      const parsed = raw === '' ? null : Number(raw)
      await onWithdraw(withdrawTarget.deposit, {
        withdrawn_at: withdrawTarget.withdrawn_at,
        actual_interest: parsed !== null && Number.isFinite(parsed) ? parsed : null
      })
      setWithdrawTarget(null)
    } finally {
      setWithdrawing(false)
    }
  }

  const isWithdrawn = (d: ReadTimeDeposit) => d.status === 'withdrawn'

  // --- 到息计划(0028,多笔)---------------------------------------------- #
  const setPlans = (plans: TimeDepositForm['payout_plans']) =>
    onFormChange({ ...form, payout_plans: plans })
  const addPlan = () =>
    setPlans([...form.payout_plans, { date: '', amount: '', note: '' }])
  const removePlan = (i: number) =>
    setPlans(form.payout_plans.filter((_, idx) => idx !== i))
  const updatePlan = (
    i: number,
    patch: Partial<TimeDepositForm['payout_plans'][number]>,
  ) => setPlans(form.payout_plans.map((plan, idx) => (idx === i ? { ...plan, ...patch } : plan)))
  const plansTotal = payoutPlansTotal(
    form.payout_plans.map((plan) => ({ amount: plan.amount })),
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('timeDeposits.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('timeDeposits.button.create')}
        </Button>
      </div>

      {deposits.length === 0 ? (
        <EmptyState
          icon={<span className="material-symbols-outlined text-2xl">savings</span>}
          title={t('timeDeposits.empty')}
          description={t('timeDeposits.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {deposits.map((d) => (
            <div
              key={d.id}
              className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
            >
              <div className="flex items-start gap-3">
                <span
                  aria-hidden
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                >
                  <span className="material-symbols-outlined text-2xl">savings</span>
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">
                      {d.account_name || accountNameById.get(d.account_id || '') || t('timeDeposits.noAccount')}
                    </span>
                    <Badge variant={STATUS_VARIANT[d.status] || 'outline'}>
                      {t(`timeDeposits.status.${d.status}`)}
                    </Badge>
                    {d.auto_renew ? (
                      <Badge variant="outline">{t('timeDeposits.autoRenew')}</Badge>
                    ) : null}
                    {d.interest_mode === 'compound' ? (
                      <Badge variant="outline">{t('timeDeposits.compound')}</Badge>
                    ) : null}
                  </div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">
                    {t('timeDeposits.rateLabel')} {(d.annual_rate * 100).toFixed(2)}% ·{' '}
                    {t('timeDeposits.termLabel')} {d.term_months} {t('timeDeposits.months')} ·{' '}
                    {dateTimeDisplay(d.start_date)} → {dateTimeDisplay(d.maturity_date)}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <Amount value={d.principal} currency={d.currency || currency} size="md" bold tone="default" />
                  <div className="mt-0.5 text-[11px] text-muted-foreground">
                    {t('timeDeposits.expectedInterest')}{' '}
                    <Amount
                      value={d.expected_interest}
                      currency={d.currency || currency}
                      size="xs"
                      tone="positive"
                    />
                  </div>
                </div>
              </div>

              <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {isWithdrawn(d)
                    ? `${t('timeDeposits.withdrawnAt')} ${dateOnly(d.withdrawn_at)}`
                    : d.is_matured
                      ? t('timeDeposits.maturedHint')
                      : t('timeDeposits.daysRemaining').replace('{n}', String(d.days_remaining))}
                </span>
                {d.note ? <span className="truncate pl-2">{d.note}</span> : null}
              </div>

              {/* 到息计划(0028,多笔):已到息的置灰,未到息的标出来。 */}
              {d.payout_plans && d.payout_plans.length > 0 ? (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {d.payout_plans.map((plan, idx) => (
                    <Badge
                      key={`${plan.date}-${idx}`}
                      variant="outline"
                      className={plan.is_due ? 'text-muted-foreground' : undefined}
                    >
                      {dateOnly(plan.date)} · {Number(plan.amount || 0).toFixed(2)}
                      {plan.note ? ` · ${plan.note}` : ''}
                      {plan.is_due ? '' : ` · ${t('timeDeposits.payoutPlans.pending')}`}
                    </Badge>
                  ))}
                </div>
              ) : null}

              {/* 0030 已收利息 + 关联交易(利息按键生成的真实入账流水) */}
              {(d.interest_tx_count || 0) > 0 ? (
                <div className="mt-2 space-y-1 rounded-lg border border-border/60 bg-primary/5 p-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">
                      {t('timeDeposits.interestReceived')}{' '}
                      <Amount
                        value={d.interest_received || 0}
                        currency={d.currency || currency}
                        size="xs"
                        tone="positive"
                      />
                      <span className="ml-1 text-muted-foreground">
                        {t('timeDeposits.interestTxCount').replace('{n}', String(d.interest_tx_count))}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-auto p-0 text-xs underline-offset-2 hover:underline"
                      onClick={() => void toggleLinked(d)}
                    >
                      {expandedDepositId === d.id
                        ? t('timeDeposits.linkedTxCollapse')
                        : t('timeDeposits.linkedTxExpand')}
                    </Button>
                  </div>
                  {expandedDepositId === d.id ? (
                    linkedLoading ? (
                      <p className="text-[11px] text-muted-foreground">{t('timeDeposits.linkedTxLoading')}</p>
                    ) : linkedRows.length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">{t('timeDeposits.linkedTxEmpty')}</p>
                    ) : (
                      <ul className="space-y-1">
                        {linkedRows.map((tx) => (
                          <li
                            key={tx.id}
                            className="flex items-center justify-between gap-2 text-[11px]"
                          >
                            <span className="truncate">
                              {dateOnly(tx.happened_at)} · {tx.category_name || t('timeDeposits.linkedTxNoCategory')}
                              {tx.note ? ` · ${tx.note}` : ''}
                            </span>
                            <Amount
                              value={tx.amount}
                              currency={tx.currency_code || d.currency || currency}
                              size="xs"
                              tone="positive"
                            />
                          </li>
                        ))}
                      </ul>
                    )
                  ) : null}
                </div>
              ) : null}

              <div className="mt-3 flex items-center justify-end gap-2">
                {!isWithdrawn(d) ? (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!canManage}
                      onClick={() => onAddInterest(d)}
                    >
                      {t('timeDeposits.button.interest')}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => handleOpenWithdraw(d)}>
                      {t('timeDeposits.button.withdraw')}
                    </Button>
                  </>
                ) : null}
                <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => handleOpenEdit(d)}>
                  {t('common.edit')}
                </Button>
                <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setPendingDelete(d)}>
                  {t('common.delete')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('timeDeposits.button.update') : t('timeDeposits.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('timeDeposits.field.principal')}</Label>
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
                <Label>{t('timeDeposits.field.currency')}</Label>
                <Input
                  value={form.currency}
                  maxLength={8}
                  placeholder="CNY"
                  onChange={(e) => onFormChange({ ...form, currency: e.target.value.toUpperCase() })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('timeDeposits.field.annualRate')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="3.25"
                  value={form.annual_rate}
                  onChange={(e) => onFormChange({ ...form, annual_rate: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('timeDeposits.field.termMonths')}</Label>
                <Input
                  type="number"
                  inputMode="numeric"
                  min="1"
                  max="600"
                  value={form.term_months}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      term_months: e.target.value,
                      maturity_date: addMonthsToDate(form.start_date, Number(e.target.value) || 12)
                    })
                  }
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('timeDeposits.field.startDate')}</Label>
                <Input
                  type="datetime-local"
                  value={form.start_date}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      start_date: e.target.value,
                      maturity_date: addMonthsToDate(e.target.value, Number(form.term_months) || 12)
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>{t('timeDeposits.field.maturityDate')}</Label>
                <Input
                  type="datetime-local"
                  value={form.maturity_date}
                  onChange={(e) => onFormChange({ ...form, maturity_date: e.target.value })}
                />
                <p className="text-[11px] text-muted-foreground">{t('timeDeposits.field.maturityHint')}</p>
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('timeDeposits.field.account')}</Label>
              <Select
                // 同 TransactionsPanel:Radix SelectItem 不允许 value="",
                // 用 sentinel "__none__" 表示"不关联账户"。
                value={form.account_id || '__none__'}
                onValueChange={(value) => {
                  if (value === '__none__') {
                    onFormChange({ ...form, account_id: '', account_name: '' })
                    return
                  }
                  const hit = accountOptions.find((a) => a.id === value)
                  onFormChange({
                    ...form,
                    account_id: value,
                    account_name: hit?.name || ''
                  })
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t('timeDeposits.field.accountPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">
                    <span className="text-muted-foreground">{t('timeDeposits.field.accountNone')}</span>
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
                <Label>{t('timeDeposits.field.interestMode')}</Label>
                <Select
                  value={form.interest_mode}
                  onValueChange={(value) =>
                    onFormChange({
                      ...form,
                      interest_mode: value as TimeDepositForm['interest_mode']
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="simple">{t('timeDeposits.interestMode.simple')}</SelectItem>
                    <SelectItem value="compound">{t('timeDeposits.interestMode.compound')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('timeDeposits.field.status')}</Label>
                <Select
                  value={form.status}
                  onValueChange={(value) =>
                    onFormChange({ ...form, status: value as TimeDepositForm['status'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="holding">{t('timeDeposits.status.holding')}</SelectItem>
                    <SelectItem value="matured">{t('timeDeposits.status.matured')}</SelectItem>
                    <SelectItem value="withdrawn">{t('timeDeposits.status.withdrawn')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {form.status === 'withdrawn' ? (
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label>{t('timeDeposits.field.withdrawnAt')}</Label>
                  <Input
                    type="date"
                    value={form.withdrawn_at}
                    onChange={(e) => onFormChange({ ...form, withdrawn_at: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label>{t('timeDeposits.field.actualInterest')}</Label>
                  <Input
                    type="number"
                    inputMode="decimal"
                    step="0.01"
                    placeholder="0.00"
                    value={form.actual_interest}
                    onChange={(e) => onFormChange({ ...form, actual_interest: e.target.value })}
                  />
                </div>
              </div>
            ) : null}

            {/* 到息计划(0028,多笔):每条 = 到息日 + 金额 + 备注,可增删。 */}
            <div className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-2">
              <div className="flex items-center justify-between">
                <Label>{t('timeDeposits.field.payoutPlans')}</Label>
                <Button type="button" size="sm" variant="ghost" onClick={addPlan}>
                  {t('timeDeposits.payoutPlans.add')}
                </Button>
              </div>

              {form.payout_plans.length === 0 ? (
                <p className="py-1 text-[11px] text-muted-foreground">
                  {t('timeDeposits.payoutPlans.empty')}
                </p>
              ) : (
                form.payout_plans.map((plan, i) => (
                  <div key={i} className="flex items-end gap-2">
                    <div className="flex-1 space-y-1">
                      <Input
                        type="date"
                        aria-label={t('timeDeposits.payoutPlans.date')}
                        value={plan.date}
                        onChange={(e) => updatePlan(i, { date: e.target.value })}
                      />
                    </div>
                    <div className="w-28 space-y-1">
                      <Input
                        type="number"
                        inputMode="decimal"
                        step="0.01"
                        min="0"
                        placeholder="0.00"
                        aria-label={t('timeDeposits.payoutPlans.amount')}
                        value={plan.amount}
                        onChange={(e) => updatePlan(i, { amount: e.target.value })}
                      />
                    </div>
                    <div className="flex-1 space-y-1">
                      <Input
                        placeholder={t('timeDeposits.payoutPlans.notePlaceholder')}
                        value={plan.note}
                        onChange={(e) => updatePlan(i, { note: e.target.value })}
                      />
                    </div>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      aria-label={t('common.delete')}
                      onClick={() => removePlan(i)}
                    >
                      <span className="material-symbols-outlined text-sm">close</span>
                    </Button>
                  </div>
                ))
              )}

              {form.payout_plans.length > 0 ? (
                <div className="flex items-center justify-end">
                  <span className="text-[11px] text-muted-foreground">
                    {t('timeDeposits.payoutPlans.total').replace('{total}', plansTotal.toFixed(2))}
                  </span>
                </div>
              ) : null}
            </div>

            <div className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2">
              <Label className="text-sm">{t('timeDeposits.field.autoRenew')}</Label>
              <button
                type="button"
                role="switch"
                aria-checked={form.auto_renew}
                aria-label={t('timeDeposits.field.autoRenew')}
                onClick={() => onFormChange({ ...form, auto_renew: !form.auto_renew })}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                  form.auto_renew ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    form.auto_renew ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>

            <div className="space-y-1">
              <Label>{t('timeDeposits.field.note')}</Label>
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
              {form.editingId ? t('timeDeposits.button.update') : t('timeDeposits.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 支取 dialog */}
      <Dialog open={withdrawTarget !== null} onOpenChange={(open) => !open && setWithdrawTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('timeDeposits.withdraw.title')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('timeDeposits.withdraw.date')}</Label>
              <Input
                type="date"
                value={withdrawTarget?.withdrawn_at || ''}
                onChange={(e) =>
                  setWithdrawTarget((prev) => (prev ? { ...prev, withdrawn_at: e.target.value } : prev))
                }
              />
            </div>
            <div className="space-y-1">
              <Label>{t('timeDeposits.withdraw.actualInterest')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                placeholder="0.00"
                value={withdrawTarget?.actual_interest || ''}
                onChange={(e) =>
                  setWithdrawTarget((prev) => (prev ? { ...prev, actual_interest: e.target.value } : prev))
                }
              />
              <p className="text-[11px] text-muted-foreground">
                {t('timeDeposits.withdraw.hint').replace(
                  '{expected}',
                  String(
                    withdrawTarget
                      ? Math.round(withdrawTarget.deposit.expected_interest * 100) / 100
                      : 0
                  )
                )}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={withdrawing} onClick={() => setWithdrawTarget(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={withdrawing} onClick={() => void handleConfirmWithdraw()}>
              {t('timeDeposits.withdraw.confirm')}
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
        title={t('timeDeposits.delete.title')}
        description={t('timeDeposits.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
