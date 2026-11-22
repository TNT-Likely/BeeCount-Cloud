import { useSearchParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Banknote, Edit3, Landmark, PiggyBank, Plus, TrendingUp } from 'lucide-react'

import {
  type Deposit,
  type DepositInterestPayment,
  type DepositInterestSettlement,
  type DepositOnMaturity,
  type DepositStatus,
  type DepositType,
  type ReadAccount,
  createDeposit,
  deleteDeposit,
  deleteDepositInterestPayment,
  fetchDeposit,
  fetchDepositInterestPayments,
  fetchDeposits,
  fetchReadAccounts,
  renewDeposit,
  settleDepositInterest,
  updateDeposit,
  withdrawDeposit,
} from '@beecount/api-client'
import {
  useT,
  useToast,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
  Button,
  Input,
  Textarea,
  Label,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@beecount/ui'
import {
  Amount,
  formatBalanceCompact,
  formatIsoDateTimeLocal,
  toLocalDateTimeInput as toLocalInput,
  fromLocalDateTimeInput as fromLocalInput,
  SectionPageHeader,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { openTxDetailBySyncId, onOpenDetailDeposit } from '../../lib/txDialogEvents'
import { useLedgers } from '../../context/LedgersContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import {
  LoanAttachmentEditorField,
  LoanAttachmentThumbs,
  type LoanAttachmentEditorHandle,
} from '../../components/LoanAttachments'

const TYPES: DepositType[] = ['fixed', 'installment', 'cd', 'structured']
const STATUSES: DepositStatus[] = ['holding', 'matured', 'withdrawn', 'renewed']
const TERM_PRESETS: Array<{ months: number; key: string }> = [
  { months: 3, key: 'm3' },
  { months: 6, key: 'm6' },
  { months: 12, key: 'y1' },
  { months: 24, key: 'y2' },
  { months: 36, key: 'y3' },
  { months: 60, key: 'y5' },
]

/** 与后端 deposit_calc 同口径:到期利息 = 本金 × 年利率 × 月数 / 12。 */
function calcInterest(principal: number, rate: number | null, months: number): number {
  if (!rate) return 0
  return Math.round(principal * (rate / 100) * (months / 12) * 100) / 100
}

/** 与后端 add_months 同口径:日历月加法,月末自动收敛。 */
function addMonthsLocal(iso: string, months: number): Date | null {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const total = d.getMonth() + months
  const year = d.getFullYear() + Math.floor(total / 12)
  const month = ((total % 12) + 12) % 12
  const lastDay = new Date(year, month + 1, 0).getDate()
  return new Date(year, month, Math.min(d.getDate(), lastDay), d.getHours(), d.getMinutes(), d.getSeconds())
}

function statusBadgeClass(status: DepositStatus): string {
  switch (status) {
    case 'withdrawn':
      return 'bg-emerald-500/15 text-emerald-600'
    case 'matured':
      return 'bg-red-500/15 text-red-600'
    case 'renewed':
      return 'bg-amber-500/15 text-amber-600'
    default:
      return 'bg-primary/10 text-primary'
  }
}

function typeIcon(type: DepositType, sizeClass = 'h-4 w-4') {
  switch (type) {
    case 'cd':
      return <Landmark className={`${sizeClass} text-primary`} />
    case 'structured':
      return <TrendingUp className={`${sizeClass} text-primary`} />
    case 'installment':
      return <PiggyBank className={`${sizeClass} text-primary`} />
    default:
      return <Banknote className={`${sizeClass} text-primary`} />
  }
}

/** 数字转中文大写金额（银行存单样式）。 */
function toChineseUppercase(num: number): string {
  if (!Number.isFinite(num)) return ''
  const digits = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
  const units = ['', '拾', '佰', '仟']
  const bigUnits = ['', '万', '亿', '兆']
  const neg = num < 0
  const abs = Math.abs(num)
  const intPart = Math.floor(abs)
  const frac = Math.round((abs - intPart) * 100)
  // 整数部分
  let intStr = intPart === 0 ? '零' : ''
  let n = intPart
  let groupIdx = 0
  const groups: string[] = []
  while (n > 0) {
    const group = n % 10000
    groups.unshift(String(group).padStart(4, '0'))
    n = Math.floor(n / 10000)
    groupIdx++
  }
  for (let g = 0; g < groups.length; g++) {
    const gstr = groups[g]
    let seg = ''
    let zeroFlag = false
    for (let i = 0; i < 4; i++) {
      const d = parseInt(gstr[i], 10)
      if (d === 0) {
        zeroFlag = true
      } else {
        if (zeroFlag) { seg += '零'; zeroFlag = false }
        seg += digits[d] + units[3 - i]
      }
    }
    const bigIdx = groups.length - 1 - g
    if (seg) {
      if (zeroFlag && seg.endsWith('零')) seg = seg.slice(0, -1)
      intStr += seg + bigUnits[bigIdx]
    } else if (bigIdx > 0) {
      // 万/亿位为零时仍需保留大单位占位? 银行通常省略; 这里保持简洁
    }
  }
  if (intStr === '') intStr = '零'
  // 小数部分
  const jiao = Math.floor(frac / 10)
  const fen = frac % 10
  let fracStr = ''
  if (jiao > 0) fracStr += digits[jiao] + '角'
  if (fen > 0) fracStr += digits[fen] + '分'
  if (fracStr === '') fracStr = '整'
  const result = intStr + '元' + fracStr
  return (neg ? '负' : '') + result
}

function maturityCountdown(t: (k: string) => string, days: number | null): string {
  if (days == null) return ''
  if (days < 0) return t('deposits.countdown.overdue').replace('{days}', String(Math.abs(days)))
  if (days === 0) return t('deposits.countdown.today')
  return t('deposits.countdown.days').replace('{days}', String(days))
}

/** 存单详情 KPI 单元格 — 与分类详情弹窗的 KpiCell 视觉一致。 */
function DepositKpi({
  label,
  children,
  onClick,
}: {
  label: string
  children: ReactNode
  /** 点击跳转到来源流水;提供时整格可点并高亮。 */
  onClick?: () => void
}) {
  return (
    <div
      className={onClick ? 'cursor-pointer rounded-md transition-colors hover:bg-muted/60' : ''}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 flex min-h-7 flex-col items-center justify-center">{children}</div>
    </div>
  )
}

// --------------------------------------------------------------------------
// 新建 / 编辑存单弹窗
// --------------------------------------------------------------------------

function DepositFormDialog({
  open,
  editing,
  currency,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  editing: Deposit | null
  currency: string
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [name, setName] = useState('')
  const [depositType, setDepositType] = useState<DepositType>('fixed')
  const [principal, setPrincipal] = useState('')
  const [startedAt, setStartedAt] = useState('')
  const [termPreset, setTermPreset] = useState('y1')
  const [termCustom, setTermCustom] = useState('')
  const [annualRate, setAnnualRate] = useState('')
  const [interestOverride, setInterestOverride] = useState('')
  const [onMaturity, setOnMaturity] = useState<DepositOnMaturity>('withdraw')
  const [interestSettlement, setInterestSettlement] = useState<DepositInterestSettlement>('maturity')
  const [settlementDay, setSettlementDay] = useState('21')
  const [note, setNote] = useState('')
  const [accountId, setAccountId] = useState('none')
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const attachmentRef = useRef<LoanAttachmentEditorHandle>(null)

  useEffect(() => {
    if (!open) return
    if (editing) {
      setName(editing.name)
      setDepositType(editing.deposit_type)
      setPrincipal(String(editing.principal))
      setStartedAt(toLocalInput(editing.started_at))
      const preset = TERM_PRESETS.find((p) => p.months === editing.term_months)
      setTermPreset(preset ? preset.key : 'custom')
      setTermCustom(preset ? '' : String(editing.term_months))
      setAnnualRate(editing.annual_rate != null ? String(editing.annual_rate) : '')
      setInterestOverride(editing.interest_override != null ? String(editing.interest_override) : '')
      setOnMaturity(editing.on_maturity)
      setInterestSettlement(editing.interest_settlement)
      setSettlementDay(editing.settlement_day != null ? String(editing.settlement_day) : '21')
      setNote(editing.note || '')
      setAccountId(editing.account_id || 'none')
    } else {
      setName('')
      setDepositType('fixed')
      setPrincipal('')
      setStartedAt(toLocalInput(new Date().toISOString()))
      setTermPreset('y1')
      setTermCustom('')
      setAnnualRate('')
      setInterestOverride('')
      setOnMaturity('withdraw')
      setInterestSettlement('maturity')
      setSettlementDay('21')
      setNote('')
      setAccountId('none')
    }
  }, [open, editing])

  useEffect(() => {
    if (!open || !token || !activeLedgerId) return
    let cancelled = false
    setAccountsLoading(true)
    fetchReadAccounts(token, activeLedgerId)
      .then((rows) => {
        if (!cancelled) {
          const visible = rows.filter((a) => !a.hidden)
          // 编辑回填:原账户即使已被隐藏也 pin 进选项,否则 Select 显示为空。
          if (editing?.account_id && !visible.some((a) => a.id === editing.account_id)) {
            visible.unshift({
              id: editing.account_id,
              name: editing.account_name || editing.account_id,
              hidden: false,
            } as ReadAccount)
          }
          setAccounts(visible)
        }
      })
      .catch(() => { if (!cancelled) setAccounts([]) })
      .finally(() => { if (!cancelled) setAccountsLoading(false) })
    return () => { cancelled = true }
  }, [open, token, activeLedgerId, editing])

  const termMonths = useMemo(() => {
    if (termPreset !== 'custom') {
      const preset = TERM_PRESETS.find((p) => p.key === termPreset)
      return preset ? preset.months : 12
    }
    const n = parseInt(termCustom, 10)
    return Number.isFinite(n) && n >= 1 && n <= 600 ? n : 0
  }, [termPreset, termCustom])

  const principalNum = parseFloat(principal)
  const rateNum = parseFloat(annualRate)
  const autoInterest = Number.isFinite(principalNum) && Number.isFinite(rateNum) && termMonths > 0
    ? calcInterest(principalNum, rateNum, termMonths)
    : 0
  const overrideNum = parseFloat(interestOverride)
  const finalInterest = interestOverride.trim() !== '' && Number.isFinite(overrideNum) ? overrideNum : autoInterest
  const maturityPreview = useMemo(() => {
    const iso = fromLocalInput(startedAt)
    if (!iso || termMonths <= 0) return null
    return addMonthsLocal(iso, termMonths)
  }, [startedAt, termMonths])

  const save = async () => {
    if (!token || !activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    if (!name.trim()) {
      toast.error(t('deposits.field.name') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    if (!Number.isFinite(principalNum) || principalNum <= 0) {
      toast.error(t('deposits.field.principal') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const started = fromLocalInput(startedAt)
    if (!started) {
      toast.error(t('deposits.field.startedAt') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    if (termMonths <= 0) {
      toast.error(t('deposits.field.term') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const selectedAccountId = accountId === 'none' ? null : accountId
    if (!selectedAccountId) {
      toast.error(t('deposits.notice.accountRequired'), t('notice.error'))
      return
    }
    const selectedAccount = accounts.find((a) => a.id === selectedAccountId)
    setSaving(true)
    try {
      const certificateIds = await attachmentRef.current?.prepare()
      if (certificateIds == null) {
        toast.error(t('loans.attachment.uploadFailed'), t('notice.error'))
        return
      }
      const body = {
        ledger_id: activeLedgerId,
        name: name.trim(),
        deposit_type: depositType,
        principal: principalNum,
        currency_code: currency,
        happened_at: started,
        term_months: termMonths,
        annual_rate: annualRate.trim() !== '' && Number.isFinite(rateNum) ? rateNum : null,
        interest_override: interestOverride.trim() !== '' && Number.isFinite(overrideNum) ? overrideNum : null,
        on_maturity: onMaturity,
        interest_settlement: interestSettlement,
        settlement_day: interestSettlement === 'maturity' ? null : parseInt(settlementDay, 10) || 21,
        note: note || null,
        certificate_file_ids: certificateIds,
        account_id: selectedAccountId,
        account_name: selectedAccount?.name ?? null,
      }
      if (editing) await updateDeposit(token, editing.id, body)
      else await createDeposit(token, body)
      toast.success(editing ? t('deposits.notice.updated') : t('deposits.notice.created'), t('notice.success'))
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? t('deposits.button.update') : t('deposits.button.create')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('deposits.field.name')}</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('deposits.field.namePlaceholder')} />
            </div>
            <div className="space-y-1">
              <Label>{t('deposits.field.type')}</Label>
              <Select value={depositType} onValueChange={(v) => setDepositType(v as DepositType)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TYPES.map((d) => (
                    <SelectItem key={d} value={d}>{t(`deposits.type.${d}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('deposits.field.principal')}</Label>
              <Input type="number" inputMode="decimal" min="0" step="0.01" value={principal} onChange={(e) => setPrincipal(e.target.value)} placeholder="10000" />
            </div>
            <div className="space-y-1">
              <Label>{t('deposits.field.annualRate')}</Label>
              <Input type="number" inputMode="decimal" min="0" step="0.01" value={annualRate} onChange={(e) => setAnnualRate(e.target.value)} placeholder="2.75" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('deposits.field.startedAt')}</Label>
              <Input type="datetime-local" step="1" value={startedAt} onChange={(e) => setStartedAt(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>{t('deposits.field.term')}</Label>
              <div className="flex gap-2">
                <Select value={termPreset} onValueChange={setTermPreset}>
                  <SelectTrigger className="w-[130px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {TERM_PRESETS.map((p) => (
                      <SelectItem key={p.key} value={p.key}>{t(`deposits.term.${p.key}`)}</SelectItem>
                    ))}
                    <SelectItem value="custom">{t('deposits.term.custom')}</SelectItem>
                  </SelectContent>
                </Select>
                {termPreset === 'custom' ? (
                  <Input type="number" min="1" step="1" className="flex-1" value={termCustom} onChange={(e) => setTermCustom(e.target.value)} placeholder={t('deposits.term.months')} />
                ) : null}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('deposits.field.interest')}</Label>
              <Input type="number" inputMode="decimal" min="0" step="0.01" value={interestOverride} onChange={(e) => setInterestOverride(e.target.value)} placeholder={String(autoInterest)} />
              <p className="text-xs text-muted-foreground">
                {t('deposits.hint.interestAuto')}: {formatBalanceCompact(autoInterest, currency)}
              </p>
            </div>
            <div className="space-y-1">
              <Label>{t('deposits.field.maturityAt')}</Label>
              <div className="flex h-9 items-center rounded-md border border-border bg-muted/30 px-3 text-sm text-muted-foreground">
                {maturityPreview ? formatIsoDateTimeLocal(maturityPreview.toISOString()) : '—'}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('deposits.field.onMaturity')}</Label>
              <Select value={onMaturity} onValueChange={(v) => setOnMaturity(v as DepositOnMaturity)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="withdraw">{t('deposits.maturity.withdraw')}</SelectItem>
                  <SelectItem value="auto_renew">{t('deposits.maturity.autoRenew')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('deposits.field.interestSettlement')}</Label>
              <div className="flex gap-2">
                <Select value={interestSettlement} onValueChange={(v) => setInterestSettlement(v as DepositInterestSettlement)}>
                  <SelectTrigger className="w-[130px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="maturity">{t('deposits.settlement.maturity')}</SelectItem>
                    <SelectItem value="monthly">{t('deposits.settlement.monthly')}</SelectItem>
                    <SelectItem value="quarterly">{t('deposits.settlement.quarterly')}</SelectItem>
                  </SelectContent>
                </Select>
                {interestSettlement !== 'maturity' ? (
                  <Input type="number" min="1" max="28" step="1" className="flex-1" value={settlementDay} onChange={(e) => setSettlementDay(e.target.value)} placeholder={t('deposits.settlement.day')} />
                ) : null}
              </div>
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('deposits.field.account')}</Label>
            <Select value={accountId} onValueChange={setAccountId}>
              <SelectTrigger>
                <SelectValue placeholder={accountsLoading ? t('notice.loading') : t('deposits.field.account')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t('loans.field.noAccount')}</SelectItem>
                {accounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>{t('deposits.field.note')}</Label>
            <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <LoanAttachmentEditorField
            key={`${open}-${editing?.id || 'new'}`}
            ref={attachmentRef}
            fileIds={editing?.certificate_file_ids || []}
            title={t('deposits.attachment.title')}
          />
          <div className="rounded-lg bg-primary/5 px-3 py-2 text-sm">
            <span className="text-muted-foreground">{t('deposits.hint.total')}</span>
            <span className="ml-2 font-semibold text-primary">
              {formatBalanceCompact((Number.isFinite(principalNum) ? principalNum : 0) + finalInterest, currency)}
            </span>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? t('notice.loading') : t('confirm.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// --------------------------------------------------------------------------
// 支取 / 转存 / 结息 弹窗(按 mode 复用)
// --------------------------------------------------------------------------

type ActionMode = 'withdraw' | 'renew' | 'settle'

function DepositActionDialog({
  open,
  mode,
  deposit,
  currency,
  onOpenChange,
  onDone,
}: {
  open: boolean
  mode: ActionMode
  deposit: Deposit | null
  currency: string
  onOpenChange: (v: boolean) => void
  onDone: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [at, setAt] = useState('')
  const [accountId, setAccountId] = useState('none')
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [interestAmount, setInterestAmount] = useState('')
  const [note, setNote] = useState('')
  const [newRate, setNewRate] = useState('')
  const [newTerm, setNewTerm] = useState('')
  const [newPrincipal, setNewPrincipal] = useState('')
  const [includeInterest, setIncludeInterest] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open || !deposit) return
    setAt(toLocalInput(new Date().toISOString()))
    setAccountId(deposit.account_id || 'none')
    setNote('')
    setIncludeInterest(true)
    const remain = Math.max(0, Math.round((deposit.expected_interest - deposit.settled_interest) * 100) / 100)
    setInterestAmount(String(remain))
    setNewRate(deposit.annual_rate != null ? String(deposit.annual_rate) : '')
    setNewTerm(String(deposit.term_months))
    setNewPrincipal('')
    setAccounts([])
  }, [open, deposit])

  useEffect(() => {
    if (!open || !token || !activeLedgerId) return
    let cancelled = false
    fetchReadAccounts(token, activeLedgerId)
      .then((rows) => {
        if (cancelled) return
        const visible = rows.filter((a) => !a.hidden)
        if (deposit?.account_id && !visible.some((a) => a.id === deposit.account_id)) {
          visible.unshift({
            id: deposit.account_id,
            name: deposit.account_name || deposit.account_id,
            hidden: false,
          } as ReadAccount)
        }
        setAccounts(visible)
      })
      .catch(() => { if (!cancelled) setAccounts([]) })
    return () => { cancelled = true }
  }, [open, token, activeLedgerId, deposit])

  const remainInterest = deposit
    ? Math.max(0, Math.round((deposit.expected_interest - deposit.settled_interest) * 100) / 100)
    : 0
  const interestNum = parseFloat(interestAmount)
  const withdrawTotal = deposit ? deposit.principal + (Number.isFinite(interestNum) ? interestNum : remainInterest) : 0

  const save = async () => {
    if (!token || !deposit) return
    const atIso = fromLocalInput(at)
    if (!atIso) {
      toast.error(t('deposits.field.time') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const selectedAccountId = accountId === 'none' ? null : accountId
    const selectedAccount = accounts.find((a) => a.id === selectedAccountId)
    setSaving(true)
    try {
      if (mode === 'withdraw') {
        await withdrawDeposit(token, deposit.id, {
          withdrawn_at: atIso,
          account_id: selectedAccountId,
          account_name: selectedAccount?.name ?? null,
          interest_amount: interestOverrideValid() ? interestNum : null,
        })
        toast.success(t('deposits.notice.withdrawn'), t('notice.success'))
      } else if (mode === 'renew') {
        const termNum = parseInt(newTerm, 10)
        await renewDeposit(token, deposit.id, {
          new_rate: newRate.trim() !== '' ? parseFloat(newRate) : null,
          new_term_months: Number.isFinite(termNum) && termNum > 0 ? termNum : null,
          started_at: atIso,
          include_interest: includeInterest,
          new_principal: newPrincipal.trim() !== '' ? parseFloat(newPrincipal) : null,
          account_id: selectedAccountId,
          account_name: selectedAccount?.name ?? null,
        })
        toast.success(t('deposits.notice.renewed'), t('notice.success'))
      } else {
        const amount = parseFloat(interestAmount)
        if (!Number.isFinite(amount) || amount <= 0) {
          toast.error(t('deposits.field.interest') + ' ' + t('notice.required'), t('notice.error'))
          return
        }
        await settleDepositInterest(token, deposit.id, {
          amount,
          settled_at: atIso,
          note: note || null,
          account_id: selectedAccountId,
          account_name: selectedAccount?.name ?? null,
        })
        toast.success(t('deposits.notice.settled'), t('notice.success'))
      }
      onDone()
      onOpenChange(false)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  const interestOverrideValid = () =>
    interestAmount.trim() !== '' && Number.isFinite(interestNum) && interestNum >= 0

  const title =
    mode === 'withdraw' ? t('deposits.withdraw.title')
    : mode === 'renew' ? t('deposits.renew.title')
    : t('deposits.settle.title')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {deposit ? (
            <DialogDescription>
              {deposit.name} · {formatBalanceCompact(deposit.principal, currency)}
              {mode !== 'settle' ? ` · ${t('deposits.field.interest')} ${formatBalanceCompact(remainInterest, currency)}` : ''}
            </DialogDescription>
          ) : null}
        </DialogHeader>
        <div className="space-y-3">
          {mode === 'withdraw' ? (
            <div className="rounded-lg bg-emerald-500/5 px-3 py-2 text-sm">
              <span className="text-muted-foreground">{t('deposits.withdraw.totalHint')}</span>
              <span className="ml-2 font-semibold text-emerald-600">{formatBalanceCompact(withdrawTotal, currency)}</span>
            </div>
          ) : null}
          {mode === 'renew' ? (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>{t('deposits.renew.newRate')}</Label>
                <Input type="number" min="0" step="0.01" value={newRate} onChange={(e) => setNewRate(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>{t('deposits.renew.newTerm')}</Label>
                <Input type="number" min="1" step="1" value={newTerm} onChange={(e) => setNewTerm(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>{t('deposits.field.time')}</Label>
                <Input type="datetime-local" step="1" value={at} onChange={(e) => setAt(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>{t('deposits.renew.newPrincipal')}</Label>
                <Input type="number" min="0" step="0.01" value={newPrincipal} onChange={(e) => setNewPrincipal(e.target.value)} placeholder={t('deposits.renew.principalAuto')} />
              </div>
              <label className="col-span-2 flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={includeInterest}
                  onChange={(e) => setIncludeInterest(e.target.checked)}
                  className="h-4 w-4 rounded border-border"
                />
                {t('deposits.renew.includeInterest')}
              </label>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label>{t('deposits.field.time')}</Label>
                  <Input type="datetime-local" step="1" value={at} onChange={(e) => setAt(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label>{t('deposits.field.interest')}</Label>
                  <Input type="number" min="0" step="0.01" value={interestAmount} onChange={(e) => setInterestAmount(e.target.value)} />
                </div>
              </div>
              {mode === 'settle' ? (
                <div className="space-y-1">
                  <Label>{t('deposits.field.note')}</Label>
                  <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
                </div>
              ) : null}
            </>
          )}
          <div className="space-y-1">
            <Label>{t('deposits.field.account')}</Label>
            <Select value={accountId} onValueChange={setAccountId}>
              <SelectTrigger>
                <SelectValue placeholder={t('deposits.field.account')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t('loans.field.noAccount')}</SelectItem>
                {accounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? t('notice.loading') : t('confirm.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// --------------------------------------------------------------------------
// 存单详情弹窗
// --------------------------------------------------------------------------

function DepositDetailDialog({
  open,
  deposit,
  currency,
  onOpenChange,
  onEdit,
  onWithdraw,
  onRenew,
  onSettle,
  onChanged,
}: {
  open: boolean
  deposit: Deposit | null
  currency: string
  onOpenChange: (v: boolean) => void
  onEdit: (d: Deposit) => void
  onWithdraw: (d: Deposit) => void
  onRenew: (d: Deposit) => void
  onSettle: (d: Deposit) => void
  onChanged: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const [payments, setPayments] = useState<DepositInterestPayment[]>([])

  useEffect(() => {
    if (!open || !deposit || !token) {
      setPayments([])
      return
    }
    let cancelled = false
    fetchDepositInterestPayments(token, deposit.id)
      .then((rows) => { if (!cancelled) setPayments(rows) })
      .catch(() => { if (!cancelled) setPayments([]) })
    return () => { cancelled = true }
  }, [open, deposit, token])

  const deletePayment = async (paymentId: string) => {
    if (!token) return
    try {
      await deleteDepositInterestPayment(token, paymentId)
      setPayments((prev) => prev.filter((p) => p.id !== paymentId))
      onChanged()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  if (!deposit) return null
  const active = deposit.status === 'holding' || deposit.status === 'matured'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex flex-row items-start justify-between gap-3 border-b border-border/60 bg-gradient-to-br from-muted/40 to-transparent px-6 py-4">
          <DialogTitle className="flex items-center gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              {typeIcon(deposit.deposit_type, 'h-6 w-6')}
            </span>
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-lg font-semibold">{deposit.name}</span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[11px] font-normal uppercase tracking-[0.18em] text-primary">
                <span>{t(`deposits.type.${deposit.deposit_type}`)}</span>
                <span className="text-muted-foreground/60">·</span>
                <span className={`rounded-full px-1.5 py-0.5 normal-case tracking-normal ${statusBadgeClass(deposit.status)}`}>
                  {t(`deposits.status.${deposit.status}`)}
                </span>
              </span>
            </div>
          </DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {/* KPI 行:本金 / 年利率 / 预期利息 / 已结息 */}
            <div className="grid grid-cols-2 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center sm:grid-cols-4">
              <DepositKpi
                label={t('deposits.field.principal')}
                onClick={() =>
                  openTxDetailBySyncId(
                    token,
                    deposit.flow_tx_sync_id || deposit.tx_sync_details?.[0]?.sync_id,
                    deposit.ledger_id,
                    () => onOpenChange(false),
                  )
                }
              >
                <Amount
                  value={deposit.principal}
                  currency={deposit.currency_code || currency}
                  size="md"
                  bold
                />
                <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">
                  {toChineseUppercase(deposit.principal)}
                </span>
              </DepositKpi>
              <DepositKpi label={t('deposits.field.annualRate')}>
                <span className="font-mono text-xl font-bold tabular-nums">
                  {deposit.annual_rate != null ? `${deposit.annual_rate}%` : '—'}
                </span>
              </DepositKpi>
              <DepositKpi label={t('deposits.field.interest')}>
                <Amount
                  value={deposit.expected_interest}
                  currency={deposit.currency_code || currency}
                  size="md"
                  bold
                  tone="positive"
                />
                {deposit.interest_override != null ? (
                  <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">
                    ({t('deposits.hint.manual')})
                  </span>
                ) : null}
              </DepositKpi>
              <DepositKpi label={t('deposits.detail.settled')}>
                {deposit.settled_interest > 0 ? (
                  <Amount
                    value={deposit.settled_interest}
                    currency={deposit.currency_code || currency}
                    size="md"
                    bold
                    tone="positive"
                  />
                ) : (
                  <span className="font-mono text-xl text-muted-foreground">—</span>
                )}
              </DepositKpi>
            </div>

            {/* 明细字段 */}
            <div className="border-b border-border/60 px-6 py-4">
              <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <div>
                  <span className="text-muted-foreground">{t('deposits.field.type')}</span>
                  <div>{t(`deposits.type.${deposit.deposit_type}`)}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">{t('deposits.field.term')}</span>
                  <div>{t('deposits.term.monthsValue').replace('{months}', String(deposit.term_months))}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">{t('deposits.field.startedAt')}</span>
                  <div>{formatIsoDateTimeLocal(deposit.started_at)}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">{t('deposits.field.maturityAt')}</span>
                  <div>
                    {deposit.maturity_at ? formatIsoDateTimeLocal(deposit.maturity_at) : '—'}
                    {active && deposit.days_to_maturity != null ? (
                      <span className={`ml-1 text-xs ${deposit.days_to_maturity < 0 ? 'text-red-600' : 'text-amber-600'}`}>
                        {maturityCountdown(t, deposit.days_to_maturity)}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div>
                  <span className="text-muted-foreground">{t('deposits.field.account')}</span>
                  <div>{deposit.account_name || t('loans.field.noAccount')}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">{t('deposits.field.onMaturity')}</span>
                  <div>{deposit.on_maturity === 'auto_renew' ? t('deposits.maturity.autoRenew') : t('deposits.maturity.withdraw')}</div>
                </div>
                <div className="sm:col-span-2">
                  <span className="text-muted-foreground">{t('deposits.field.interestSettlement')}</span>
                  <div>
                    {t(`deposits.settlement.${deposit.interest_settlement}`)}
                    {deposit.interest_settlement !== 'maturity' && deposit.settlement_day ? (
                      ` · ${t('deposits.settlement.day')} ${deposit.settlement_day}`
                    ) : ''}
                  </div>
                </div>
              </div>

              {deposit.status === 'withdrawn' && deposit.withdrawn_at ? (
                <div className="mt-3 rounded-lg bg-emerald-500/5 px-3 py-2 text-sm">
                  <span className="text-muted-foreground">{t('deposits.detail.withdrawnAt')}</span>
                  <span className="ml-2">{formatIsoDateTimeLocal(deposit.withdrawn_at)}</span>
                  {deposit.withdrawn_interest != null ? (
                    <span className="ml-2 text-emerald-600">
                      {t('deposits.field.interest')} {formatBalanceCompact(deposit.withdrawn_interest, deposit.currency_code || currency)}
                    </span>
                  ) : null}
                </div>
              ) : null}
              {deposit.status === 'renewed' && deposit.renewed_at ? (
                <div className="mt-3 rounded-lg bg-amber-500/5 px-3 py-2 text-sm">
                  <span className="text-muted-foreground">{t('deposits.detail.renewedAt')}</span>
                  <span className="ml-2">{formatIsoDateTimeLocal(deposit.renewed_at)}</span>
                </div>
              ) : null}
              {deposit.parent_deposit_id ? (
                <div className="mt-3 text-xs text-muted-foreground">{t('deposits.detail.fromRenewal')}</div>
              ) : null}
            </div>

            {deposit.note ? (
              <div className="border-b border-border/60 px-6 py-3 text-sm">
                <div className="rounded-lg bg-muted/40 px-3 py-2">{deposit.note}</div>
              </div>
            ) : null}

            <div className="border-b border-border/60 px-6 py-3">
              <LoanAttachmentThumbs
                fileIds={deposit.certificate_file_ids}
                label={t('deposits.attachment.title')}
              />
            </div>

            {payments.length > 0 ? (
              <div className="border-b border-border/60 px-6 py-4">
                <div className="mb-2 text-[11px] uppercase tracking-widest text-muted-foreground">
                  {t('deposits.settlement.history')} · {payments.length}
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {payments.map((p) => {
                    const paySyncId = p.tx_sync_id || p.tx_sync_details?.[0]?.sync_id
                    return (
                      <div
                        key={p.id}
                        className={`rounded-md border border-border px-2.5 py-1.5 text-sm ${
                          paySyncId ? 'cursor-pointer transition-colors hover:border-primary/60 hover:bg-primary/5' : ''
                        }`}
                        onClick={() => paySyncId && openTxDetailBySyncId(token, paySyncId, p.ledger_id || deposit.ledger_id, () => onOpenChange(false))}
                        title={paySyncId ? (t('deposits.detail.viewFlow') as string) : undefined}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="min-w-0 truncate">
                            <Amount
                              value={p.amount}
                              currency={deposit.currency_code || currency}
                              size="sm"
                              bold
                              tone="positive"
                            />
                            <span className="ml-2 text-xs text-muted-foreground">{formatIsoDateTimeLocal(p.settled_at)}</span>
                            {p.note ? <span className="ml-2 truncate text-xs text-muted-foreground">{p.note}</span> : null}
                          </span>
                          <button
                            type="button"
                            className="shrink-0 text-xs text-destructive hover:underline"
                            onClick={(e) => { e.stopPropagation(); void deletePayment(p.id) }}
                          >
                            {t('confirm.delete')}
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            ) : null}
          </div>

          <div className="flex flex-row flex-wrap items-center justify-end gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
            <Button variant="outline" size="sm" onClick={() => onEdit(deposit)}>
              <Edit3 className="mr-1 h-3.5 w-3.5" />
              {t('common.edit')}
            </Button>
            {active ? (
              <>
                <Button variant="outline" size="sm" onClick={() => onSettle(deposit)}>{t('deposits.button.settle')}</Button>
                <Button variant="outline" size="sm" onClick={() => onRenew(deposit)}>{t('deposits.button.renew')}</Button>
                <Button size="sm" onClick={() => onWithdraw(deposit)}>{t('deposits.button.withdraw')}</Button>
              </>
            ) : null}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// --------------------------------------------------------------------------
// 主页面
// --------------------------------------------------------------------------

export function DepositsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { currency, activeLedgerId } = useLedgers()

  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [statusFilter, setStatusFilter] = useState<'all' | DepositStatus>('all')
  const [typeFilter, setTypeFilter] = useState<'all' | DepositType>('all')
  const [loading, setLoading] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Deposit | null>(null)
  const [detail, setDetail] = useState<Deposit | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  // 关联记录跳转(0043):?deposit_id=xxx → 自动打开该存单详情。
  const [searchParams] = useSearchParams()
  const presetDepositId = searchParams.get('deposit_id')
  const [actionOpen, setActionOpen] = useState(false)
  const [actionMode, setActionMode] = useState<ActionMode>('withdraw')

  const load = useCallback(async () => {
    if (!token || !activeLedgerId) {
      setDeposits([])
      return
    }
    setLoading(true)
    try {
      setDeposits(await fetchDeposits(token, activeLedgerId))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setLoading(false)
    }
  }, [token, activeLedgerId, toast, t])

  useEffect(() => { void load() }, [load])
  useSyncRefresh(() => { void load() })

  const activeDeposits = useMemo(
    () => deposits.filter((d) => d.status === 'holding' || d.status === 'matured'),
    [deposits]
  )
  const totalPrincipal = activeDeposits.reduce((s, d) => s + d.principal, 0)
  const totalInterest = activeDeposits.reduce((s, d) => s + d.expected_interest, 0)
  const totalSettled = deposits.reduce((s, d) => s + d.settled_interest, 0)
  const maturedCount = deposits.filter((d) => d.status === 'matured').length
  const nearCount = activeDeposits.filter(
    (d) => d.days_to_maturity != null && d.days_to_maturity >= 0 && d.days_to_maturity <= 7
  ).length

  const visible = useMemo(
    () => deposits.filter((d) => {
      if (statusFilter !== 'all' && d.status !== statusFilter) return false
      if (typeFilter !== 'all' && d.deposit_type !== typeFilter) return false
      return true
    }),
    [deposits, statusFilter, typeFilter]
  )

  const refreshDetail = useCallback(async () => {
    const id = detail?.id
    if (!token || !id) return
    try {
      setDetail(await fetchDeposit(token, id))
    } catch {
      // 详情刷新失败由列表刷新兜底。
    }
    void load()
  }, [token, load, detail?.id])

  const openCreate = () => { setEditing(null); setFormOpen(true) }
  const openDetail = (d: Deposit) => { setDetail(d); setDetailOpen(true) }

  // 关联记录跳转(0043):拉起指定存单的详情。
  const openedDepositRef = useRef<string | null>(null)
  useEffect(() => {
    if (!token || !presetDepositId || !activeLedgerId) return
    if (openedDepositRef.current === presetDepositId) return
    openedDepositRef.current = presetDepositId
    void fetchDeposit(token, presetDepositId)
      .then((d) => {
        setDetail(d)
        setDetailOpen(true)
      })
      .catch(() => undefined)
  }, [token, presetDepositId, activeLedgerId])

  // 流水详情「关联记录」跳回存单:同页面时 URL 参数 effect 的 ref guard
  // 会拦截,靠此事件直接打开(跨页面时两条通道都会触发,setDetail 幂等)。
  useEffect(() => {
    if (!token) return
    return onOpenDetailDeposit((depositId) => {
      void fetchDeposit(token, depositId)
        .then((d) => {
          setDetail(d)
          setDetailOpen(true)
        })
        .catch(() => undefined)
    })
  }, [token])
  const openAction = (d: Deposit, mode: ActionMode) => {
    setActionMode(mode)
    setDetail(d)
    setActionOpen(true)
  }

  const deleteOne = async (d: Deposit) => {
    if (!token) return
    if (!window.confirm(t('deposits.confirm.delete').replace('{name}', d.name))) return
    try {
      await deleteDeposit(token, d.id)
      toast.success(t('deposits.notice.deleted'), t('notice.success'))
      void load()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  return (
    <div className="space-y-4">
      <SectionPageHeader
        section="deposits"
        title={t('deposits.title')}
        subtitle={t('deposits.subtitle')}
        actions={
          <Button onClick={openCreate}>
            <Plus className="mr-1 h-4 w-4" />{t('deposits.button.create')}
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('deposits.summary.total')}</div>
          <div className="mt-1 text-lg font-semibold text-primary">{formatBalanceCompact(totalPrincipal, currency)}</div>
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('deposits.summary.interest')}</div>
          <div className="mt-1 text-lg font-semibold text-emerald-600">{formatBalanceCompact(totalInterest, currency)}</div>
          <div className="mt-1 text-xs text-emerald-600/80">
            {t('deposits.summary.settled')}: {formatBalanceCompact(totalSettled, currency)}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('deposits.summary.count')}</div>
          <div className="mt-1 text-lg font-semibold">{activeDeposits.length}</div>
          {maturedCount > 0 ? (
            <div className="mt-1 text-xs text-red-600">{t('deposits.summary.matured')} · {maturedCount}</div>
          ) : null}
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('deposits.summary.nearDue')}</div>
          <div className={`mt-1 text-lg font-semibold ${nearCount > 0 ? 'text-amber-600' : ''}`}>{nearCount}</div>
          {nearCount > 0 ? (
            <div className="mt-1 text-xs text-amber-600">{t('deposits.summary.nearDueHint')}</div>
          ) : null}
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('deposits.summary.remainingInterest')}</div>
          <div className="mt-1 text-lg font-semibold text-amber-600">{formatBalanceCompact(Math.max(0, totalInterest - totalSettled), currency)}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Chip active={statusFilter === 'all'} onClick={() => setStatusFilter('all')}>{t('loans.filter.all')}</Chip>
        {STATUSES.map((s) => (
          <Chip key={s} active={statusFilter === s} onClick={() => setStatusFilter(s)}>{t(`deposits.status.${s}`)}</Chip>
        ))}
        <span className="mx-1 w-px self-stretch bg-border" />
        <Chip active={typeFilter === 'all'} onClick={() => setTypeFilter('all')}>{t('deposits.filter.allTypes')}</Chip>
        {TYPES.map((tp) => (
          <Chip key={tp} active={typeFilter === tp} onClick={() => setTypeFilter(tp)}>{t(`deposits.type.${tp}`)}</Chip>
        ))}
      </div>

      {!activeLedgerId ? (
        <div className="rounded-xl border border-dashed border-border py-12 text-center text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</div>
      ) : loading && deposits.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border py-12 text-center">
          <div className="font-medium">{t('deposits.empty.title')}</div>
          <div className="mt-1 text-sm text-muted-foreground">{t('deposits.empty.desc')}</div>
        </div>
      ) : (
        <div className="space-y-2">
          {visible.map((d) => {
            const active = d.status === 'holding' || d.status === 'matured'
            return (
              <div
                key={d.id}
                className="rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/50"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    {typeIcon(d.deposit_type)}
                    <button
                      type="button"
                      className="truncate text-left font-semibold hover:text-primary"
                      onClick={() => openDetail(d)}
                    >
                      {d.name}
                    </button>
                    <span className="shrink-0 text-xs text-muted-foreground">{t(`deposits.type.${d.deposit_type}`)}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${statusBadgeClass(d.status)}`}>
                      {t(`deposits.status.${d.status}`)}
                    </span>
                    <button type="button" className="text-xs text-destructive hover:underline" onClick={() => void deleteOne(d)}>
                      {t('confirm.delete')}
                    </button>
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
                  <div>
                    <span className="text-xs text-muted-foreground">{t('deposits.field.principal')}</span>
                    <div className={d.status === 'withdrawn' ? 'text-muted-foreground line-through' : 'font-semibold'}>
                      {formatBalanceCompact(d.principal, d.currency_code || currency)}
                    </div>
                    <div className="text-xs text-muted-foreground">{toChineseUppercase(d.principal)}</div>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">{t('deposits.field.interest')}</span>
                    <div>
                      {formatBalanceCompact(d.expected_interest, d.currency_code || currency)}
                      {d.settled_interest > 0 ? (
                        <span className="ml-1 text-xs text-emerald-600">
                          {t('deposits.summary.settled')}: {formatBalanceCompact(d.settled_interest, d.currency_code || currency)}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">{t('deposits.field.maturityAt')}</span>
                    <div>
                      {d.maturity_at ? formatIsoDateTimeLocal(d.maturity_at) : '—'}
                      {active && d.days_to_maturity != null ? (
                        <span className={`ml-1 text-xs ${d.days_to_maturity < 0 ? 'text-red-600' : 'text-amber-600'}`}>
                          {maturityCountdown(t, d.days_to_maturity)}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">{t('deposits.field.annualRate')}</span>
                    <div>{d.annual_rate != null ? `${d.annual_rate}% · ${t('deposits.term.monthsValue').replace('{months}', String(d.term_months))}` : '—'}</div>
                  </div>
                </div>
                {d.account_name ? (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {t('deposits.field.account')}: {d.account_name}
                    {d.parent_deposit_id ? ` · ${t('deposits.detail.fromRenewal')}` : ''}
                  </div>
                ) : null}
                {active ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button type="button" className="text-xs text-primary hover:underline" onClick={() => openAction(d, 'settle')}>{t('deposits.button.settle')}</button>
                    <button type="button" className="text-xs text-primary hover:underline" onClick={() => openAction(d, 'renew')}>{t('deposits.button.renew')}</button>
                    <button type="button" className="text-xs text-primary hover:underline" onClick={() => openAction(d, 'withdraw')}>{t('deposits.button.withdraw')}</button>
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      )}

      <DepositFormDialog
        open={formOpen}
        editing={editing}
        currency={currency}
        onOpenChange={setFormOpen}
        onSaved={() => void load()}
      />
      <DepositDetailDialog
        open={detailOpen}
        deposit={detail}
        currency={currency}
        onOpenChange={setDetailOpen}
        onEdit={(d) => { setDetailOpen(false); setEditing(d); setFormOpen(true) }}
        onWithdraw={(d) => { setDetailOpen(false); openAction(d, 'withdraw') }}
        onRenew={(d) => { setDetailOpen(false); openAction(d, 'renew') }}
        onSettle={(d) => { setDetailOpen(false); openAction(d, 'settle') }}
        onChanged={() => void refreshDetail()}
      />
      <DepositActionDialog
        open={actionOpen}
        mode={actionMode}
        deposit={detail}
        currency={currency}
        onOpenChange={setActionOpen}
        onDone={() => void refreshDetail()}
      />
    </div>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm transition-colors ${
        active ? 'border-primary bg-primary/15 text-primary' : 'border-border text-muted-foreground hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}
