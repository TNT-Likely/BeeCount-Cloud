import type { AttachmentRef } from '@beecount/api-client'

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
}

export type AccountForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  account_type: string
  currency: string
  initial_balance: string
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
  attachments: []
})

export const accountDefaults = (): AccountForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  account_type: 'cash',
  currency: 'CNY',
  initial_balance: '0',
  note: '',
  credit_limit: '',
  billing_day: '',
  payment_due_day: '',
  bank_name: '',
  card_last_four: '',
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
  parent_name: ''
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
