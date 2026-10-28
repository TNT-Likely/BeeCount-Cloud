import type { AttachmentRef } from '@beecount/api-client'
import type { DiscountForm, PaymentLegForm, TransferFeeForm } from './lib/txExtensions'

export type TxForm = {
  editingId: string | null
  editingOwnerUserId: string
  tx_type: 'expense' | 'income' | 'transfer'
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
  /** 优惠/折扣(0021):仅 expense。提交时由 discountToWire 转 camelCase。 */
  discount: DiscountForm
  /** 转账手续费(0021):仅 transfer。提交时由 transferFeeToWire 转 camelCase。 */
  transfer_fee: TransferFeeForm
  /** 多账户合并付款(0021):仅 expense。merge_enabled 时提交 legs,
   *  否则单账户(legs 忽略,保持旧行为)。 */
  payment_merge_enabled: boolean
  payment_legs: PaymentLegForm[]
  /** 成员分摊(0024):仅 expense。split_enabled 时提交 shares,
   *  否则不按人分摊(统计走记账人口径)。 */
  member_split_enabled: boolean
  member_shares: MemberShareForm[]
  /** 归物(0029):关联物品 sync_id;'' = 不关联。仅 expense 有意义。 */
  item_id: string
  /** 定期存款利息(0030):关联定期存款 sync_id;'' = 不关联。仅 income 有意义
   *  (利息按键生成的一笔收入挂到这里,金额计入该存款「已收利息」)。 */
  time_deposit_id: string
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
  /** 完整卡号(0026)。录入后服务端派生 card_last_four;详情页展示脱敏。 */
  card_number: string
  /** 账户图标 key(0026):内置银行/支付图标注册表主键。空 = 按类型回退默认。 */
  icon: string
  /** 当前 icon 是否开户行自动匹配得来(区别于手动指定)。改开户行时只覆盖
   *  自动匹配的图标,不动用户手动挑的。 */
  icon_auto: boolean
  /** 账户别名(0027):展示用短名,所有类型可填。空 = 显示账户名。 */
  alias: string
  /** 出账日(账单日)当天计入当期账单(0027,信用卡)。默认 true。 */
  billing_day_in_current: boolean
  /** 账户隐藏(issue #240)。只在编辑已有账户时通过「隐藏/恢复」切换修改;
   *  新建默认 false。 */
  hidden: boolean
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

/** 定期存款表单(0021)。金额/利率/期限都是字符串绑 input,提交时转 number。 */
/** 到息计划单条(0028)。日期 / 金额都是输入态字符串。 */
export type TimeDepositPayoutPlanForm = {
  /** 到息日 'YYYY-MM-DD'。 */
  date: string
  amount: string
  note: string
}

export type TimeDepositForm = {
  editingId: string | null
  /** 本金,>0。 */
  principal: string
  /** 币种代码,如 CNY / USD。 */
  currency: string
  /** 年利率,输入按「3.25 = 3.25%」口径,提交 /100。 */
  annual_rate: string
  /** 期限(月),1-600。 */
  term_months: string
  /** 起息日 'YYYY-MM-DDTHH:mm'(datetime-local 口径,可精确到分钟)。 */
  start_date: string
  /** 到期日 'YYYY-MM-DDTHH:mm',留空 = 服务端按 start+term 推算。 */
  maturity_date: string
  status: 'holding' | 'matured' | 'withdrawn'
  /** 关联账户 syncId(空 = 不关联)。 */
  account_id: string
  /** 关联账户显示名(仅 UI)。 */
  account_name: string
  auto_renew: boolean
  interest_mode: 'simple' | 'compound'
  note: string
  /** 支取日期 'YYYY-MM-DD'(status=withdrawn 时必填)。 */
  withdrawn_at: string
  /** 实得利息(withdrawn 时填,服务端 expected 会回退成它)。 */
  actual_interest: string
  /** 到息计划(0028,可多笔):提交时只保留 date + 金额都有效的行。 */
  payout_plans: TimeDepositPayoutPlanForm[]
}

/** 当前本地时间 → 'YYYY-MM-DDTHH:mm'(<input type="datetime-local"> 是本地口径)。 */
function nowLocalInputValue(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export const timeDepositDefaults = (): TimeDepositForm => ({
  editingId: null,
  principal: '',
  currency: 'CNY',
  annual_rate: '',
  term_months: '12',
  start_date: nowLocalInputValue(),
  maturity_date: '',
  status: 'holding',
  account_id: '',
  account_name: '',
  auto_renew: false,
  interest_mode: 'simple',
  note: '',
  withdrawn_at: '',
  actual_interest: '',
  payout_plans: []
})

/** 归物表单(0021,App issue #465 的「退款」诉求)。 */
export type ItemReturnForm = {
  editingId: string | null
  /** 物品名称,必填。 */
  name: string
  /** 关联原始购买交易 syncId(弱引用,可空)。 */
  original_tx_id: string
  /** 原购买金额(展示口径,可空)。 */
  original_amount: string
  /** 退款金额,>=0。 */
  refund_amount: string
  /** 归还/退款发生日 'YYYY-MM-DD'。 */
  happened_at: string
  reason: string
  status: 'pending' | 'returned' | 'refunded' | 'cancelled'
  account_id: string
  account_name: string
  category_id: string
  category_name: string
  note: string
}

export const itemReturnDefaults = (): ItemReturnForm => ({
  editingId: null,
  name: '',
  original_tx_id: '',
  original_amount: '',
  refund_amount: '',
  happened_at: new Date().toISOString().slice(0, 10),
  reason: '',
  status: 'pending',
  account_id: '',
  account_name: '',
  category_id: '',
  category_name: '',
  note: ''
})

/** 账本内自定义成员表单(0024)。 */
export type MemberForm = {
  editingId: string | null
  /** 成员名,必填。 */
  name: string
  note: string
  /** 列表排序权重,小的在前。 */
  sort_order: string
  /** 成员图标 key(人物语义 Material Symbols 名);空串 = 用默认图标。 */
  icon: string
  /** 关联的记账人(登录用户 id);空串 = 不关联。 */
  linked_user_id: string
}

export const memberDefaults = (): MemberForm => ({
  editingId: null,
  name: '',
  note: '',
  sort_order: '0',
  icon: '',
  linked_user_id: '',
})

/** 分别退款(0025)的一条退款腿:去向账户 + 字符串金额(input 绑定)。 */
export type RefundLegForm = {
  /** 该腿退款去向账户 syncId。 */
  account_id: string
  /** 该腿退款去向账户名(提交时一并带上)。 */
  account_name: string
  /** 输入框绑定的字符串金额。 */
  amount: string
}

/** 交易退款表单(0022,App issue #465 退款功能)。 */
export type RefundForm = {
  editingId: string | null
  /** 关联原始支出交易 syncId(必填)。 */
  original_tx_id: string
  /** 原单标题快照(选择原单后展示,不提交)。 */
  original_tx_label: string
  /** 原单金额快照(选择原单后展示,不提交;服务端以权威值覆盖)。 */
  original_amount: string
  /** 本笔退款金额,必填 > 0。 */
  amount: string
  /** 币种(缺省取原交易币种)。 */
  currency: string
  /** 退款去向账户(unified 模式)。 */
  account_id: string
  account_name: string
  /** 退款去向模式(0025):unified=统一退至一张卡 / split=分别退至多张卡。 */
  refund_mode: 'unified' | 'split'
  /** 分别退款(0025)的退款腿;unified 模式为空数组。 */
  refund_legs: RefundLegForm[]
  /** 退款日期 'YYYY-MM-DD'。 */
  happened_at: string
  reason: string
  status: 'pending' | 'received' | 'cancelled'
  note: string
}

export const refundDefaults = (): RefundForm => ({
  editingId: null,
  original_tx_id: '',
  original_tx_label: '',
  original_amount: '',
  amount: '',
  currency: '',
  account_id: '',
  account_name: '',
  refund_mode: 'unified',
  refund_legs: [],
  // 退款日期时间:默认当前时间(含时分秒),精确到分钟。
  happened_at: new Date().toISOString(),
  reason: '',
  status: 'pending',
  note: ''
})

/** 成员分摊的一份(0024)。金额字符串绑 input,提交转 number。 */
export type MemberShareForm = {
  /** 成员 syncId。 */
  member_id: string
  /** 成员名快照(提交时带上,成员被删后历史账单仍显示人名)。 */
  member_name: string
  /** 分摊金额,字符串绑 input。 */
  amount: string
}

/** 借款表单(0023)。 */
export type LoanForm = {
  editingId: string | null
  /** borrowed=借入 / lent=借出。 */
  direction: 'borrowed' | 'lent'
  /** 交易对手,必填。 */
  counterparty: string
  /** 本金(> 0)。 */
  principal: string
  /** 币种。 */
  currency: string
  /** 年利率小数(>= 0)。 */
  interest_rate: string
  /** 起借日 'YYYY-MM-DD'。 */
  started_at: string
  /** 到期日 'YYYY-MM-DD'(可空)。 */
  due_at: string
  /** 已还金额(>= 0,默认 0)。 */
  repaid_amount: string
  /** active / repaid / cancelled。 */
  status: 'active' | 'repaid' | 'cancelled'
  account_id: string
  account_name: string
  note: string
}

export const loanDefaults = (): LoanForm => ({
  editingId: null,
  direction: 'borrowed',
  counterparty: '',
  principal: '',
  currency: '',
  interest_rate: '0',
  started_at: new Date().toISOString().slice(0, 10),
  due_at: '',
  repaid_amount: '0',
  status: 'active',
  account_id: '',
  account_name: '',
  note: ''
})

/** 报销表单(0023)。 */
export type ReimbursementForm = {
  editingId: string | null
  /** 报销事由,必填。 */
  title: string
  /** 垫付方。 */
  payer: string
  /** 给付方。 */
  reimburser: string
  /** 金额(> 0)。 */
  amount: string
  currency: string
  category_id: string
  category_name: string
  /** 发生日 'YYYY-MM-DD'。 */
  happened_at: string
  /** pending / reimbursed / cancelled。 */
  status: 'pending' | 'reimbursed' | 'cancelled'
  account_id: string
  account_name: string
  /** 关联垫付交易 syncId(可空)。 */
  related_tx_id: string
  note: string
}

export const reimbursementDefaults = (): ReimbursementForm => ({
  editingId: null,
  title: '',
  payer: '',
  reimburser: '',
  amount: '',
  currency: '',
  category_id: '',
  category_name: '',
  happened_at: new Date().toISOString().slice(0, 10),
  status: 'pending',
  account_id: '',
  account_name: '',
  related_tx_id: '',
  note: ''
})

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
  discount: {
    discount_enabled: false,
    discount_mode: 'percent',
    discount_value: '',
    discount_coupon_name: ''
  },
  transfer_fee: {
    transfer_fee_enabled: false,
    transfer_fee_amount: '',
    transfer_fee_mode: 'deduct_from_source',
    transfer_fee_account_name: ''
  },
  payment_merge_enabled: false,
  payment_legs: [],
  member_split_enabled: false,
  member_shares: [],
  item_id: '',
  time_deposit_id: ''
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
  card_number: '',
  icon: '',
  icon_auto: false,
  alias: '',
  billing_day_in_current: true,
  hidden: false,
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
