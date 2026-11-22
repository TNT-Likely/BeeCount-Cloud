import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'
import type {
  ReadAccount,
  ReadTransaction,
  WriteCommitMeta,
} from '@beecount/api-client'
import {
  createTransaction,
  fetchReadAccounts,
  fetchReadLedgerDetail,
} from '@beecount/api-client'

const formatMoney = (value: number) =>
  value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })

/** ISO(UTC,Z)→ 本地 datetime-local 字符串(含秒),用于输入框回显。 */
function isoToLocalInput(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 本地 datetime-local 字符串 → 后端 ISO(UTC,Z)。保留用户输入的秒。 */
function localInputToIso(local: string): string {
  if (!local) return new Date().toISOString()
  const d = new Date(local)
  if (Number.isNaN(d.getTime())) return new Date().toISOString()
  return d.toISOString()
}

interface Props {
  /** 被退的原单(必须是 expense;调用方已确保)。 */
  original: ReadTransaction | null
  token: string
  /** 写回账本 id —— 退款必须落在原单所在账本。 */
  ledgerId: string
  canManage?: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 提交成功(已创建退款)后回调:外层关弹窗 + 触发列表刷新。 */
  onSubmitted: () => void
}

/** 退款弹窗(0025) —— 强制从原单发起,选择退回账户 + 金额(默认可退余额)。
 *
 * 不污染月度收支 / 不计收入,这些口径由后端 analytics 自然保证;前端只负责把
 * refund_of_sync_id / account_name / amount 正确传给写接口。 */
export function RefundDialog({
  original,
  token,
  ledgerId,
  canManage = true,
  open,
  onOpenChange,
  onSubmitted,
}: Props) {
  const t = useT()
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [baseChangeId, setBaseChangeId] = useState(0)
  const [accountName, setAccountName] = useState('')
  const [amount, setAmount] = useState('')
  const [happenedAt, setHappenedAt] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const maxRefundable = useMemo(() => {
    if (!original) return 0
    const refunded = typeof original.refunded_amount === 'number' ? original.refunded_amount : 0
    return Math.max(0, Math.round((original.amount - refunded) * 100) / 100)
  }, [original])

  // 打开时拉账户 + 账本 base_change_id,并把表单默认值填好。
  useEffect(() => {
    if (!open || !original) return
    setAccountName(original.account_name || '')
    setAmount(formatMoney(maxRefundable))
    setHappenedAt(new Date().toISOString())
    setError(null)
    void fetchReadAccounts(token, ledgerId)
      .then(setAccounts)
      .catch(() => setAccounts([]))
    void fetchReadLedgerDetail(token, ledgerId)
      .then((detail) => setBaseChangeId(detail.source_change_id))
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, original?.id])

  const amountNum = Number((amount || '').toString().trim())
  const amountValid = Number.isFinite(amountNum) && amountNum > 0
  const withinLimit =
    Math.round(amountNum * 100) <= Math.round(maxRefundable * 100) + 1
  const canSubmit =
    canManage &&
    Boolean(accountName.trim()) &&
    amountValid &&
    withinLimit &&
    !saving

  const submit = async () => {
    if (!original || !canSubmit) return
    setSaving(true)
    setError(null)
    try {
      const payload = {
        tx_type: 'refund' as const,
        amount: Math.round(amountNum * 100) / 100,
        happened_at: localInputToIso(happenedAt),
        // 退款备注由后端按规则派生(原交易备注),前端不再传自定义备注。
        account_name: accountName.trim(),
        refund_of_sync_id: original.id,
      }
      const meta: WriteCommitMeta = await createTransaction(
        token,
        ledgerId,
        baseChangeId,
        payload,
      )
      if (meta && typeof meta.new_change_id === 'number') {
        onSubmitted()
      } else {
        setError(t('common.error.unexpected') as string)
      }
    } catch (err) {
      const msg =
        (err as { message?: string })?.message ||
        (t('common.error.unexpected') as string)
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !saving && onOpenChange(v)}>
      <DialogContent className="flex max-h-[90dvh] max-w-md flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle>{t('refund.detail.title')}</DialogTitle>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {original ? (
            <p className="text-xs text-muted-foreground">
              {t('refund.detail.intro', {
                note: original.note || t('common.none'),
                amount: formatMoney(original.amount),
                refunded: formatMoney(
                  typeof original.refunded_amount === 'number'
                    ? original.refunded_amount
                    : 0,
                ),
              })}
            </p>
          ) : null}

          <div className="space-y-1">
            <Label>{t('refund.detail.receiveAccount')}</Label>
            <Select
              value={accountName ? accountName : '__none__'}
              disabled={saving || accounts.length === 0}
              onValueChange={(value) =>
                setAccountName(value === '__none__' ? '' : value)
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={t('transactions.placeholder.accountName')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">
                  <span className="text-muted-foreground">
                    {t('transactions.placeholder.noAccount')}
                  </span>
                </SelectItem>
                {accounts
                  .map((a) => (a.name || '').trim())
                  .filter(Boolean)
                  .filter((name, index, self) => self.indexOf(name) === index)
                  .sort((a, b) => a.localeCompare(b))
                  .map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {t('refund.detail.receiveAccountHint')}
            </p>
          </div>

          <div className="space-y-1">
            <Label>{t('refund.detail.amount')}</Label>
            <Input
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={amount}
              disabled={saving}
              onChange={(e) => setAmount(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('refund.detail.amountHint')}
              {' · '}
              {t('refund.detail.fullAmount')}: {formatMoney(maxRefundable)}
            </p>
            <p className="rounded bg-muted/40 px-2 py-1 text-xs text-muted-foreground">
              {t('refund.listHint')}
            </p>
          </div>

          <div className="space-y-1">
            <Label>{t('refund.detail.time')}</Label>
            <Input
              type="datetime-local"
              step={1}
              value={isoToLocalInput(happenedAt)}
              disabled={saving}
              onChange={(e) => setHappenedAt(localInputToIso(e.target.value))}
            />
          </div>

          {!withinLimit && amountValid ? (
            <p className="text-xs text-destructive">
              {t('refund.detail.maxExceed')}
            </p>
          ) : null}
          {error ? (
            <p className="text-xs text-destructive">{error}</p>
          ) : null}
        </div>

        <DialogFooter className="flex flex-row items-center justify-end gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
          <Button
            variant="outline"
            size="sm"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            {t('dialog.cancel')}
          </Button>
          <Button
            size="sm"
            disabled={!canSubmit}
            onClick={() => void submit()}
          >
            {t('refund.detail.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
