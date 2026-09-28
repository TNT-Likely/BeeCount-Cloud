import type {
  AttachmentRef,
  InstallmentInterestPeriod,
  InstallmentPlanCreatePayload,
  InstallmentPlanStatus,
  InstallmentRemainderPosition,
  InstallmentRepaymentMethod,
  RecurringAdvancedRule,
  RecurringInlineCreatePayload,
  RecurringRuleKind,
  TxSplitPayload,
} from '@beecount/api-client'

export type TxForm = {
  editingId: string | null
  editingOwnerUserId: string
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  happened_at: string
  note: string
  /** 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。 */
  merchant: string
  category_name: string
  category_kind: 'expense' | 'income' | 'transfer'
  account_name: string
  from_account_name: string
  to_account_name: string
  /** 帳戶必選(需求 #9,Phase 11):編輯既有交易時,記錄打開表單當下的原始
   *  `account_name`(新建=''),用來判斷使用者是否「主動變更」過帳戶欄位。
   *  只有新建 或 使用者主動變更過帳戶時才強制必選,避免舊有 mobile 匯入的
   *  無帳戶交易被迫在 web 端補選帳戶才能存檔其它欄位的編輯(比照
   *  `original_currency` 同款「開表單時的快照」模式)。 */
  original_account_name: string
  tags: string[]
  attachments: AttachmentRef[]
  /** 交易币种(v30 多币种):'' = 跟随账户/账本本位币;显式值 = 用户手选。
   *  选了币种后账户下拉按该币种过滤(币种优先联动)。 */
  currency: string
  /** 编辑模式:该笔原币种(''=本位币)。提交时币种未变 → 不发字段,金额
   *  变化由 server 按隐含汇率联动(防快照漂移);仅前端用,不进 payload。 */
  original_currency: string
  /** 跨幣別自動換算(2026-08):使用者手動覆蓋的匯率,''=自動抓匯率。
   *  expense/income 時代表「入帳幣別 → 所選帳戶幣別」;transfer 時代表
   *  「轉出帳戶幣別 → 轉入帳戶幣別」——兩種 tx_type 互斥,共用同一欄位。
   *  只在幣別/帳戶欄位改變時清空(見 forms.ts 呼叫端),避免舊幣別對的
   *  手動匯率誤套到新的幣別對。純前端 UI 狀態,不進 payload。
   *  跟 {@link fx_amount_override} 互斥:使用者編輯其中一個時,呼叫端要把
   *  另一個清空(見 TransactionsPanel.tsx 的 onRateChange/onAmountChange)。 */
  fx_rate_override: string
  /** 跨幣別自動換算(2026-08-14 補充):使用者直接手動輸入的「換算後金額」,
   *  ''=未手動輸入。使用者需求:換算金額有誤時,應該以直接改這個「換算後
   *  金額」為主要修正手段(而非先反推該改多少匯率),所以這個欄位在
   *  {@link fx_rate_override} 都有值時優先生效(見
   *  currencies.ts::resolveEffectiveRate)——仍然可以改匯率來連動改變換算
   *  後金額,只是換算後金額本身也能直接編輯。純前端 UI 狀態,不進 payload。 */
  fx_amount_override: string
  /** 不计入收支统计(income/expense 显示开关,transfer 隐藏)。 */
  exclude_from_stats: boolean
  /** 不计入预算用量(仅 expense 显示开关)。 */
  exclude_from_budget: boolean
  /** 退款(§2.6):这笔交易是对哪笔支出的退款,存目标交易 syncId。''=普通交易。 */
  refund_of_id: string
  /** 借還款追蹤(§2.5 體驗補強):这笔交易关联的欠款 syncId。''=不掛欠款,
   *  '__new__' = 用这笔交易顺便建一笔新欠款(见下面 new_debt_* 两个字段)。
   *  跟 refund_of_id 同款语意,expense/income 都可选,transfer 不适用。 */
  debt_id: string
  /** 專案(Phase 13,docs/PH13_PROJECT_SD.md):这笔交易关联的專案 syncId。
   *  ''=不掛專案。只在 expense/income 顯示,跟 debt_id 同款语意
   *  (transfer 隱藏此欄位)。 */
  project_id: string
  /** 選中專案的顯示名(給 UI 展示當前選中,不傳給 server)。 */
  project_name: string
  /** debt_id === '__new__' 时用:新欠款的对方名字(必填)/到期日(可选,
   *  纯日期字符串 yyyy-mm-dd)。direction 不在表单里存 —— 由 tx_type 在
   *  提交时推算(income=payable 我欠对方,expense=receivable 对方欠我),
   *  这笔交易本身是欠款的起点、不是还款,所以不会把它的 debt_id 设成
   *  这笔新欠款(设了的话 remaining_amount 会被这笔交易的金额立刻冲抵掉,
   *  见 read/ledgers.py::list_debts 的 repaid 累加逻辑)。 */
  new_debt_counterparty_name: string
  new_debt_due_at: string
  /** 延後入帳(§2.10 Phase 5):這筆交易的實際入帳日跟 `happened_at`(消費日)
   *  不同時填,純日期字符串 yyyy-mm-dd,''=未設定(server 不收這個字段/清空)。
   *  進階/選填欄位,只給對帳/信用卡帳單週期歸屬用。 */
  deferred_posting_at: string
  /** Phase 1.5(§2.12.2):建交易当下顺便設為週期性收支起点。只在新建
   *  (editingId=null)时可开启,跟 installment_enabled 互斥。 */
  recurring_enabled: boolean
  recurring_frequency: 'daily' | 'weekly' | 'monthly' | 'yearly'
  recurring_interval: string
  /** 空字符串 = 不设结束日期(长期规则,只生成默认视窗)。 */
  recurring_end_at: string
  /** 简单 frequency+interval 表达不了时用进阶规则。'none' = 不用。 */
  recurring_advanced_mode: 'none' | 'weekly_days' | 'monthly_day'
  /** Python `datetime.weekday()` 惯例:Monday=0…Sunday=6。 */
  recurring_weekly_days: number[]
  recurring_monthly_day: string
  /** Phase 1.5(§2.12.1):建交易当下顺便設為分期付款起点。只在新建 expense
   *  时可开启,跟 recurring_enabled 互斥(挂在同一个 amount/happened_at 上,
   *  两者语意冲突,一次只能选一个)。 */
  installment_enabled: boolean
  installment_periods: string
  installment_repayment_method: 'equal_installment' | 'equal_principal' | 'fixed_interest'
  installment_interest_period: 'monthly' | 'daily'
  installment_interest_rate: string
  installment_round_amounts: boolean
  installment_remainder_position: 'first' | 'last'
  installment_grace_period_months: string
  /** 拆帳(§2.4):把这笔交易拆到多个分类。tx_type 只能 expense/income,跟
   *  transfer 互斥;开启时上面单选的 category_name 不使用,改看 splits。
   *  跟 recurring/installment 不同 —— 编辑既有交易时也能开关(对齐 Moze
   *  编辑既有交易能改分类的语意)。 */
  split_enabled: boolean
  splits: TxSplitFormItem[]
  /** 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易走
   *  哪幾條回饋規則(可複選),只在 tx_type='expense' 且選了信用卡帳戶時
   *  顯示。存 `read_card_reward_rule_projection.sync_id` 列表。 */
  reward_rule_ids: string[]
  /** 手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction):
   *  expense/income 用一個共用面板——`amount` 欄位在這個功能開啟時代表
   *  使用者輸入的原始金額(base_amount,信用卡回饋計算的權威基準),
   *  `fee_enabled` 控制「+」展開的手續費/折扣兩列 UI 是否顯示。
   *  fee_label/discount_label 空字串 = 用預設「手續費」「折扣」顯示,不送
   *  給 server(留給 server 端預設)。
   *
   *  transfer(2026-08-29 轉帳手續費/折損)用獨立的兩個面板:`fee_enabled`
   *  控制轉出側手續費面板,`discount_enabled`(見下方)控制轉入側折損
   *  面板,兩者互不影響——跟 expense/income「`fee_enabled` 同時控制手續費
   *  +折扣兩個輸入」的既有行為不同,不要混用同一顆開關。切到 transfer 時
   *  `amount`/`fee_amount`/`discount_amount` 不重算(維持客戶端算好的值,
   *  跟 base_amount 無關,見 applyTxType()/TransactionsPage.tsx payload
   *  組裝邏輯的說明)。 */
  fee_enabled: boolean
  fee_amount: string
  fee_label: string
  discount_amount: string
  discount_label: string
  /** 轉帳手續費/折損(2026-08-29):只給 tx_type='transfer' 用,獨立控制
   *  轉入側折損面板的顯示/隱藏,不影響 expense/income 既有的 fee_enabled
   *  行為。 */
  discount_enabled: boolean
}

/** 手續費/折扣(2026-08 使用者需求)換算總額的公式,跟後端
 * `src/routers/write/_shared.py::_normalize_fee_discount_amount` 用同一條
 * 公式——server 端仍是最終權威來源,這裡只用來即時預覽跟離線送出時的
 * `amount` 欄位,兩邊改動要一起同步。
 *   expense: amount = base + fee - discount
 *   income:  amount = base - fee + discount
 */
export const computeTxTotalAmount = (
  txType: 'expense' | 'income' | 'transfer',
  base: number,
  fee: number,
  discount: number
): number => {
  if (txType === 'income') return base - fee + discount
  return base + fee - discount
}

/** 拆帳(§2.4):`TxForm.splits` 单行明细的表单态(金额存字符串绑 input)。 */
export type TxSplitFormItem = {
  category_id: string
  category_name: string
  amount: string
  note: string
}

export const txSplitItemDefaults = (): TxSplitFormItem => ({
  category_id: '',
  category_name: '',
  amount: '',
  note: '',
})

export type AccountForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  account_type: string
  currency: string
  initial_balance: string
  /** 备注,所有类型可填。 */
  note: string
  /** 信用额度(§2.9 Phase 4 2026-08-02 改版),仅 account_group 类型有意义,
   *  整组共用一个额度。空字符串 = 未填。 */
  credit_limit: string
  /** 账单日(1-31),仅 account_group。空字符串 / NaN = 未填。 */
  billing_day: string
  /** 还款日(1-31),仅 account_group。 */
  payment_due_day: string
  /** 开户行,bank_card / credit_card / account_group 元信息。 */
  bank_name: string
  /** 卡号后四位,bank_card / credit_card / account_group 元信息。 */
  card_last_four: string
  /** 掛靠的主帳戶(account_group)account id,仅 credit_card。
   *  空字符串 = 沒有掛靠。 */
  parent_account_id: string
  /** 账户隐藏(issue #240)。只在编辑已有账户时通过「隐藏/恢复」切换修改;
   *  新建默认 false。 */
  hidden: boolean
  /** 自動扣繳(§2.9,2026-08-04 改版):開關,只在 account_group,或沒有
   *  掛靠任何群組的獨立信用卡上生效。 */
  auto_pay_enabled: boolean
  /** 自動扣繳來源帳戶 id。空字符串 = 未設定。 */
  auto_pay_from_account_id: string
  /** 帳戶頭像(2026-08-02 補強):`AttachmentFile.id`,空字符串 = 沒有頭像。 */
  avatar_cloud_file_id: string
  avatar_cloud_sha256: string
  /** 納入總餘額(Phase 18):對齊 Moze,關閉後這個帳戶的餘額不列入淨資產/
   *  資產構成總額,但帳戶本身、個別餘額顯示、底部分組列表都不受影響。
   *  新建/未設置一律預設 true(納入)。 */
  include_in_total: boolean
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
  /** 十六进制色值,如 `#FF9800`;空字符串 = 未设色(旧数据 / 转账分类),渲
   *  染端 fallback 回灰色。只有一级分类(level=1)存值,二级分类固定传空
   *  字符串,渲染时向上查父分类颜色 —— 跟 app 端 `Categories.color` 的约定
   *  对齐(见 docs/changes/2026-09-05-category-colors-and-add-tile.md)。 */
  color: string
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

export type RecurringRuleForm = {
  /** 编辑模式 = rule syncId,新建 = null。 */
  editingId: string | null
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  note: string
  category_id: string
  category_name: string
  account_id: string
  account_name: string
  from_account_id: string
  from_account_name: string
  to_account_id: string
  to_account_name: string
  frequency: 'daily' | 'weekly' | 'monthly' | 'yearly'
  interval: string
  /** datetime-local 输入用的本地时间字符串。 */
  next_run_at: string
  /** 空字符串 = 不设结束日期。 */
  end_at: string
  enabled: boolean
  /** Phase 1.5(§2.12.2)进阶规则,只在新建时生效(编辑既有规则不改规则本身
   *  的生成逻辑,只能改基础字段/enabled)。 */
  advanced_mode: 'none' | 'weekly_days' | 'monthly_day'
  weekly_days: number[]
  monthly_day: string
  /** 手續費/折扣/信用卡回饋(2026-08 使用者回饋):規則固定屬性,同 TxForm
   *  同名欄位語意,每一期自動產生的 occurrence 都會繼承這裡設定的值。 */
  reward_rule_ids: string[]
  fee_enabled: boolean
  fee_amount: string
  fee_label: string
  discount_amount: string
  discount_label: string
  /** 股票定期定額(2026-09-28,docs/STOCK_HOLDINGS_SD.md §9):'stock_dca' 時
   *  tx_type 強制是 'transfer',from_account/to_account 分別是交割戶/投資
   *  理財帳戶,並新增 market/symbol/security_name 三個欄位(建立後鎖定)。
   *  stock_fee_override 開啟時才送 stock_fee_rate(百分比顯示)/stock_fee_min,
   *  關閉時送 null 清除（改回沿用帳戶預設）。 */
  kind: RecurringRuleKind
  market: string
  symbol: string
  security_name: string
  stock_fee_override: boolean
  stock_fee_rate: string
  stock_fee_min: string
}

export type InstallmentPlanForm = {
  /** 编辑模式 = plan syncId,新建 = null。新建后期数/总额/攤還参数不可改
   *  (server 约束,要调这些走差异化端点 rebalance-from 等)。 */
  editingId: string | null
  total_amount: string
  periods: string
  first_period_at: string
  account_id: string
  account_name: string
  category_id: string
  category_name: string
  note: string
  status: InstallmentPlanStatus
  /** Phase 1.5(§2.12.1)攤還参数,只在新建时生效。 */
  repayment_method: InstallmentRepaymentMethod
  interest_period: InstallmentInterestPeriod
  interest_rate: string
  round_amounts: boolean
  remainder_position: InstallmentRemainderPosition
  grace_period_months: string
}

export type DebtForm = {
  /** 编辑模式 = debt syncId,新建 = null。`direction`/`principal_amount`
   *  建立后不可改(server 约束)。 */
  editingId: string | null
  direction: 'payable' | 'receivable'
  counterparty_name: string
  principal_amount: string
  /** datetime-local 输入用的本地时间字符串,空字符串 = 不设到期日。 */
  due_at: string
  note: string
  /** 排除計入總額(§5.4 對象管理):只影響淨資產/總額統計。 */
  excluded_from_total: boolean
}

export type ProjectPeriodType = 'fixed' | 'monthly' | 'yearly'

export type DailyBudgetMode = 'fixed' | 'proportional'

export type ProjectForm = {
  /** 编辑模式 = project syncId,新建 = null。 */
  editingId: string | null
  name: string
  /** emoji 字串,選填。 */
  icon: string
  /** 金额字符串,空字符串 = 不設預算(純追蹤用途)。 */
  budget_amount: string
  period_type: ProjectPeriodType
  /** date input 用的 yyyy-mm-dd 字符串。`period_type==='fixed'` 時必填。 */
  period_start: string
  period_end: string
  carryover_enabled: boolean
  visible_on_home: boolean
  /** 軟刪除(有交易掛著時 DELETE 會把這個設成 false)後,編輯表單裡可以
   *  手動切回 true 重新啟用 —— 沒有獨立的「重新開啟」端點,靠 PATCH 這個
   *  欄位達成(跟 debt 的 close/reopen 不同,不需要專門的按鈕/端點)。 */
  enabled: boolean
  /** 收入併入預算。純追蹤專案(budget_amount 空)沒有預算基準可併入,UI 停用。 */
  income_included_in_budget: boolean
  daily_budget_enabled: boolean
  daily_budget_mode: DailyBudgetMode
  /** 空字串 = 不提醒;否則 1-200 的整數字符串。 */
  reminder_threshold_percent: string
}

export type TxTemplateForm = {
  /** 编辑模式 = template syncId,新建 = null。 */
  editingId: string | null
  name: string
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  note: string
  category_id: string
  category_name: string
  account_id: string
  account_name: string
  from_account_id: string
  from_account_name: string
  to_account_id: string
  to_account_name: string
  sort_order: string
}

export const txDefaults = (): TxForm => ({
  editingId: null,
  editingOwnerUserId: '',
  tx_type: 'expense',
  amount: '',
  happened_at: new Date().toISOString(),
  note: '',
  merchant: '',
  category_name: '',
  category_kind: 'expense',
  account_name: '',
  from_account_name: '',
  to_account_name: '',
  original_account_name: '',
  tags: [],
  attachments: [],
  currency: '',
  original_currency: '',
  fx_rate_override: '',
  fx_amount_override: '',
  exclude_from_stats: false,
  exclude_from_budget: false,
  refund_of_id: '',
  debt_id: '',
  project_id: '',
  project_name: '',
  new_debt_counterparty_name: '',
  new_debt_due_at: '',
  deferred_posting_at: '',
  recurring_enabled: false,
  recurring_frequency: 'monthly',
  recurring_interval: '1',
  recurring_end_at: '',
  recurring_advanced_mode: 'none',
  recurring_weekly_days: [],
  recurring_monthly_day: '1',
  installment_enabled: false,
  installment_periods: '',
  installment_repayment_method: 'equal_principal',
  installment_interest_period: 'monthly',
  installment_interest_rate: '',
  installment_round_amounts: true,
  installment_remainder_position: 'last',
  installment_grace_period_months: '0',
  split_enabled: false,
  splits: [],
  reward_rule_ids: [],
  fee_enabled: false,
  fee_amount: '',
  fee_label: '',
  discount_amount: '',
  discount_label: '',
  discount_enabled: false,
})

export const accountDefaults = (): AccountForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  account_type: 'cash',
  currency: 'TWD',
  initial_balance: '0',
  note: '',
  credit_limit: '',
  billing_day: '',
  payment_due_day: '',
  bank_name: '',
  card_last_four: '',
  parent_account_id: '',
  hidden: false,
  auto_pay_enabled: false,
  auto_pay_from_account_id: '',
  avatar_cloud_file_id: '',
  avatar_cloud_sha256: '',
  include_in_total: true,
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
  // 跟 tagDefaults 一样,新建默认从 20 色调色板随机选一个(对齐 app 端
  // local_category_repository.dart::_nextAutoColor 的"自动配色"行为),用户
  // 仍可在 dialog 里手动改选。
  color: pickRandomTagColor()
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

/** next_run_at 默认明天此刻,避免用户忘填被 server 拒绝(next_run_at 必填)。 */
function tomorrowLocalDatetime(): string {
  const d = new Date(Date.now() + 24 * 60 * 60 * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export const recurringRuleDefaults = (): RecurringRuleForm => ({
  editingId: null,
  tx_type: 'expense',
  amount: '',
  note: '',
  category_id: '',
  category_name: '',
  account_id: '',
  account_name: '',
  from_account_id: '',
  from_account_name: '',
  to_account_id: '',
  to_account_name: '',
  frequency: 'monthly',
  interval: '1',
  next_run_at: tomorrowLocalDatetime(),
  end_at: '',
  enabled: true,
  advanced_mode: 'none',
  weekly_days: [],
  monthly_day: '1',
  reward_rule_ids: [],
  fee_enabled: false,
  fee_amount: '',
  fee_label: '',
  discount_amount: '',
  discount_label: '',
  kind: 'general',
  market: '',
  symbol: '',
  security_name: '',
  stock_fee_override: false,
  stock_fee_rate: '',
  stock_fee_min: '',
})

export const installmentPlanDefaults = (): InstallmentPlanForm => ({
  editingId: null,
  total_amount: '',
  periods: '',
  first_period_at: (() => {
    const d = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  })(),
  account_id: '',
  account_name: '',
  category_id: '',
  category_name: '',
  note: '',
  status: 'active',
  repayment_method: 'equal_principal',
  interest_period: 'monthly',
  interest_rate: '0',
  round_amounts: true,
  remainder_position: 'last',
  grace_period_months: '0',
})

export const debtDefaults = (): DebtForm => ({
  editingId: null,
  direction: 'payable',
  counterparty_name: '',
  principal_amount: '',
  due_at: '',
  note: '',
  excluded_from_total: false,
})

export const projectDefaults = (): ProjectForm => ({
  editingId: null,
  name: '',
  icon: '',
  budget_amount: '',
  period_type: 'monthly',
  period_start: '',
  period_end: '',
  carryover_enabled: false,
  visible_on_home: true,
  enabled: true,
  income_included_in_budget: false,
  daily_budget_enabled: false,
  daily_budget_mode: 'proportional',
  reminder_threshold_percent: '',
})

export type ProjectCategoryBudgetForm = {
  /** 编辑模式 = allocation syncId,新建 = null。 */
  editingId: string | null
  category_id: string
  category_name: string
  mode: 'fixed' | 'percentage'
  /** 金额字符串,mode==='fixed' 時使用。 */
  fixed_amount: string
  /** 0-100 的百分比字符串,mode==='percentage' 時使用。 */
  percentage: string
  carryover_enabled: boolean
}

export const projectCategoryBudgetDefaults = (): ProjectCategoryBudgetForm => ({
  editingId: null,
  category_id: '',
  category_name: '',
  mode: 'fixed',
  fixed_amount: '',
  percentage: '',
  carryover_enabled: false,
})

export const txTemplateDefaults = (): TxTemplateForm => ({
  editingId: null,
  name: '',
  tx_type: 'expense',
  amount: '',
  note: '',
  category_id: '',
  category_name: '',
  account_id: '',
  account_name: '',
  from_account_id: '',
  from_account_name: '',
  to_account_id: '',
  to_account_name: '',
  sort_order: '0',
})

/**
 * Phase 1.5(§2.12.2):把 `TxForm` 里的週期性收支开关字段組成
 * `RecurringInlineCreatePayload`,挂在 `createTransaction` 的
 * `TxPayload.recurring` 上。`form.recurring_enabled=false` → 返回 null
 * (不带这个字段,走普通建交易流程)。
 */
export function buildRecurringInlinePayload(form: TxForm): RecurringInlineCreatePayload | null {
  if (!form.recurring_enabled) return null
  let advanced_rule_json: RecurringAdvancedRule | null = null
  if (form.recurring_advanced_mode === 'weekly_days' && form.recurring_weekly_days.length > 0) {
    advanced_rule_json = { type: 'weekly_days', days: form.recurring_weekly_days }
  } else if (form.recurring_advanced_mode === 'monthly_day') {
    const day = Math.round(Number(form.recurring_monthly_day))
    if (Number.isFinite(day) && day >= 1 && day <= 31) {
      advanced_rule_json = { type: 'monthly_day', day }
    }
  }
  const interval = Math.max(1, Math.min(365, Math.round(Number(form.recurring_interval)) || 1))
  return {
    frequency: form.recurring_frequency,
    interval,
    end_at: form.recurring_end_at.trim() ? new Date(form.recurring_end_at).toISOString() : null,
    advanced_rule_json,
  }
}

/**
 * Phase 1.5(§2.12.1):把 `TxForm` 里的分期付款开关字段組成
 * `InstallmentPlanCreatePayload`,走独立的 `createInstallmentPlan` 调用
 * (分期跟一般交易欄位差異太大,不能塞进 `TxPayload`)。`account_id`/
 * `category_id` 由调用方解析 name→id 传入(跟一般建交易的解析逻辑共用)。
 * `form.installment_enabled=false` → 返回 null。
 */
export function buildInstallmentPlanPayload(
  form: TxForm,
  resolved: { account_id: string | null; category_id: string | null },
): InstallmentPlanCreatePayload | null {
  if (!form.installment_enabled) return null
  const periods = Math.max(1, Math.min(600, Math.round(Number(form.installment_periods)) || 0))
  const graceMonths = Math.max(0, Math.round(Number(form.installment_grace_period_months)) || 0)
  return {
    total_amount: Number(form.amount || 0),
    periods,
    first_period_at: form.happened_at,
    account_id: resolved.account_id,
    category_id: resolved.category_id,
    note: form.note || null,
    repayment_method: form.installment_repayment_method,
    interest_period: form.installment_interest_period,
    interest_rate: Number(form.installment_interest_rate || 0),
    round_amounts: form.installment_round_amounts,
    remainder_position: form.installment_remainder_position,
    grace_period_months: graceMonths,
  }
}

/**
 * 拆帳(§2.4):`TxForm.splits` 里已经填了分类的行(空分类行忽略,让用户能
 * 先加一行还没选分类的占位)。UI 校验 + 组 payload 共用同一份过滤逻辑,
 * 避免两处各写一套导致行数不一致。
 */
function _filledTxSplitRows(form: TxForm): TxSplitFormItem[] {
  return form.splits.filter((row) => row.category_id.trim().length > 0)
}

/**
 * 拆帳(§2.4)表单校验,`GlobalEditDialogs.tsx`/`TransactionsPage.tsx` 两处
 * 提交前共用,回传 i18n key(null = 通过)。跟 server 端
 * `write/_shared.py::_validate_tx_splits` 同一套规则:tx_type 只能
 * expense/income、至少 2 笔、每笔金额 > 0、加总等于交易 amount。
 */
export function validateTxSplits(form: TxForm, amountNum: number): string | null {
  if (!form.split_enabled) return null
  if (form.tx_type === 'transfer') return 'transactions.error.splitTransferNotAllowed'
  const rows = _filledTxSplitRows(form)
  if (rows.length < 2) return 'transactions.error.splitNeedsTwo'
  if (rows.some((row) => !(Number(row.amount) > 0))) return 'transactions.error.splitAmountInvalid'
  const sum = rows.reduce((acc, row) => acc + (Number(row.amount) || 0), 0)
  if (Math.abs(sum - amountNum) > 0.01) return 'transactions.error.splitSumMismatch'
  return null
}

/**
 * 拆帳(§2.4):把 `TxForm.splits` 组成 `TxPayload.splits`。调用前必须先跑
 * `validateTxSplits` 确认通过(本函数不重新校验,只负责转型)。
 * `form.split_enabled=false` → 返回空数组(server 语义:显式 [] 清空既有
 * splits,回到单一 category;create 场景下等同"没有 splits")。
 */
export function buildTxSplitsPayload(form: TxForm): TxSplitPayload[] {
  if (!form.split_enabled) return []
  return _filledTxSplitRows(form).map((row) => ({
    category_id: row.category_id.trim(),
    category_name: row.category_name.trim() || null,
    amount: Number(row.amount) || 0,
    note: row.note.trim() || null,
  }))
}
