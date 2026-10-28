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
  useT
} from '@beecount/ui'

import type { TxForm } from '../forms'
import {
  autoFillLegAmount,
  computeDiscount,
  legsRemaining,
  legsTotal,
  resolveTxExtensionSections,
  round2,
  toNum,
} from '../lib/txExtensions'
import { evenShares, sharesTotal } from '../lib/members'

/**
 * 0021 交易扩展字段编辑区(优惠/折扣、多账户合并付款、转账手续费)
 * + 0024 成员分摊 + 0029 归物 + 0030 定期存款利息。
 *
 * 每个区块是否渲染一律由 {@link resolveTxExtensionSections} 纯函数裁决:
 * - expense → 折扣 + 合并付款 + 成员分摊(有成员时)+ 归物(有物品时)
 * - income  → 归物(有物品时)+ **关联定期存款**(有存款时,利息收入挂靠)
 * - transfer → 手续费
 *
 * 0030 修复:这里原本有 `if (!isExpense && !isTransfer) return null` 的早退,
 * 把为 income 写的「归物 / 关联定期存款」两个区块变成**永远不可达的死代码** ——
 * 在交易流水里记一笔利息收入时看不到「关联定期存款」下拉。早退已移除,可见性
 * 全部下沉到纯函数并由单测锁住。
 *
 * 所有金额派生(原价/优惠额/剩余待分配)走 lib 纯函数,组件只渲染。
 */

export type TxExtensionEditorProps = {
  form: TxForm
  onFormChange: (next: TxForm) => void
  /** 去重排序后的账户名列表(已排除隐藏账户)。 */
  accountOptions: string[]
  /** name → syncId,提交 legs / feeAccount 时解析 id 用。 */
  accountIdByName: Map<string, string>
  /** 隐藏账户名集合(legs 选择器同样要显示灰标)。 */
  hiddenAccountNames: Set<string>
  /** 0024 可选成员(账本内自定义成员),给「成员分摊」下拉用;没成员时不展示该区块。 */
  memberOptions?: readonly { id: string; name: string }[]
  /** 0029 归物候选(物品持有),给「关联物品」下拉用;没物品时不展示该区块。
   *  挂上后这笔支出计入该物品的累计成本。 */
  itemOptions?: readonly { id: string; name: string }[]
  /** 0030 定期存款候选,给「关联定期存款」下拉用(仅收入);没存款时不展示该区块。
   *  挂上后这笔收入(利息)计入该存款的「已收利息」。 */
  depositOptions?: readonly { id: string; name: string }[]
  disabled?: boolean
}

/** 跟 panel 内标记开关同款的开关样式。 */
function Switch({
  checked,
  onChange,
  label
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
        checked ? 'bg-primary' : 'bg-muted-foreground/30'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-[18px]' : 'translate-x-0.5'
        }`}
      />
    </button>
  )
}

export function TxExtensionEditor({
  form,
  onFormChange,
  accountOptions,
  accountIdByName,
  hiddenAccountNames,
  memberOptions = [],
  itemOptions = [],
  depositOptions = [],
  disabled
}: TxExtensionEditorProps) {
  const t = useT()
  const amountNum = toNum(form.amount)
  // 0030:各区块可见性由纯函数裁决(避免早退把 income 区块变成不可达死代码)。
  const sections = resolveTxExtensionSections(form.tx_type, {
    hasMembers: memberOptions.length > 0,
    hasItems: itemOptions.length > 0,
    hasDeposits: depositOptions.length > 0,
  })

  const discountPreview = useMemo(
    () =>
      form.discount.discount_enabled
        ? computeDiscount(form.discount.discount_mode, form.discount.discount_value, amountNum)
        : null,
    [form.discount, amountNum]
  )

  const legsSummary = useMemo(() => {
    const total = legsTotal(form.payment_legs)
    return { total, remaining: legsRemaining(form.payment_legs, amountNum) }
  }, [form.payment_legs, amountNum])

  // 已选过腿的账户不再出现在其它腿的下拉里(避免重复账户);保底留当前值。
  const legAccountOptions = (current: string) => {
    const usedByOthers = new Set(
      form.payment_legs
        .filter((leg) => leg.account_name.trim().toLowerCase() !== current.trim().toLowerCase())
        .map((leg) => leg.account_name.trim().toLowerCase())
    )
    return accountOptions.filter((name) => !usedByOthers.has(name.toLowerCase()))
  }

  // 已选过成员不再出现在其它行的下拉里(避免同一个人重复分摊);保底留当前值。
  const shareMemberOptions = (current: string) => {
    const usedByOthers = new Set(
      form.member_shares
        .filter((s) => s.member_id !== current)
        .map((s) => s.member_id)
        .filter(Boolean)
    )
    return memberOptions.filter((m) => !usedByOthers.has(m.id))
  }

  const sharesSummary = useMemo(() => {
    const total = sharesTotal(form.member_shares)
    return { total, remaining: round2(amountNum - total) }
  }, [form.member_shares, amountNum])

  const nameByMemberId = useMemo(() => {
    const map = new Map<string, string>()
    for (const m of memberOptions) map.set(m.id, m.name)
    return map
  }, [memberOptions])

  /** 均分:已选成员按当前行均分;一行都没选则按全部成员均分。 */
  const splitEvenly = () => {
    const picked = form.member_shares.map((s) => s.member_id).filter(Boolean)
    const ids = picked.length > 0 ? picked : memberOptions.map((m) => m.id)
    const shares = evenShares(ids, amountNum, nameByMemberId)
    onFormChange({
      ...form,
      member_shares: shares.map((s) => ({
        member_id: s.memberId,
        member_name: s.memberName || '',
        amount: String(s.amount)
      }))
    })
  }

  return (
    <>
      {/* ---------------- 优惠/折扣(expense) ---------------- */}
      {sections.discount ? (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 md:col-span-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{t('txExt.discount.title')}</p>
            <Switch
              checked={form.discount.discount_enabled}
              onChange={(next) =>
                onFormChange({
                  ...form,
                  discount: { ...form.discount, discount_enabled: next }
                })
              }
              label={t('txExt.discount.enable') as string}
            />
          </div>
          {form.discount.discount_enabled ? (
            <div className="grid gap-2 md:grid-cols-3">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t('txExt.discount.mode')}
                </Label>
                <Select
                  value={form.discount.discount_mode}
                  onValueChange={(value) =>
                    onFormChange({
                      ...form,
                      discount: {
                        ...form.discount,
                        discount_mode: value as 'percent' | 'amount'
                      }
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="percent">{t('txExt.discount.mode.percent')}</SelectItem>
                    <SelectItem value="amount">{t('txExt.discount.mode.amount')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t('txExt.discount.value')}
                </Label>
                <Input
                  disabled={disabled}
                  placeholder={
                    form.discount.discount_mode === 'percent'
                      ? t('txExt.discount.valuePlaceholderPercent')
                      : t('txExt.discount.valuePlaceholderAmount')
                  }
                  value={form.discount.discount_value}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      discount: { ...form.discount, discount_value: e.target.value }
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t('txExt.discount.coupon')}
                </Label>
                <Input
                  disabled={disabled}
                  placeholder={t('txExt.discount.couponPlaceholder')}
                  value={form.discount.discount_coupon_name}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      discount: { ...form.discount, discount_coupon_name: e.target.value }
                    })
                  }
                />
              </div>
              <p className="text-xs text-muted-foreground md:col-span-3">
                {discountPreview
                  ? t('txExt.discount.preview')
                      .replace('{original}', String(discountPreview.originalAmount))
                      .replace('{saved}', String(discountPreview.discountAmount))
                      .replace('{final}', String(discountPreview.finalAmount))
                  : t('txExt.discount.previewInvalid')}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* ---------------- 多账户合并付款(expense) ---------------- */}
      {sections.legs ? (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 md:col-span-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{t('txExt.legs.title')}</p>
            <Switch
              checked={form.payment_merge_enabled}
              onChange={(next) =>
                onFormChange({
                  ...form,
                  payment_merge_enabled: next,
                  // 开启合并付款时,预置一条占位腿(留空账户由用户选)。
                  payment_legs: next && form.payment_legs.length === 0
                    ? [{ account_name: '', amount: '' }]
                    : form.payment_legs
                })
              }
              label={t('txExt.legs.enable') as string}
            />
          </div>
          {form.payment_merge_enabled ? (
            <div className="space-y-2">
              {form.payment_legs.map((leg, index) => (
                <div key={index} className="flex items-end gap-2">
                  <div className="flex-1 space-y-1">
                    {index === 0 ? (
                      <Label className="text-xs text-muted-foreground">
                        {t('txExt.legs.account')}
                      </Label>
                    ) : null}
                    <Select
                      value={leg.account_name || undefined}
                      disabled={disabled}
                      onValueChange={(value) =>
                        onFormChange({
                          ...form,
                          payment_legs: form.payment_legs.map((row, i) =>
                            i === index ? { ...row, account_name: value } : row
                          )
                        })
                      }
                    >
                      <SelectTrigger>
                        <SelectValue placeholder={t('txExt.legs.accountPlaceholder')} />
                      </SelectTrigger>
                      <SelectContent>
                        {legAccountOptions(leg.account_name).map((name) => (
                          <SelectItem key={name} value={name}>
                            {name}
                            {hiddenAccountNames.has(name) ? (
                              <span className="ml-1 text-xs text-muted-foreground">
                                {t('accounts.hidden.optionSuffix')}
                              </span>
                            ) : null}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="w-28 space-y-1">
                    {index === 0 ? (
                      <Label className="text-xs text-muted-foreground">
                        {t('txExt.legs.amount')}
                      </Label>
                    ) : null}
                    <Input
                      disabled={disabled}
                      placeholder="0.00"
                      value={leg.amount}
                      onChange={(e) =>
                        onFormChange({
                          ...form,
                          payment_legs: form.payment_legs.map((row, i) =>
                            i === index ? { ...row, amount: e.target.value } : row
                          )
                        })
                      }
                    />
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={disabled || form.payment_legs.length <= 1}
                    onClick={() =>
                      onFormChange({
                        ...form,
                        payment_legs: form.payment_legs.filter((_row, i) => i !== index)
                      })
                    }
                  >
                    {t('txExt.legs.remove')}
                  </Button>
                </div>
              ))}
              <div className="flex items-center justify-between">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={disabled}
                  onClick={() =>
                    onFormChange({
                      ...form,
                      payment_legs: [
                        ...form.payment_legs,
                        { account_name: '', amount: autoFillLegAmount(legsSummary.remaining) }
                      ]
                    })
                  }
                >
                  {t('txExt.legs.add')}
                </Button>
                <p
                  className={`text-xs ${
                    Math.abs(legsSummary.remaining) <= 0.011
                      ? 'text-muted-foreground'
                      : 'text-destructive'
                  }`}
                >
                  {t('txExt.legs.summary')
                    .replace('{allocated}', String(round2(legsSummary.total)))
                    .replace('{amount}', String(round2(amountNum)))
                    .replace('{remaining}', String(round2(legsSummary.remaining)))}
                </p>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* ---------------- 归物(0029):关联物品 ---------------- */}
      {/* 支出 / 收入都可挂到某件物品:支出金额计入物品累计成本,
          收入(回收款 / 折价补偿 / 保修理赔等)抵减净成本。 */}
      {sections.linkedItem ? (
        <div className="space-y-1 rounded-lg border border-border/60 bg-muted/20 p-3 md:col-span-2">
          <Label className="text-xs text-muted-foreground">
            {t('items.field.linkedItem')}
          </Label>
          <Select
            value={form.item_id || '__none__'}
            disabled={disabled}
            onValueChange={(value) =>
              onFormChange({
                ...form,
                item_id: value === '__none__' ? '' : value
              })
            }
          >
            <SelectTrigger>
              <SelectValue placeholder={t('items.field.linkedItemPlaceholder')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">{t('items.field.linkedItemPlaceholder')}</SelectItem>
              {itemOptions.map((it) => (
                <SelectItem key={it.id} value={it.id}>
                  {it.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">{t('items.field.linkedItemHint')}</p>
        </div>
      ) : null}

      {/* ---------------- 定期存款利息(0030):关联定期存款 ---------------- */}
      {/* 仅收入(利息)可挂到某笔定期存款,挂上后该笔金额计入该存款「已收利息」。 */}
      {sections.linkedDeposit ? (
        <div className="space-y-1 rounded-lg border border-border/60 bg-muted/20 p-3 md:col-span-2">
          <Label className="text-xs text-muted-foreground">
            {t('timeDeposits.field.linkedDeposit')}
          </Label>
          <Select
            value={form.time_deposit_id || '__none__'}
            disabled={disabled}
            onValueChange={(value) =>
              onFormChange({
                ...form,
                time_deposit_id: value === '__none__' ? '' : value
              })
            }
          >
            <SelectTrigger>
              <SelectValue placeholder={t('timeDeposits.field.linkedDepositPlaceholder')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">
                {t('timeDeposits.field.linkedDepositPlaceholder')}
              </SelectItem>
              {depositOptions.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {t('timeDeposits.field.linkedDepositHint')}
          </p>
        </div>
      ) : null}

      {sections.members ? (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 md:col-span-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{t('txExt.members.title')}</p>
            <Switch
              checked={form.member_split_enabled}
              onChange={(next) =>
                onFormChange({
                  ...form,
                  member_split_enabled: next,
                  // 开启时预置一行(成员留空由用户选);也可直接点「均分」。
                  member_shares:
                    next && form.member_shares.length === 0
                      ? [{ member_id: '', member_name: '', amount: '' }]
                      : form.member_shares
                })
              }
              label={t('txExt.members.enable') as string}
            />
          </div>
          {form.member_split_enabled ? (
            <div className="space-y-2">
              {form.member_shares.map((share, index) => {
                const shareAmount = toNum(share.amount)
                const pct =
                  amountNum > 0 ? Math.round((shareAmount / amountNum) * 1000) / 10 : 0
                return (
                  <div key={index} className="flex items-end gap-2">
                    <div className="flex-1 space-y-1">
                      {index === 0 ? (
                        <Label className="text-xs text-muted-foreground">
                          {t('txExt.members.member')}
                        </Label>
                      ) : null}
                      <Select
                        value={share.member_id || undefined}
                        disabled={disabled}
                        onValueChange={(value) =>
                          onFormChange({
                            ...form,
                            member_shares: form.member_shares.map((row, i) =>
                              i === index
                                ? {
                                    ...row,
                                    member_id: value,
                                    member_name: nameByMemberId.get(value) || ''
                                  }
                                : row
                            )
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue placeholder={t('txExt.members.memberPlaceholder')} />
                        </SelectTrigger>
                        <SelectContent>
                          {shareMemberOptions(share.member_id).map((m) => (
                            <SelectItem key={m.id} value={m.id}>
                              {m.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="w-24 space-y-1">
                      {index === 0 ? (
                        <Label className="text-xs text-muted-foreground">
                          {t('txExt.members.amount')}
                        </Label>
                      ) : null}
                      <Input
                        disabled={disabled}
                        placeholder="0.00"
                        value={share.amount}
                        onChange={(e) =>
                          onFormChange({
                            ...form,
                            member_shares: form.member_shares.map((row, i) =>
                              i === index ? { ...row, amount: e.target.value } : row
                            )
                          })
                        }
                      />
                    </div>
                    {/* 分摊比例(金额/账单),只读展示 —— 金额才是权威 */}
                    <div className="w-16 pb-2 text-right text-xs tabular-nums text-muted-foreground">
                      {pct ? `${pct}%` : '—'}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={disabled || form.member_shares.length <= 1}
                      onClick={() =>
                        onFormChange({
                          ...form,
                          member_shares: form.member_shares.filter((_row, i) => i !== index)
                        })
                      }
                    >
                      {t('txExt.members.remove')}
                    </Button>
                  </div>
                )
              })}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={disabled}
                    onClick={() =>
                      onFormChange({
                        ...form,
                        member_shares: [
                          ...form.member_shares,
                          { member_id: '', member_name: '', amount: '' }
                        ]
                      })
                    }
                  >
                    {t('txExt.members.add')}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={disabled || amountNum <= 0}
                    onClick={splitEvenly}
                  >
                    {t('txExt.members.evenSplit')}
                  </Button>
                </div>
                <p
                  className={`text-xs ${
                    Math.abs(sharesSummary.remaining) <= 0.011
                      ? 'text-muted-foreground'
                      : 'text-destructive'
                  }`}
                >
                  {t('txExt.members.summary')
                    .replace('{allocated}', String(round2(sharesSummary.total)))
                    .replace('{amount}', String(round2(amountNum)))
                    .replace('{remaining}', String(sharesSummary.remaining))}
                </p>
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">{t('txExt.members.hint')}</p>
          )}
        </div>
      ) : null}

      {/* ---------------- 转账手续费(transfer) ---------------- */}
      {sections.fee ? (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 md:col-span-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{t('txExt.fee.title')}</p>
            <Switch
              checked={form.transfer_fee.transfer_fee_enabled}
              onChange={(next) =>
                onFormChange({
                  ...form,
                  transfer_fee: { ...form.transfer_fee, transfer_fee_enabled: next }
                })
              }
              label={t('txExt.fee.enable') as string}
            />
          </div>
          {form.transfer_fee.transfer_fee_enabled ? (
            <div className="grid gap-2 md:grid-cols-3">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t('txExt.fee.amount')}
                </Label>
                <Input
                  disabled={disabled}
                  placeholder="0.00"
                  value={form.transfer_fee.transfer_fee_amount}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      transfer_fee: { ...form.transfer_fee, transfer_fee_amount: e.target.value }
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {t('txExt.fee.mode')}
                </Label>
                <Select
                  value={form.transfer_fee.transfer_fee_mode}
                  onValueChange={(value) =>
                    onFormChange({
                      ...form,
                      transfer_fee: {
                        ...form.transfer_fee,
                        transfer_fee_mode: value as typeof form.transfer_fee.transfer_fee_mode
                      }
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="deduct_from_source">
                      {t('txExt.fee.mode.deductFromSource')}
                    </SelectItem>
                    <SelectItem value="deduct_from_target">
                      {t('txExt.fee.mode.deductFromTarget')}
                    </SelectItem>
                    <SelectItem value="separate_account">
                      {t('txExt.fee.mode.separateAccount')}
                    </SelectItem>
                    <SelectItem value="discount">
                      {t('txExt.fee.mode.discount')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.transfer_fee.transfer_fee_mode === 'separate_account' ? (
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t('txExt.fee.account')}
                  </Label>
                  <Select
                    value={form.transfer_fee.transfer_fee_account_name || undefined}
                    disabled={disabled}
                    onValueChange={(value) =>
                      onFormChange({
                        ...form,
                        transfer_fee: { ...form.transfer_fee, transfer_fee_account_name: value }
                      })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('txExt.fee.accountPlaceholder')} />
                    </SelectTrigger>
                    <SelectContent>
                      {accountOptions.map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                          {hiddenAccountNames.has(name) ? (
                            <span className="ml-1 text-xs text-muted-foreground">
                              {t('accounts.hidden.optionSuffix')}
                            </span>
                          ) : null}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
              <p className="text-xs text-muted-foreground md:col-span-3">
                {form.transfer_fee.transfer_fee_mode === 'deduct_from_target'
                  ? t('txExt.fee.arrivalPreview')
                      .replace(
                        '{arrival}',
                        String(round2(amountNum - toNum(form.transfer_fee.transfer_fee_amount)))
                      )
                  : form.transfer_fee.transfer_fee_mode === 'discount'
                    ? t('txExt.fee.discountPreview')
                        .replace(
                          '{deduction}',
                          String(
                            round2(
                              amountNum - toNum(form.transfer_fee.transfer_fee_amount)
                            )
                          )
                        )
                        .replace('{amount}', String(round2(amountNum)))
                    : t('txExt.fee.hint')}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  )
}
