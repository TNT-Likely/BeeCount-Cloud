import type { RefundLeg, TxDiscount, TxPaymentLeg, TxTransferFee } from '@beecount/api-client'
import type { RefundLegForm } from '../forms'

/**
 * 0021 交易扩展字段(优惠/折扣、转账手续费、多账户合并付款)的纯逻辑核心。
 *
 * 铁律同 assetAggregation:UI 只负责渲染,所有"金额怎么算、form 怎么变 wire
 * 对象"的决策集中在这里,脱离 React 组件单测,锁住以下契约:
 *
 * - discount.finalAmount 恒等于实付 amount(服务端 upsert 不同步重算);
 * - percent 模式 value 是折扣率小数(0.85 = 85 折),originalAmount =
 *   amount / value;amount 模式 originalAmount = amount + value;
 * - sum(paymentLegs) 必须等于 amount(服务端容差 0.01,前端提前算给用户看);
 * - wire 对象键是 camelCase(跟 snapshot payload 惯例一致,mobile 友好)。
 */

export type DiscountForm = {
  discount_enabled: boolean
  discount_mode: 'percent' | 'amount'
  /** 输入框绑定:percent = 折扣率(如 '8.5' 表示 8.5 折);amount = 减免额。 */
  discount_value: string
  discount_coupon_name: string
}

export type TransferFeeForm = {
  transfer_fee_enabled: boolean
  transfer_fee_amount: string
  transfer_fee_mode: TxTransferFee['mode']
  /** separate_account 模式下的手续费扣款账户名(提交时转 id)。 */
  transfer_fee_account_name: string
}

export type PaymentLegForm = {
  account_name: string
  /** 输入框绑定的字符串金额。 */
  amount: string
}

/** 输入字符串 → number;空 / 非法 → 0。 */
export function toNum(raw: string | number | null | undefined): number {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : 0
  const text = (raw ?? '').toString().trim()
  if (!text) return 0
  const n = Number(text)
  return Number.isFinite(n) ? n : 0
}

/** 保留 2 位小数(金额展示 / legs 缩放),避免 0.1+0.2 类浮点噪音。 */
export function round2(n: number): number {
  return Math.round(n * 100) / 100
}

/**
 * 由 mode + value + 实付 amount 推导折扣三件套。
 *
 * - percent:value 是"折"数(8.5 折 = 0.85)→ original = amount / rate
 * - amount:value 是减免额 → original = amount + value
 * 输入不合法(空 / 非正)返回 null(UI 层据此禁用保存)。
 */
export function computeDiscount(
  mode: 'percent' | 'amount',
  value: string,
  amount: number,
): { originalAmount: number; discountAmount: number; finalAmount: number } | null {
  const v = toNum(value)
  if (amount <= 0) return null
  if (mode === 'percent') {
    // 折扣率 0 < rate < 1;0.85 = 85 折。value 输入按"8.5 折"口径 /10。
    const rate = v > 1 ? v / 10 : v
    if (!(rate > 0 && rate < 1)) return null
    const original = round2(amount / rate)
    return {
      originalAmount: original,
      discountAmount: round2(original - amount),
      finalAmount: round2(amount),
    }
  }
  if (!(v > 0)) return null
  return {
    originalAmount: round2(amount + v),
    discountAmount: round2(v),
    finalAmount: round2(amount),
  }
}

/** form 状态 → wire 对象(camelCase)。未启用 / 金额非法 → null(不发字段)。 */
export function discountToWire(
  form: DiscountForm,
  amount: number,
): TxDiscount | null {
  if (!form.discount_enabled) return null
  const computed = computeDiscount(form.discount_mode, form.discount_value, amount)
  if (!computed) return null
  const coupon = form.discount_coupon_name.trim()
  return {
    mode: form.discount_mode,
    value: toNum(form.discount_value),
    ...(coupon ? { couponName: coupon } : {}),
    originalAmount: computed.originalAmount,
    discountAmount: computed.discountAmount,
    finalAmount: computed.finalAmount,
  }
}

/** wire 对象 → form 状态(编辑带入)。null → 关闭态。 */
export function discountFromWire(d: TxDiscount | null | undefined): DiscountForm {
  if (!d) {
    return { discount_enabled: false, discount_mode: 'percent', discount_value: '', discount_coupon_name: '' }
  }
  const mode = d.mode === 'amount' ? 'amount' : 'percent'
  // percent wire value 是小数(0.85),输入框按"8.5 折"口径回显 → *10
  const value =
    mode === 'percent' && Number.isFinite(d.value) && d.value > 0 && d.value < 1
      ? String(round2(d.value * 10))
      : String(d.value ?? '')
  return {
    discount_enabled: true,
    discount_mode: mode,
    discount_value: value,
    discount_coupon_name: d.couponName ?? '',
  }
}

/** form 状态 → wire 对象。未启用 / 金额非正 → null。 */
export function transferFeeToWire(
  form: TransferFeeForm,
  accountIdByName: Map<string, string>,
): TxTransferFee | null {
  if (!form.transfer_fee_enabled) return null
  const amount = toNum(form.transfer_fee_amount)
  if (!(amount > 0)) return null
  const accountName = form.transfer_fee_account_name.trim()
  const feeAccountId = accountName ? accountIdByName.get(accountName.toLowerCase()) : undefined
  return {
    amount: round2(amount),
    mode: form.transfer_fee_mode,
    ...(form.transfer_fee_mode === 'separate_account'
      ? {
          ...(feeAccountId ? { feeAccountId } : {}),
          ...(accountName ? { feeAccountName: accountName } : {}),
        }
      : {}),
  }
}

/** wire 对象 → form 状态(编辑带入)。 */
export function transferFeeFromWire(f: TxTransferFee | null | undefined): TransferFeeForm {
  if (!f) {
    return {
      transfer_fee_enabled: false,
      transfer_fee_amount: '',
      transfer_fee_mode: 'deduct_from_source',
      transfer_fee_account_name: '',
    }
  }
  return {
    transfer_fee_enabled: true,
    transfer_fee_amount: String(f.amount ?? ''),
    transfer_fee_mode: f.mode ?? 'deduct_from_source',
    transfer_fee_account_name: f.feeAccountName ?? '',
  }
}

/** legs 合计(容差外的调用方自己判断)。 */
export function legsTotal(legs: PaymentLegForm[]): number {
  return round2(legs.reduce((sum, leg) => sum + toNum(leg.amount), 0))
}

/** 剩余待分配金额 = amount - 已分配。可为负(超分)。 */
export function legsRemaining(legs: PaymentLegForm[], amount: number): number {
  return round2(amount - legsTotal(legs))
}

/**
 * legs form → wire 对象。账户名重复 / 非正金额 / 合计不符(容差 0.01)返回 null。
 * accountIdByName 由调用方按 (name → syncId) 构造,找不到 id 的账户名整条 legs
 * 判失败(服务端也会拒,前端提前拦)。
 */
export function legsToWire(
  legs: PaymentLegForm[],
  amount: number,
  accountIdByName: Map<string, string>,
): TxPaymentLeg[] | null {
  if (legs.length === 0) return null
  const seen = new Set<string>()
  const out: TxPaymentLeg[] = []
  for (const leg of legs) {
    const name = leg.account_name.trim()
    if (!name) return null
    const key = name.toLowerCase()
    if (seen.has(key)) return null
    seen.add(key)
    const accountId = accountIdByName.get(key)
    if (!accountId) return null
    const legAmount = toNum(leg.amount)
    if (!(legAmount > 0)) return null
    out.push({ accountId, accountName: name, amount: round2(legAmount) })
  }
  if (Math.abs(legsTotal(legs) - amount) > 0.011) return null
  return out
}

/** wire 对象 → legs form(编辑带入)。 */
export function legsFromWire(legs: TxPaymentLeg[] | null | undefined): PaymentLegForm[] {
  if (!Array.isArray(legs) || legs.length === 0) return []
  return legs.map((leg) => ({
    account_name: leg.accountName ?? '',
    amount: String(leg.amount ?? ''),
  }))
}

/**
 * 合并付款模式:新增一条腿时自动把"剩余金额"填进去(常用交互:选第一个账户
 * 填一部分,第二个账户自动补足)。remaining <= 0 时填 0。
 */
export function autoFillLegAmount(remaining: number): string {
  return remaining > 0 ? String(round2(remaining)) : '0'
}

/**
 * 到账金额预览:deduct_from_target 时到账 = amount - fee;其余模式
 * (deduct_from_source / separate_account / discount)到账恒等于 amount。
 */
export function transferArrivalPreview(amount: number, fee: TxTransferFee | null): number {
  if (!fee || fee.mode !== 'deduct_from_target') return amount
  return round2(amount - fee.amount)
}

/**
 * 转出方实扣金额预览(0026):
 * - deduct_from_source: 实扣 = amount + fee
 * - discount: 实扣 = amount - fee(优惠由转出方享受,到账方全收)
 * - 其余模式: 实扣 = amount
 */
export function transferSourceDeductionPreview(amount: number, fee: TxTransferFee | null): number {
  if (!fee) return amount
  if (fee.mode === 'deduct_from_source') return round2(amount + fee.amount)
  if (fee.mode === 'discount') return round2(amount - fee.amount)
  return amount
}

// --- 0025 退款分摊(分别退款)form ↔ wire ------------------------------------ #

/** 退款腿合计(容差外的调用方自己判断)。 */
export function refundLegsTotal(legs: RefundLegForm[]): number {
  return round2(legs.reduce((sum, leg) => sum + toNum(leg.amount), 0))
}

/**
 * 由合并付款腿(payment_legs)推导分别退款腿,并按目标退款金额等比缩放。
 *
 * 例如原单微信付 600、支付宝付 400(合计 1000),退 300 时得 微信 180、支付宝 120。
 * 末腿吸收四舍五入误差,保证 sum(legs) === target(2 位精度)。
 * 原始腿无正金额 / 空数组 → 返回 []。
 */
export function refundLegsFromPaymentLegs(
  paymentLegs: readonly { accountId: string; accountName?: string | null; amount: number }[],
  targetAmount: number,
): RefundLegForm[] {
  if (!Array.isArray(paymentLegs) || paymentLegs.length === 0) return []
  const total = paymentLegs.reduce((s, l) => s + (Number(l.amount) || 0), 0)
  if (!(total > 0)) return []
  const target = Number.isFinite(targetAmount) && targetAmount > 0 ? targetAmount : 0
  const out: RefundLegForm[] = []
  let acc = 0
  paymentLegs.forEach((l, i) => {
    const share =
      i === paymentLegs.length - 1
        ? round2(target - acc)
        : round2(((Number(l.amount) || 0) / total) * target)
    acc = round2(acc + share)
    out.push({
      account_id: l.accountId,
      account_name: l.accountName ?? '',
      amount: String(share),
    })
  })
  return out
}

/**
 * 退款腿 form → wire 对象。账户缺失 / 重复 / 非正金额 → 返回 null(交由
 * validateRefundInput 给出 'legsRequired' / 'legsMismatch' 类提示)。sum 与退款
 * 总额是否一致由校验函数判定,这里只管单腿合法性 + 去重。
 */
export function refundLegsToWire(
  legs: RefundLegForm[],
  accountIdByName?: Map<string, string>,
): RefundLeg[] | null {
  if (!Array.isArray(legs) || legs.length === 0) return null
  const seen = new Set<string>()
  const out: RefundLeg[] = []
  for (const leg of legs) {
    const name = leg.account_name.trim()
    const id =
      leg.account_id || (name ? accountIdByName?.get(name.toLowerCase()) : undefined) || ''
    if (!id) return null
    if (seen.has(id)) return null
    seen.add(id)
    const amt = toNum(leg.amount)
    if (!(amt > 0)) return null
    out.push({ accountId: id, accountName: name, amount: round2(amt) })
  }
  return out
}

/** wire 对象 → 退款腿 form(编辑带入)。 */
export function refundLegsFromWire(legs: RefundLeg[] | null | undefined): RefundLegForm[] {
  if (!Array.isArray(legs) || legs.length === 0) return []
  return legs.map((leg) => ({
    account_id: leg.accountId || '',
    account_name: leg.accountName ?? '',
    amount: String(leg.amount ?? ''),
  }))
}

// --- 交易扩展区块可见性(0030 修复) ---------------------------------------- #

/**
 * 交易类型常量。跟后端 `WriteTransactionCreateRequest.tx_type` 的
 * Literal['expense','income','transfer'] 对齐;UI 判定一律走这里,不再散落裸字符串。
 */
export const TX_TYPE = {
  EXPENSE: 'expense',
  INCOME: 'income',
  TRANSFER: 'transfer',
} as const

export type TxType = (typeof TX_TYPE)[keyof typeof TX_TYPE]

/** TxExtensionEditor 里各区块是否渲染。 */
export type TxExtensionSections = {
  /** 0021 优惠/折扣 —— 仅 expense */
  discount: boolean
  /** 0021 多账户合并付款 —— 仅 expense */
  legs: boolean
  /** 0024 成员分摊 —— expense / income(且账本内有成员) */
  members: boolean
  /** 0021 转账手续费 —— 仅 transfer */
  fee: boolean
  /** 0029 归物:关联物品 —— expense / income(且账本内有物品) */
  linkedItem: boolean
  /** 0030 定期存款利息:关联定期存款 —— 仅 income(且账本内有存款) */
  linkedDeposit: boolean
}

export type TxExtensionSectionOptions = {
  /** 账本内是否有可选成员。 */
  hasMembers?: boolean
  /** 账本内是否有可选物品。 */
  hasItems?: boolean
  /** 账本内是否有可选定期存款。 */
  hasDeposits?: boolean
}

/**
 * 按交易类型 + 候选数据裁决 TxExtensionEditor 各区块可见性。
 *
 * 为什么要抽成纯函数(0030 修复的教训):编辑器里原来有一句
 * `if (!isExpense && !isTransfer) return null` 的早退,导致后来为收入写的
 * 「归物 / 关联定期存款」两个区块**永远不可达**(死代码)—— 在交易流水里记
 * 一笔利息收入时压根看不到「关联定期存款」下拉。可见性集中到这里裁决后,
 * 由单测锁住每种类型的契约,新增区块不会再次被早退吞掉。
 */
export function resolveTxExtensionSections(
  txType: string | null | undefined,
  options: TxExtensionSectionOptions = {},
): TxExtensionSections {
  const hasMembers = options.hasMembers ?? false
  const hasItems = options.hasItems ?? false
  const hasDeposits = options.hasDeposits ?? false
  const none: TxExtensionSections = {
    discount: false,
    legs: false,
    members: false,
    fee: false,
    linkedItem: false,
    linkedDeposit: false,
  }
  switch (txType) {
    case TX_TYPE.EXPENSE:
      return { ...none, discount: true, legs: true, members: hasMembers, linkedItem: hasItems }
    case TX_TYPE.INCOME:
      return { ...none, members: hasMembers, linkedItem: hasItems, linkedDeposit: hasDeposits }
    case TX_TYPE.TRANSFER:
      return { ...none, fee: true }
    default:
      return none
  }
}
