import { useMemo } from 'react'

import {
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'

import type { RefundForm, RefundLegForm } from '../forms'
import type { RefundAccountOption } from '../features/RefundsPanel'
import {
  refundLegsFromPaymentLegs,
  refundLegsTotal,
  toNum,
} from '../lib/txExtensions'

/**
 * 退款去向(0025):统一退至一张卡 / 分别退至多张卡。
 *
 * 抽成独立组件,RefundsPanel 与 TransactionDetailDialog 的「登记退款」弹窗共用,
 * 保证两处交互完全一致。split 模式下提供「按原分摊填充」(从原交易合并付款腿
 * 等比缩放)与逐卡金额输入,并实时显示合计是否等于退款金额。
 */

export type RefundDestinationFieldsProps = {
  form: RefundForm
  onFormChange: (next: RefundForm) => void
  accountOptions: readonly RefundAccountOption[]
  /** 原交易的合并付款腿(payment_legs),用于「按原分摊」一键填充;无则省略。 */
  originalPaymentLegs?: readonly { accountId: string; accountName?: string | null; amount: number }[] | null
}

/** 进入 split 模式时的初始腿:优先按原合并付款等比缩放,否则退化成单卡(沿用统一模式的账户)。 */
function deriveInitialLegs(
  form: RefundForm,
  originalPaymentLegs?: readonly { accountId: string; accountName?: string | null; amount: number }[] | null,
): RefundLegForm[] {
  if (originalPaymentLegs && originalPaymentLegs.length) {
    return refundLegsFromPaymentLegs(originalPaymentLegs, toNum(form.amount))
  }
  if (form.account_id) {
    return [{ account_id: form.account_id, account_name: form.account_name, amount: form.amount }]
  }
  return []
}

export function RefundDestinationFields({
  form,
  onFormChange,
  accountOptions,
  originalPaymentLegs,
}: RefundDestinationFieldsProps) {
  const t = useT()
  const mode = form.refund_mode || 'unified'

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.id, a.name)
    return map
  }, [accountOptions])

  const handleModeChange = (next: 'unified' | 'split') => {
    if (next === mode) return
    if (next === 'split') {
      onFormChange({ ...form, refund_mode: 'split', refund_legs: deriveInitialLegs(form, originalPaymentLegs) })
      return
    }
    // split → unified:把首条腿的账户带回统一模式,方便继续用。
    const first = form.refund_legs[0]
    onFormChange({
      ...form,
      refund_mode: 'unified',
      account_id: first?.account_id || form.account_id,
      account_name: first?.account_name || form.account_name,
    })
  }

  const setLegs = (legs: RefundLegForm[]) => onFormChange({ ...form, refund_legs: legs })
  const addLeg = () => setLegs([...form.refund_legs, { account_id: '', account_name: '', amount: '' }])
  const removeLeg = (i: number) => setLegs(form.refund_legs.filter((_, idx) => idx !== i))
  const updateLeg = (i: number, patch: Partial<RefundLegForm>) =>
    setLegs(form.refund_legs.map((l, idx) => (idx === i ? { ...l, ...patch } : l)))
  const fillFromOriginal = () => {
    if (!originalPaymentLegs || !originalPaymentLegs.length) return
    setLegs(refundLegsFromPaymentLegs(originalPaymentLegs, toNum(form.amount)))
  }

  const legsSum = refundLegsTotal(form.refund_legs)
  const amountNum = toNum(form.amount)
  const legsMismatch = mode === 'split' && form.refund_legs.length > 0 && Math.abs(legsSum - amountNum) > 0.011

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <Label>{t('refunds.field.refundMode')}</Label>
        <Select value={mode} onValueChange={(v) => handleModeChange(v as 'unified' | 'split')}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="unified">{t('refunds.mode.unified')}</SelectItem>
            <SelectItem value="split">{t('refunds.mode.split')}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {mode === 'unified' ? (
        <div className="space-y-1">
          <Label>{t('refunds.field.account')}</Label>
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
              <SelectValue placeholder={t('refunds.field.accountPlaceholder')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">
                <span className="text-muted-foreground">{t('refunds.field.accountNone')}</span>
              </SelectItem>
              {accountOptions.map((a) => (
                <SelectItem key={a.id} value={a.id}>
                  {a.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <div className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-2">
          <div className="flex items-center justify-between">
            <Label>{t('refunds.field.legs')}</Label>
            {originalPaymentLegs && originalPaymentLegs.length > 0 ? (
              <Button type="button" size="sm" variant="ghost" onClick={fillFromOriginal}>
                {t('refunds.legs.fillFromOriginal')}
              </Button>
            ) : null}
          </div>

          {form.refund_legs.length === 0 ? (
            <p className="py-1 text-[11px] text-muted-foreground">{t('refunds.legs.empty')}</p>
          ) : (
            form.refund_legs.map((leg, i) => (
              <div key={i} className="flex items-end gap-2">
                <div className="flex-1 space-y-1">
                  <Select
                    value={leg.account_id || '__none__'}
                    onValueChange={(value) => {
                      if (value === '__none__') {
                        updateLeg(i, { account_id: '', account_name: '' })
                        return
                      }
                      const hit = accountOptions.find((a) => a.id === value)
                      updateLeg(i, { account_id: value, account_name: hit?.name || '' })
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('refunds.field.accountPlaceholder')} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">
                        <span className="text-muted-foreground">{t('refunds.field.accountNone')}</span>
                      </SelectItem>
                      {accountOptions.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="w-28 space-y-1">
                  <Input
                    type="number"
                    inputMode="decimal"
                    step="0.01"
                    min="0"
                    placeholder="0.00"
                    value={leg.amount}
                    onChange={(e) => updateLeg(i, { amount: e.target.value })}
                  />
                </div>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  aria-label={t('common.delete')}
                  onClick={() => removeLeg(i)}
                >
                  <span className="material-symbols-outlined text-sm">close</span>
                </Button>
              </div>
            ))
          )}

          <div className="flex items-center justify-between">
            <Button type="button" size="sm" variant="outline" onClick={addLeg}>
              {t('refunds.legs.add')}
            </Button>
            <span className={`text-[11px] ${legsMismatch ? 'text-destructive' : 'text-muted-foreground'}`}>
              {t('refunds.legs.total').replace('{total}', legsSum.toFixed(2))}
            </span>
          </div>
          {legsMismatch ? (
            <p className="text-[11px] text-destructive">{t('refunds.legs.mismatchHint')}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
