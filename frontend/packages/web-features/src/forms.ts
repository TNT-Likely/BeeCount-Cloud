import { emptyPartyProfile, type AttachmentRef, type PartyProfile } from '@beecount/api-client'

export type PaymentSplitForm = {
  account_id: string
  account_name: string
  amount: string
}

export type TxForm = {
  editingId: string | null
  editingOwnerUserId: string
  tx_type: 'expense' | 'income' | 'transfer' | 'refund'
  amount: string
  happened_at: string
  note: string
  category_name: string
  category_kind: 'expense' | 'income' | 'transfer'
  account_name: string
  from_account_name: string
  to_account_name: string
  tags: string[]
  attachments: AttachmentRef[]
  /** 交易币种(v30 多币种):'' = 跟随账户/账本本位币;显式值 = 用户手选。
   *  选了币种后账户下拉按该币种过滤(币种优先联动)。 */
  currency: string
  /** 编辑模式:该笔原币种(''=本位币)。提交时币种未变 → 不发字段,金额
   *  变化由 server 按隐含汇率联动(防快照漂移);仅前端用,不进 payload。 */
  original_currency: string
  /** 不计入收支统计(income/expense 显示开关,transfer 隐藏)。 */
  exclude_from_stats: boolean
  /** 不计入预算用量(仅 expense 显示开关)。 */
  exclude_from_budget: boolean
  /** 报销(0035):标记该账单为需报销(仅 expense)。会自动进入报销页"待报销"。 */
  is_reimbursement: boolean
  /** 报销公司维度(0037):勾选报销时必填公司档案 id/名称快照。 */
  reimbursement_company_id: string
  reimbursement_company_name: string
  /** 退款(0025):关联的原单 sync_id;仅 tx_type==='refund' 时有意义。
   *  编辑退款时随 payload 回传,后端据此保留「冲减原分类」语义。 */
  refund_of_sync_id?: string | null
  /** 转账手续费/优惠(0022,仅 transfer 有效)。表单中用字符串便于输入。 */
  transfer_fee_amount: string
  transfer_discount_amount: string
  /** 手续费从哪个账户扣除(可选,为空则默认从转出账户扣)。 */
  transfer_fee_account_name: string
  /** 支出优惠金额(消费优惠)，仅 expense 有效。 */
  discount_amount: string
  /** 成员(0024):单成员关联时存 syncId。 */
  member_id: string
  /** 是否开启多人分摊。false = 上面 member_id 单成员(整笔归该成员)。 */
  split_enabled: boolean
  /** 多人分摊行。金额用字符串便于输入，提交时转 number。 */
  member_splits: MemberSplitForm[]
  /** 关联物品(0044):该交易挂到这件物品(成为它的一条支出/收入流水)。 */
  item_id: string
  /** 支出组合付款(0028):是否开启多账户支付(仅 expense)。 */
  payment_split_enabled: boolean
  /** 组合付款各渠道。金额用字符串便于输入，提交时转 number。 */
  payment_splits: PaymentSplitForm[]
}

export type MemberSplitForm = {
  member_id: string
  name: string
  amount: string
}

export type MemberForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  color: string
  /** 头像 / 多联系方式 / 多银行卡，与联系人共用结构。 */
  profile: PartyProfile
}

export type AccountForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  account_type: string
  currency: string
  initial_balance: string
  credit_balance_state: 'debt' | 'overpayment'
  /** 备注,所有类型可填。 */
  note: string
  /** 信用额度,仅 credit_card 类型有意义。空字符串 = 未填。 */
  credit_limit: string
  /** 账单日(1-31),仅 credit_card。空字符串 / NaN = 未填。 */
  billing_day: string
  /** 还款日(1-31),仅 credit_card。 */
  payment_due_day: string
  /** 开户行,bank_card / credit_card 元信息。 */
  bank_name: string
  /** 卡号后四位,bank_card / credit_card 元信息。 */
  card_last_four: string
  /** 账户隐藏(issue #240)。只在编辑已有账户时通过「隐藏/恢复」切换修改;
   *  新建默认 false。 */
  hidden: boolean
  /** 账户图标/背景(issue #icon)。icon_key 对应公共图标库键;icon_type 标识
   *  图标类型(bank|card|custom);background_key 对应背景键。空 = 未设置。 */
  icon_key: string
  icon_type: string
  background_key: string
}

export type CategoryForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  kind: 'expense' | 'income' | 'transfer'
  level: string
  sort_order: string
  icon: string
  icon_type: string
  custom_icon_path: string
  icon_cloud_file_id: string
  icon_cloud_sha256: string
  parent_name: string
  parent_sync_id: string
}

import { pickRandomTagColor } from './lib/tagColorPalette'

export type BudgetForm = {
  /** 编辑模式 = budget syncId,新建 = null。 */
  editingId: string | null
  /** 'total' / 'category' — 决定要不要展示 category 选择;新建后不可改类型,
   *  改类型走"删一条 + 新建一条"路径,跟 mobile 一致。 */
  type: 'total' | 'category'
  /** 选中的分类 syncId(仅 type='category' 有意义)。 */
  category_id: string
  /** 选中的分类显示名(给 UI 展示当前选中,不传给 server)。 */
  category_name: string
  /** 金额,字符串形式绑 input,提交转 number;<=0 校验失败。 */
  amount: string
  /** 起始日(1-28)。mobile 暂时隐藏 UI 默认 1,web 也跟着默认。 */
  start_day: string
  /** 周期,默认 monthly。 */
  period: 'monthly' | 'weekly' | 'yearly'
}

export type TagForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  color: string
}

export const txDefaults = (): TxForm => ({
  editingId: null,
  editingOwnerUserId: '',
  tx_type: 'expense',
  amount: '',
  happened_at: new Date().toISOString(),
  note: '',
  category_name: '',
  category_kind: 'expense',
  account_name: '',
  from_account_name: '',
  to_account_name: '',
  tags: [],
  attachments: [],
  currency: '',
  original_currency: '',
  exclude_from_stats: false,
  exclude_from_budget: false,
  is_reimbursement: false,
  reimbursement_company_id: '',
  reimbursement_company_name: '',
  transfer_fee_amount: '',
  transfer_discount_amount: '',
  transfer_fee_account_name: '',
  discount_amount: '',
  member_id: '',
  item_id: '',
  split_enabled: false,
  member_splits: [],
  payment_split_enabled: false,
  payment_splits: []
})

/** 把表单里的「单成员 / 多人分摊」统一转成写接口的 member_splits payload。
 *  单成员时金额恒等于交易金额；转账一律返回空数组（后端会清除）。 */
export const buildMemberSplits = (
  form: TxForm,
  memberNameById: Map<string, string> = new Map()
): Array<{ member_id: string; name: string; amount: number }> => {
  if (form.tx_type === 'transfer') return []
  if (form.split_enabled) {
    return form.member_splits
      .filter((row) => Boolean(row.member_id) && Number(row.amount) > 0)
      .map((row) => ({
        member_id: row.member_id,
        name: row.name || memberNameById.get(row.member_id) || row.member_id,
        amount: Math.round(Number(row.amount) * 100) / 100
      }))
  }
  if (!form.member_id) return []
  return [{
    member_id: form.member_id,
    name: memberNameById.get(form.member_id) || form.member_id,
    amount: Math.round((Number(form.amount) || 0) * 100) / 100
  }]
}

/** 分摊金额合计必须等于交易金额(容差 0.01)，与后端校验一致。 */
export const memberSplitsValid = (
  splits: Array<{ amount: number }>,
  amount: number
): boolean => {
  if (splits.length <= 1) return true
  const total = splits.reduce((sum, row) => sum + row.amount, 0)
  // 同后端：先按「分」取整再比较，避免 33.33*3=99.99 vs 100 的
  // 0.010000000000005 被误判成超出 0.01 容差。
  return Math.round(Math.abs(total - amount) * 100) / 100 <= 0.01
}

/** 支出组合付款(0028):表单转写接口 payload。仅 expense 且开启时有效。 */
export const buildPaymentSplits = (
  form: TxForm,
  accountNameById: Map<string, string> = new Map()
): Array<{ account_id: string; account_name: string; amount: number }> => {
  if (!form.payment_split_enabled || form.tx_type !== 'expense') return []
  return form.payment_splits
    .filter((row) => Boolean(row.account_id) && Number(row.amount) > 0)
    .map((row) => ({
      account_id: row.account_id,
      account_name: row.account_name || accountNameById.get(row.account_id) || row.account_id,
      amount: Math.round(Number(row.amount) * 100) / 100
    }))
}

/** 组合付款金额合计必须等于交易金额(容差 0.01)。少于 2 条视为未生效。 */
export const paymentSplitsValid = (
  splits: Array<{ amount: number }>,
  amount: number
): boolean => {
  if (splits.length < 2) return true
  const total = splits.reduce((sum, row) => sum + row.amount, 0)
  return Math.round(Math.abs(total - amount) * 100) / 100 <= 0.01
}

/** 组合付款自动补差:最后一项 = 总额 - 其它项之和(四舍五入到分,>=0)。 */
export const autoFillLastPaymentSplit = (
  splits: Array<{ account_id: string; account_name: string; amount: string }>,
  totalAmount: number
): Array<{ account_id: string; account_name: string; amount: string }> => {
  if (splits.length < 2) return splits
  const others = splits.slice(0, -1).reduce((sum, row) => sum + (Number(row.amount) || 0), 0)
  const last = Math.max(0, Math.round((totalAmount - others) * 100) / 100)
  return splits.map((row, i) => (i === splits.length - 1 ? { ...row, amount: last.toFixed(2) } : row))
}

export const memberDefaults = (): MemberForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  color: pickRandomTagColor(),
  profile: emptyPartyProfile(),
})

export const accountDefaults = (): AccountForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  account_type: 'cash',
  currency: 'CNY',
  initial_balance: '0',
  credit_balance_state: 'debt',
  note: '',
  credit_limit: '',
  billing_day: '',
  payment_due_day: '',
  bank_name: '',
  card_last_four: '',
  hidden: false,
  icon_key: '',
  icon_type: '',
  background_key: '',
})

export const categoryDefaults = (): CategoryForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  kind: 'expense',
  level: '1',
  sort_order: '1',
  icon: '',
  icon_type: 'material',
  custom_icon_path: '',
  icon_cloud_file_id: '',
  icon_cloud_sha256: '',
  parent_name: '',
  parent_sync_id: ''
})

export const tagDefaults = (): TagForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  // 跟 app 端 TagSeedService.getRandomColor() 一致:每次新建从 20 色调色板
  // 里随机选一个,避免所有用户都默认 #F59E0B 导致全员标签同色。
  color: pickRandomTagColor()
})

export const budgetDefaults = (): BudgetForm => ({
  editingId: null,
  type: 'total',
  category_id: '',
  category_name: '',
  amount: '',
  start_day: '1',
  period: 'monthly',
})
