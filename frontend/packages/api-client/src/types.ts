export type LoginResponse = {
  /** 2FA 已启用且未验证时为 true,其余字段除 challenge_token / available_methods 外都 undefined */
  requires_2fa?: boolean
  // 2FA 未启用 / 已验证时填这些(后端 AuthLoginResponse 见 .docs/2fa-design.md):
  access_token?: string
  refresh_token?: string
  expires_in?: number
  device_id?: string
  scopes?: string[]
  user?: { id: string; email: string; is_admin?: boolean }
  // 2FA 启用且未验证时填这些:
  challenge_token?: string
  available_methods?: Array<'totp' | 'recovery_code'>
}

export type TwoFASetupResponse = {
  secret: string
  qr_code_uri: string
  expires_in: number
}

export type TwoFAConfirmResponse = {
  enabled: boolean
  recovery_codes: string[]
}

export type TwoFAStatusResponse = {
  enabled: boolean
  enabled_at: string | null
}

export type TwoFARegenerateResponse = {
  recovery_codes: string[]
}

export type ProfileAppearance = {
  /** 顶部皮肤 id:'none' | 'aurora' | 'mountains' | … 详见 mobile kHeaderSkins */
  header_skin?: string
  /** 紧凑金额显示(万/亿) */
  compact_amount?: boolean
  /** 交易行是否显示时间 */
  show_transaction_time?: boolean
  /**
   * 动态皮肤是否播放动效。关掉后皮肤停在各自的静态帧(构图最完整的一刻)。
   * 存在的理由是功耗 —— 动态皮肤是持续重绘,长时间用会发烫。
   * web 自身不渲染皮肤,这里只做开关的展示与写回。
   */
  skin_animation?: boolean
  /** 明细行第一行显示方式:'category'(默认,分类+备注括号) | 'note'(备注优先) */
  note_display_mode?: 'category' | 'note'
  /** 日历是否显示法定节假日(角标/底纹)。默认开(保持既有日历行为)。 */
  show_holidays?: boolean
  /** 日历是否显示农历日期。默认关(新功能,需用户在偏好里开启)。 */
  show_lunar?: boolean
}

/**
 * AI 服务商单条配置 —— 字段命名严格对齐 mobile `AIServiceProviderConfig.toJson()`,
 * server 是透传 JSON,**不要** snake_case 化(mobile 期待 `textProviderId` /
 * `apiKey` / `isBuiltIn` 这种命名)。
 */
export type AIProvider = {
  id: string
  name: string
  isBuiltIn?: boolean
  apiKey?: string
  baseUrl?: string
  textModel?: string
  visionModel?: string
  audioModel?: string
  createdAt?: string // ISO 8601
}

export type AICapabilityBinding = {
  textProviderId?: string | null
  visionProviderId?: string | null
  speechProviderId?: string | null
}

/**
 * 完整 AI 配置 snapshot —— 跟 mobile `AIProviderManager.snapshotForSync()` 对齐。
 * server 的 `ai_config_json` 列存的就是这个 shape 序列化后的字符串。
 */
export type AIConfig = {
  providers?: AIProvider[]
  binding?: AICapabilityBinding
  custom_prompt?: string
  strategy?: string
  bill_extraction_enabled?: boolean
  use_vision?: boolean
}

/** 内置「智谱GLM」provider id —— 跟 mobile `zhipuDefault.id` 对齐,删除 fallback 用。 */
export const BUILTIN_PROVIDER_ID = 'zhipu_glm'

export type ProfileMe = {
  user_id: string
  email: string
  display_name?: string | null
  avatar_url?: string | null
  avatar_version: number
  /** mobile `incomeExpenseColorSchemeProvider` 同步过来的配色偏好：
   *  true  = 红色收入 / 绿色支出（mobile 默认）
   *  false = 红色支出 / 绿色收入
   *  null  = 未设置过，web 视为 true */
  income_is_red?: boolean | null
  /** mobile 推过来的主题色（`#RRGGBB`）。web 端用作"初始偏好"：
   *  - 用户在 web 本地改过主题色（localStorage 有值）→ 本地优先，忽略 server
   *  - 否则 apply server 值到 CSS var（不写 localStorage，保持 server 作权威） */
  theme_primary_color?: string | null
  /** mobile 推过来的外观偏好(打包的 JSON)。web 目前只读展示,不编辑。 */
  appearance?: ProfileAppearance | null
  /** mobile 推过来的 AI 配置(providers / binding / custom_prompt / strategy …)。
   *  API key 存在这里面,只读展示时要脱敏。shape 由 mobile 的 snapshotForSync
   *  定义,这里用 Record 宽松接收,避免 web 跟 mobile 的实现耦合。 */
  ai_config?: Record<string, any> | null
  /** 主币种(本位币),资产折算目标。mobile prefs `baseCurrency` 同步而来。 */
  primary_currency?: string | null
}

export type WriteCommitMeta = {
  ledger_id: string
  base_change_id: number
  new_change_id: number
  server_timestamp: string
  idempotency_replayed: boolean
  entity_id: string | null
}

export type AttachmentRef = {
  fileName: string
  originalName?: string | null
  fileSize?: number | null
  width?: number | null
  height?: number | null
  sortOrder?: number | null
  cloudFileId?: string | null
  cloudSha256?: string | null
}

export type LedgerCreatePayload = {
  ledger_id?: string | null
  ledger_name: string
  currency?: string | null
  month_start_day?: number | null
}

export type LedgerMetaPayload = {
  ledger_name?: string | null
  currency?: string | null
  month_start_day?: number | null
}

export type ReadLedger = {
  ledger_id: string
  ledger_name: string
  currency: string
  month_start_day?: number
  transaction_count: number
  income_total: number
  expense_total: number
  balance: number
  exported_at: string | null
  updated_at: string
  role: 'owner' | 'editor' | 'viewer'
  is_shared?: boolean
  member_count?: number
}

export type ReadLedgerDetail = ReadLedger & {
  source_change_id: number
}

export type ReadTransaction = {
  id: string
  tx_index: number
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  happened_at: string
  note: string | null
  category_name: string | null
  category_kind: string | null
  category_id?: string | null
  account_name: string | null
  account_id?: string | null
  from_account_name: string | null
  from_account_id?: string | null
  to_account_name: string | null
  to_account_id?: string | null
  tags: string | null
  tags_list: string[]
  tag_ids?: string[]
  attachments: AttachmentRef[] | null
  /** 不计入收支统计(仍计入账户余额/净资产)。历史交易默认 false。 */
  exclude_from_stats?: boolean
  /** 不计入预算用量(仅 expense 有意义)。历史交易默认 false。 */
  exclude_from_budget?: boolean
  /** 0022 退款进度:该支出当前有效退款合计(不含 cancelled)。仅 expense 有意义;
   *  >= amount(容差 0.011)时流水金额画删除线(已退款)。 */
  refunded_total?: number
  /** 0022 退款进度(卡维度):有效退款中实际退回本卡的钱(unified 退到本卡 /
   *  split 腿中本卡部分);跨卡退款不计入 —— 钱到了别的卡,本卡支出仍成立。
   *  >= amount(容差 0.011)时流水金额画删除线。老服务端缺省时退化 refunded_total。 */
  refunded_to_account?: number
  /** 交易原币种(ISO)。历史交易可能为 null,视作账本本位币。 */
  currency_code?: string | null
  /** 折账本本位币的金额快照(记账时汇率,保存即定)。null 时 fallback 用 amount。 */
  native_amount?: number | null
  /** 优惠/折扣(0021)。null = 无优惠。仅 expense 有意义。 */
  discount?: TxDiscount | null
  /** 转账手续费(0021)。null = 无手续费。仅 transfer 有意义。 */
  transfer_fee?: TxTransferFee | null
  /** 多账户合并付款(0021)。null = 单账户付款。仅 expense 有意义。 */
  payment_legs?: TxPaymentLeg[] | null
  /** 成员分摊(0024)。null = 未分摊(统计按记账人归属)。仅 expense 有意义。 */
  member_shares?: TxMemberShare[] | null
  /** 归物(0029):关联物品的 syncId。null = 未关联。 */
  item_id?: string | null
  /** 归物(0029):关联物品名(服务端按 item_id 现查)。 */
  item_name?: string | null
  /** 定期存款利息(0030):关联定期存款的 syncId。null = 未关联。 */
  time_deposit_id?: string | null
  /** 0031:关联定存的展示名(服务端按账户名 + 币种本金合成)。仅展示用,存款被删为 null。 */
  time_deposit_name?: string | null
  /** 0032:退款到账关联交易回显的原账单摘要(备注 > 分类名 >「金额 · 日期」)。
   *  仅退款 received 生成的 income 关联交易有值;其余交易恒为 null。 */
  refund_source_label?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
  created_by_display_name?: string | null
  created_by_avatar_url?: string | null
  created_by_avatar_version?: number | null
  // §7 共享账本 — server projection 的 last_edited_by_user_id 加上 user 信息回填,
  // tx 列表显示"创建 / 编辑"双角色。
  last_edited_by_user_id?: string | null
  last_edited_by_email?: string | null
  last_edited_by_display_name?: string | null
  last_edited_by_avatar_url?: string | null
  last_edited_by_avatar_version?: number | null
  /** 资产流水余额:该交易完成后其主账户的余额(server `with_balance=true` 时返回,
   *  否则 null)。口径与账户当前余额一致。null 时前端不展示。 */
  balance_after?: number | null
  /** 合并付款(0021)时,各腿账户在该笔完成后的余额,形如
   *  {account_sync_id: balance_after_for_that_leg}。仅 `with_balance=true` 且本笔
   *  为合并付款时才有值;单账户交易为 null(前端回退到 balance_after)。 */
  balance_by_account?: Record<string, number> | null
}

export type ReadAccount = {
  id: string
  name: string
  account_type: string | null
  currency: string | null
  initial_balance: number | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
  /** 备注,所有类型可填。null = 未填。 */
  note?: string | null
  /** 信用额度,仅 credit_card。 */
  credit_limit?: number | null
  /** 账单日(1-31),仅 credit_card。 */
  billing_day?: number | null
  /** 还款日(1-31),仅 credit_card。 */
  payment_due_day?: number | null
  /** 开户行,bank_card / credit_card 元信息。 */
  bank_name?: string | null
  /** 卡号后四位,bank_card / credit_card。 */
  card_last_four?: string | null
  /** 完整卡号(0026)。用户手动录入;服务端派生 card_last_four。 */
  card_number?: string | null
  /** 账户图标 key(0026):内置银行/支付图标注册表主键(如 'icbc');
   *  空 = 按 account_type 回退默认图标。 */
  icon?: string | null
  /** 账户别名(0027):展示用短名,所有账户类型可用。空 = 显示 name。
   *  只影响展示,不参与任何统计口径。 */
  alias?: string | null
  /** 出账日(账单日)当天消费计入哪一期账单(0027,信用卡):
   *  true = 计入当期账单;false = 计入下一期。缺省 true。 */
  billing_day_in_current?: boolean
  /** 账户隐藏(issue #240):true = 已隐藏 —— 记账/转账选择器不再出现,主列表
   *  退场收进「已隐藏」分区;但仍计入净资产/资产/收支(D1,服务端不做统计过滤)。
   *  缺省 false(旧接口未提供该字段时视为未隐藏)。 */
  hidden?: boolean
}

export type ReadCategory = {
  id: string
  name: string
  kind: 'expense' | 'income' | 'transfer'
  level: number | null
  sort_order: number | null
  icon: string | null
  icon_type: string | null
  custom_icon_path?: string | null
  icon_cloud_file_id?: string | null
  icon_cloud_sha256?: string | null
  parent_name: string | null
  parent_sync_id?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
}

export type ReadTag = {
  id: string
  name: string
  color: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
}

export type ReadBudget = {
  id: string
  /** `total` = 整账本总预算 / `category` = 分类预算 */
  type: 'total' | 'category' | string
  category_id?: string | null
  category_name?: string | null
  amount: number
  period: 'monthly' | 'weekly' | 'yearly' | string
  start_day: number
  enabled: boolean
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

/** 定期存款(0021,Cloud issue #80)。派生字段由服务端按今天现算。 */
/** 定期存款到息计划(0028,可多笔)。 */
export type TimeDepositPayoutPlan = {
  /** 到息日 'YYYY-MM-DD'。 */
  date: string
  /** 该次金额(自由金额,不区分利息/本息)。 */
  amount: number
  note?: string | null
  /** 是否已到息:服务端按「今天」派生(不落库)。 */
  is_due: boolean
}

/** 到息计划写入形态。 */
export type TimeDepositPayoutPlanInput = {
  date: string
  amount: number
  note?: string | null
}

export type ReadTimeDeposit = {
  id: string
  principal: number
  currency: string
  /** 年利率小数(0.0325 = 3.25%)。 */
  annual_rate: number
  term_months: number
  start_date: string
  maturity_date: string
  /** holding = 存期内 / matured = 已到期未支取 / withdrawn = 已支取 */
  status: 'holding' | 'matured' | 'withdrawn' | string
  account_id?: string | null
  account_name?: string | null
  auto_renew: boolean
  /** simple = 单利 / compound = 复利(按年) */
  interest_mode: 'simple' | 'compound' | string
  note?: string | null
  withdrawn_at?: string | null
  /** 实得利息(支取时填写)。 */
  actual_interest?: number | null
  /** 预期利息(withdrawn 时 = actual_interest)。 */
  expected_interest: number
  /** 距到期天数(负数 = 已过期未支取;withdrawn 恒 0)。 */
  days_remaining: number
  is_matured: boolean
  /** 到息计划(0028,多笔);仅作预期提醒,不生成交易、不进余额。 */
  payout_plans?: TimeDepositPayoutPlan[]
  /** 计划金额合计。 */
  payout_plans_total?: number
  /** 下一个尚未到息的计划日期;全部已到息/无计划 → null。 */
  next_payout_date?: string | null
  /** 已收利息(0030):关联交易中 income 的金额合计(真实入账的利息流水)。 */
  interest_received?: number
  /** 已收利息笔数(关联交易中 income 的条数)。 */
  interest_tx_count?: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type TimeDepositCreatePayload = {
  principal: number
  currency?: string | null
  /** 年利率小数,默认 0。 */
  annual_rate?: number
  /** 期限(月),默认 12。 */
  term_months?: number
  /** 'YYYY-MM-DD' 或 ISO datetime。 */
  start_date: string
  /** 到期日可不传 —— 服务端按 start_date + term_months 推算。 */
  maturity_date?: string | null
  status?: 'holding' | 'matured' | 'withdrawn'
  account_id?: string | null
  account_name?: string | null
  auto_renew?: boolean
  interest_mode?: 'simple' | 'compound'
  note?: string | null
  withdrawn_at?: string | null
  actual_interest?: number | null
  /** 到息计划(0028,多笔):不传 = 无计划。 */
  payout_plans?: TimeDepositPayoutPlanInput[] | null
}

export type TimeDepositUpdatePayload = {
  principal?: number
  currency?: string | null
  annual_rate?: number
  term_months?: number
  start_date?: string | null
  maturity_date?: string | null
  status?: 'holding' | 'matured' | 'withdrawn'
  account_id?: string | null
  account_name?: string | null
  auto_renew?: boolean | null
  interest_mode?: 'simple' | 'compound'
  note?: string | null
  withdrawn_at?: string | null
  actual_interest?: number | null
  /** 到息计划(0028,多笔):不传 = 保持现值;显式 [] / null = 清空。 */
  payout_plans?: TimeDepositPayoutPlanInput[] | null
}

/** 归物 · 物品持有(0029):一件「我持有的物品」的全生命周期台账。 */
export type ReadItem = {
  id: string
  name: string
  /** 购买日期。 */
  purchase_date: string
  purchase_amount: number
  /** 保修到期日;null = 未记录保修。 */
  warranty_until?: string | null
  /** in_use = 在用(继续摊成本) / disposed = 已处置(二手卖出或报废)。 */
  status: 'in_use' | 'disposed' | string
  /** 处置方式:sold = 二手卖出(有回收款)/ scrapped = 报废。 */
  disposal_type?: 'sold' | 'scrapped' | string | null
  disposal_date?: string | null
  /** 二手卖出收入(抵减成本);报废恒 0。 */
  disposal_amount?: number | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  account_name?: string | null
  note?: string | null
  // --- 派生(服务端按「今天」现算,不落库)------------------------------- //
  /** 持有天数(含购买当天;已处置则定格在处置日)。 */
  held_days: number
  /** 净成本 = 购买金额 + 关联交易支出 - 二手卖出收入(可为负)。 */
  total_cost: number
  /** 日均成本 = 净成本 / 持有天数。 */
  daily_cost: number
  /** 关联交易支出累计。 */
  linked_expense: number
  /** 关联交易收入累计(回收款 / 折价补偿 / 保修理赔……),抵减净成本。 */
  linked_income: number
  /** none = 没记保修 / active = 保修中 / expired = 已过保(物品仍存活)。 */
  warranty_status: 'none' | 'active' | 'expired' | string
  /** 保修剩余天数;expired 时为负;none 时为 null。 */
  warranty_days_remaining?: number | null
  is_disposed: boolean
  /** 关联交易笔数。 */
  linked_tx_count: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type ItemCreatePayload = {
  name: string
  /** 'YYYY-MM-DD' 或 ISO datetime。 */
  purchase_date: string
  purchase_amount?: number
  warranty_until?: string | null
  status?: 'in_use' | 'disposed'
  disposal_type?: 'sold' | 'scrapped' | null
  disposal_date?: string | null
  disposal_amount?: number | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  account_name?: string | null
  note?: string | null
}

export type ItemUpdatePayload = {
  name?: string
  purchase_date?: string | null
  purchase_amount?: number
  warranty_until?: string | null
  /** 改回 in_use 会清掉 disposal_* 三个字段。 */
  status?: 'in_use' | 'disposed'
  disposal_type?: 'sold' | 'scrapped' | null
  disposal_date?: string | null
  disposal_amount?: number | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  account_name?: string | null
  note?: string | null
}

/** 归物(退货/退还物品,0021,App issue #465)。 */
export type ReadItemReturn = {
  id: string
  name: string
  /** 关联原始购买交易 syncId(弱引用,可空)。 */
  original_tx_id?: string | null
  original_amount?: number | null
  refund_amount: number
  /** 归还/退款日期。 */
  happened_at: string
  reason?: string | null
  /** pending = 待归还 / returned = 已归还待退款 / refunded = 已退款入账 / cancelled = 已取消 */
  status: 'pending' | 'returned' | 'refunded' | 'cancelled' | string
  account_id?: string | null
  account_name?: string | null
  category_id?: string | null
  category_name?: string | null
  note?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type ItemReturnCreatePayload = {
  name: string
  original_tx_id?: string | null
  original_amount?: number | null
  refund_amount?: number
  /** 'YYYY-MM-DD' 或 ISO datetime。 */
  happened_at: string
  reason?: string | null
  status?: 'pending' | 'returned' | 'refunded' | 'cancelled'
  account_id?: string | null
  account_name?: string | null
  category_id?: string | null
  category_name?: string | null
  note?: string | null
}

export type ItemReturnUpdatePayload = {
  name?: string
  original_tx_id?: string | null
  original_amount?: number | null
  refund_amount?: number
  happened_at?: string | null
  reason?: string | null
  status?: 'pending' | 'returned' | 'refunded' | 'cancelled'
  account_id?: string | null
  account_name?: string | null
  category_id?: string | null
  category_name?: string | null
  note?: string | null
}

/** 退款腿(0025):分别退款时每条腿的去向账户 + 金额。 */
export type RefundLeg = {
  /** 该腿退款去向账户 syncId。 */
  accountId: string
  /** 该腿退款去向账户名(快照,账户被删后历史仍显示)。 */
  accountName?: string | null
  /** 该腿退款金额;sum(legs) == 退款总额(容差 0.011)。 */
  amount: number
}

/** 交易退款(0022,App issue #465)。挂在原始支出交易上的退款记录。 */
export type ReadRefund = {
  id: string
  /** 关联原始支出交易 syncId。 */
  original_tx_id: string
  /** 原单金额快照(服务端写入时从原交易带过来)。 */
  original_amount?: number | null
  /** 本笔退款金额。 */
  amount: number
  currency: string
  /** 退款去向账户(unified 模式)。 */
  account_id?: string | null
  account_name?: string | null
  /** 退款日期。 */
  happened_at: string
  reason?: string | null
  /** pending = 待退款 / received = 已到账 / cancelled = 已取消 */
  status: 'pending' | 'received' | 'cancelled' | string
  note?: string | null
  /** 退款分摊模式(0025):unified=统一退至单一账户 / split=分别退款。 */
  refund_mode?: 'unified' | 'split' | string
  /** 分别退款(0025)的退款腿:[{accountId,accountName,amount}]。 */
  refund_legs?: RefundLeg[] | null
  /** 该原单当前有效退款合计(不含 cancelled,含本笔;服务端现算)。 */
  refunded_total: number
  /** 该原单剩余可退金额(服务端现算)。 */
  remaining_refundable: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type RefundCreatePayload = {
  original_tx_id: string
  /** 本笔退款金额(> 0;同单累计不得超过原单)。 */
  amount: number
  /** 缺省取原交易币种。 */
  currency?: string | null
  account_id?: string | null
  account_name?: string | null
  /** 'YYYY-MM-DD' 或 ISO datetime。 */
  happened_at: string
  reason?: string | null
  status?: 'pending' | 'received' | 'cancelled'
  note?: string | null
  /** 退款分摊模式(0025):unified=统一退至单一账户 / split=分别退款。 */
  refund_mode?: 'unified' | 'split'
  /** 分别退款(0025)腿:[{accountId,accountName,amount}]。 */
  refund_legs?: RefundLeg[] | null
}

export type RefundUpdatePayload = {
  original_tx_id?: string
  amount?: number
  currency?: string | null
  account_id?: string | null
  account_name?: string | null
  happened_at?: string | null
  reason?: string | null
  status?: 'pending' | 'received' | 'cancelled'
  note?: string | null
  /** 退款分摊模式(0025)。 */
  refund_mode?: 'unified' | 'split'
  /** 分别退款(0025)腿。 */
  refund_legs?: RefundLeg[] | null
}

/** 借款(0023)。direction:borrowed=借入 / lent=借出。 */
export type ReadLoan = {
  id: string
  /** borrowed = 借入(欠别人);lent = 借出(别人欠我)。 */
  direction: 'borrowed' | 'lent' | string
  /** 交易对手(谁借给我 / 我借给谁)。 */
  counterparty: string
  /** 本金。 */
  principal: number
  currency: string
  /** 年利率小数(0.05 = 5%)。 */
  interest_rate: number
  /** 起借日 'YYYY-MM-DD' 或 ISO。 */
  started_at: string
  /** 到期日(可空,无限期)。 */
  due_at?: string | null
  /** 已还金额。 */
  repaid_amount: number
  /** active = 在借 / repaid = 已结清 / cancelled = 作废 */
  status: 'active' | 'repaid' | 'cancelled' | string
  account_id?: string | null
  account_name?: string | null
  note?: string | null
  /** 还款进度 0..1(服务端按今天现算)。 */
  repay_progress: number
  /** 距到期天数(无到期日 = null,负数 = 已过期)。 */
  days_to_due?: number | null
  /** 已逾期(due_at 早于今天 且仍 active)。 */
  is_overdue: boolean
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type LoanCreatePayload = {
  direction?: 'borrowed' | 'lent'
  /** 必填,交易对手。 */
  counterparty: string
  /** 本金 > 0。 */
  principal: number
  currency?: string | null
  /** 年利率小数,>= 0,默认 0。 */
  interest_rate?: number
  /** 起借日 'YYYY-MM-DD' 或 ISO,必填。 */
  started_at: string
  /** 到期日(可空)。 */
  due_at?: string | null
  /** 已还金额,>= 0,默认 0。 */
  repaid_amount?: number
  status?: 'active' | 'repaid' | 'cancelled'
  account_id?: string | null
  account_name?: string | null
  note?: string | null
}

export type LoanUpdatePayload = {
  direction?: 'borrowed' | 'lent' | null
  counterparty?: string | null
  principal?: number | null
  currency?: string | null
  interest_rate?: number | null
  started_at?: string | null
  due_at?: string | null
  repaid_amount?: number | null
  status?: 'active' | 'repaid' | 'cancelled' | null
  account_id?: string | null
  account_name?: string | null
  note?: string | null
}

/** 报销(0023)。title 事由,payer 垫付方,reimburser 给付方。 */
export type ReadReimbursement = {
  id: string
  /** 报销事由。 */
  title: string
  /** 实际垫付方(通常是我)。 */
  payer?: string | null
  /** 报销给付方(公司 / 他人)。 */
  reimburser?: string | null
  amount: number
  currency: string
  category_id?: string | null
  category_name?: string | null
  /** 发生日 'YYYY-MM-DD' 或 ISO。 */
  happened_at: string
  /** pending = 待报销 / reimbursed = 已到账 / cancelled = 已取消 */
  status: 'pending' | 'reimbursed' | 'cancelled' | string
  account_id?: string | null
  account_name?: string | null
  /** 关联垫付交易 syncId(弱引用,可空)。 */
  related_tx_id?: string | null
  note?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type ReimbursementCreatePayload = {
  /** 报销事由,必填。 */
  title: string
  payer?: string | null
  reimburser?: string | null
  /** 金额 > 0。 */
  amount: number
  currency?: string | null
  category_id?: string | null
  category_name?: string | null
  /** 发生日 'YYYY-MM-DD' 或 ISO,必填。 */
  happened_at: string
  status?: 'pending' | 'reimbursed' | 'cancelled'
  account_id?: string | null
  account_name?: string | null
  related_tx_id?: string | null
  note?: string | null
}

export type ReimbursementUpdatePayload = {
  title?: string | null
  payer?: string | null
  reimburser?: string | null
  amount?: number | null
  currency?: string | null
  category_id?: string | null
  category_name?: string | null
  happened_at?: string | null
  status?: 'pending' | 'reimbursed' | 'cancelled' | null
  account_id?: string | null
  account_name?: string | null
  related_tx_id?: string | null
  note?: string | null
}

export type WorkspaceTransaction = ReadTransaction & {
  ledger_id: string
  ledger_name: string
  created_by_user_id: string | null
  created_by_email: string | null
  created_by_display_name?: string | null
  created_by_avatar_url?: string | null
  created_by_avatar_version?: number | null
}

export type WorkspaceTransactionPage = {
  items: WorkspaceTransaction[]
  total: number
  limit: number
  offset: number
}

export type WorkspaceAccount = ReadAccount & {
  ledger_id: string | null
  ledger_name: string | null
  created_by_user_id: string | null
  created_by_email: string | null
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

export type WorkspaceCategory = ReadCategory & {
  ledger_id: string | null
  ledger_name: string | null
  created_by_user_id: string | null
  created_by_email: string | null
  // 服务端按 category_sync_id 聚合的笔数,跨所有账本累加(跟 dedup 后的展
  // 示口径一致)。None = 历史接口未提供。
  tx_count?: number | null
}

export type WorkspaceTag = ReadTag & {
  ledger_id: string | null
  ledger_name: string | null
  created_by_user_id: string | null
  created_by_email: string | null
  // 服务端一次性算好，跨全账本全期。前端不再需要自己从分页 tx 里聚合。
  tx_count?: number | null
  expense_total?: number | null
  income_total?: number | null
}

export type AnalyticsScope = 'month' | 'year' | 'all'
export type AnalyticsMetric = 'expense' | 'income' | 'balance'

export type WorkspaceLedgerCounts = {
  tx_count: number
  /** 首次记账到今天（含当天）。对齐 mobile `getCountsForLedger` 的 dayCount。 */
  days_since_first_tx: number
  /** 有数据的日期数（distinct DATE）。备用字段，首页不用。 */
  distinct_days: number
  first_tx_at?: string | null
}

export type WorkspaceAnalyticsSummary = {
  transaction_count: number
  income_total: number
  expense_total: number
  balance: number
  distinct_days?: number
  first_tx_at?: string | null
  last_tx_at?: string | null
}

export type WorkspaceAnalyticsSeriesItem = {
  bucket: string
  expense: number
  income: number
  balance: number
}

export type WorkspaceAnalyticsCategoryRank = {
  category_name: string
  total: number
  tx_count: number
}

export type WorkspaceAnalyticsAnomalyAttribution = {
  category_name: string
  amount: number
  /** 该分类在其他月份的中位数;本月独有(其他月都 0)时为 0。 */
  median_others: number
  /** amount / median_others;本月独有时为 null,前端显示"本月独有"。 */
  multiplier: number | null
}

export type WorkspaceAnalyticsAnomalyMonth = {
  /** "YYYY-MM" */
  bucket: string
  expense: number
  /** median(已发生月份的 expense),见 .docs/dashboard-anomaly-budget/plan.md §2.1 */
  baseline: number
  /** (expense - baseline) / baseline */
  deviation_pct: number
  /** 归因到的 top 1-2 分类(按 diff 降序) */
  top_attributions: WorkspaceAnalyticsAnomalyAttribution[]
}

export type WorkspaceAnalyticsRange = {
  scope: AnalyticsScope
  metric: AnalyticsMetric
  period: string | null
  start_at: string | null
  end_at: string | null
}

export type WorkspaceAnalytics = {
  summary: WorkspaceAnalyticsSummary
  series: WorkspaceAnalyticsSeriesItem[]
  category_ranks: WorkspaceAnalyticsCategoryRank[]
  /** 仅 scope=year 时填,已发生月份 < 3 时为空。 */
  anomaly_months: WorkspaceAnalyticsAnomalyMonth[]
  range: WorkspaceAnalyticsRange
}

export type UserAdmin = {
  id: string
  email: string
  is_admin: boolean
  is_enabled: boolean
  created_at: string
  display_name?: string | null
  avatar_url?: string | null
  avatar_version?: number
}

export type UserAdminCreatePayload = {
  email: string
  password: string
  is_admin?: boolean
  is_enabled?: boolean
}

export type UserAdminList = {
  total: number
  items: UserAdmin[]
}

export type AdminOverview = {
  users_total: number
  users_enabled_total: number
  ledgers_total: number
  transactions_total: number
  accounts_total: number
  categories_total: number
  tags_total: number
}

export type AdminHealth = {
  status: string
  db: string
  online_ws_users: number
  time: string
}

export type RagIndexLanguageStatus = {
  build_time: string | null
  chunk_count: number
  dim: number
}

export type RagIndexStatus = {
  source: string
  corpus_hash: string | null
  embedding_model: string | null
  languages: Record<string, RagIndexLanguageStatus>
  last_checked_at: string | null
  last_updated_at: string | null
  last_error: string | null
  remote_corpus_hash: string | null
  is_latest: boolean | null
}

// ────────── 数据清理(替代旧 IntegrityScan)──────────

export type DataCleanupOrphanType =
  | 'tx_missing_category'
  | 'tx_missing_account'
  | 'tx_missing_from_account'
  | 'tx_missing_to_account'
  | 'budget_missing_category'
  | 'sync_change_missing_entity'
  | 'attachment_no_ref'
  | 'attachment_file_missing'
  | 'disk_file_no_ref'
  | 'tx_ref_broken_attachment'

export type DataCleanupRecord = {
  type: DataCleanupOrphanType | string
  title: string
  subtitle: string
  user_id?: string | null
  row_id?: string | null
  sync_id?: string | null
  file_path?: string | null
  size_bytes?: number | null
  extra?: Record<string, unknown> | null
}

export type DataCleanupScanReport = {
  db_orphans: DataCleanupRecord[]
  file_orphans: DataCleanupRecord[]
  sync_orphans: DataCleanupRecord[]
  total_count: number
  total_size_bytes: number
}

export type DataCleanupFailure = {
  record_key: string
  error: string
}

export type DataCleanupResult = {
  success_count: number
  failures: DataCleanupFailure[]
}

export type AdminSyncErrorItem = {
  id: number
  action: string
  metadata: Record<string, unknown> | null
  createdAt: string
}

export type AdminSyncErrors = {
  count: number
  items: AdminSyncErrorItem[]
}

export type AdminLogEntry = {
  seq: number
  ts: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | string
  logger: string
  message: string
  ledger_id?: string | null
  user_id?: string | null
  device_id?: string | null
}

export type AdminLogList = {
  items: AdminLogEntry[]
  capacity: number
  latest_seq: number
}

export type AdminBackupArtifact = {
  id: string
  ledger_id: string
  kind: 'db' | 'snapshot'
  file_name: string
  content_type: string | null
  checksum: string
  size: number
  created_at: string
  created_by: string
  note: string | null
  metadata: Record<string, unknown>
}

export type AdminBackupCreateResponse = {
  snapshot_id: string
  ledger_id: string
  created_at: string
}

export type AdminBackupRestoreResponse = {
  restored: boolean
  ledger_id: string
  change_id: number
}

export type TxPayload = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  happened_at: string
  /** 交易级多币种(0018):原币种;不传 = 账本本位币(不产生字段)。 */
  currency_code?: string | null
  /** 折账本本位币的金额快照(前端按 server 汇率算好传入)。 */
  native_amount?: number | null
  note?: string | null
  category_name?: string | null
  category_kind?: 'expense' | 'income' | 'transfer' | null
  category_id?: string | null
  account_name?: string | null
  account_id?: string | null
  from_account_name?: string | null
  from_account_id?: string | null
  to_account_name?: string | null
  to_account_id?: string | null
  tags?: string | string[] | null
  tag_ids?: string[] | null
  attachments?: AttachmentRef[] | null
  /** 不计入收支统计(income/expense 可填,transfer 无意义)。 */
  exclude_from_stats?: boolean | null
  /** 不计入预算用量(仅 expense 有意义)。 */
  exclude_from_budget?: boolean | null
  /** 优惠/折扣(0021)。仅 expense;显式 null = 清除。 */
  discount?: TxDiscount | null
  /** 转账手续费(0021)。仅 transfer;显式 null = 清除。 */
  transfer_fee?: TxTransferFee | null
  /** 多账户合并付款(0021)。仅 expense;sum(legs) 必须等于 amount。显式 null = 清除。 */
  payment_legs?: TxPaymentLeg[] | null
  /** 成员分摊(0024)。仅 expense;sum(shares.amount) 必须等于 amount。 */
  member_shares?: TxMemberShare[] | null
}

/** 优惠/折扣(0021)。嵌套对象键跟 snapshot 惯例一致用 camelCase(mobile 友好)。finalAmount 恒等于 tx.amount(实付)。 */
export type TxDiscount = {
  /** percent = 按折扣率(value 为 0.85 表示 85 折);amount = 按减免额。 */
  mode: 'percent' | 'amount'
  /** percent: 折扣率小数(0.85);amount: 减免金额。 */
  value: number
  /** 券名/优惠描述,可空。 */
  couponName?: string | null
  /** 原价(优惠前)。 */
  originalAmount?: number | null
  /** 优惠金额(originalAmount - finalAmount)。 */
  discountAmount?: number | null
  /** 实付金额(恒等于 tx.amount)。 */
  finalAmount?: number | null
}

/** 转账手续费(0021)。 */
export type TxTransferFee = {
  /** 手续费/优惠金额。 */
  amount: number
  /**
   * deduct_from_source — 转出账户扣 amount+fee(到账 amount);
   * deduct_from_target — 转出扣 amount(到账 amount-fee);
   * separate_account — 手续费从 feeAccountId 另扣;
   * discount — 转出方少扣 fee(实扣 amount-fee,到账 amount),银行/平台优惠。
   */
  mode: 'deduct_from_source' | 'deduct_from_target' | 'separate_account' | 'discount'
  /** separate_account 模式下的手续费扣款账户(syncId)。 */
  feeAccountId?: string | null
  feeAccountName?: string | null
}

/** 合并付款的一条腿(0021)。 */
export type TxPaymentLeg = {
  accountId: string
  accountName?: string | null
  /** 该腿承担的金额。sum(legs) == tx.amount。 */
  amount: number
}

/** 成员分摊的一份(0024)。把一笔支出按「人」拆开,不动账户余额。 */
export type TxMemberShare = {
  /** 成员 syncId(账本内自定义成员,不是登录用户)。 */
  memberId: string
  /** 成员名快照:成员被删后历史账单仍按当时的人名展示。 */
  memberName?: string | null
  /** 该成员分摊比例 0..1(展示用;金额才是权威)。 */
  ratio?: number | null
  /** 该成员分摊金额。sum(shares) == tx.amount。 */
  amount: number
}

/** 账本内自定义成员(0024)。跟共享账本的登录用户成员是两套东西。 */
export type ReadMember = {
  id: string
  name: string
  note?: string | null
  sort_order?: number
  /** 成员图标 key(人物语义 Material Symbols 名);空 = 前端回退默认图标。 */
  icon?: string | null
  /** 关联的记账人(登录用户 id);空 = 未关联。 */
  linked_user_id?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type MemberCreatePayload = {
  name: string
  note?: string | null
  sort_order?: number
  icon?: string | null
  linked_user_id?: string | null
}

export type MemberUpdatePayload = {
  name?: string
  note?: string | null
  sort_order?: number | null
  icon?: string | null
  linked_user_id?: string | null
}

/** 成员维度统计的一行(0024)。kind: member = 自定义成员分摊;user = 记账人。 */
export type MemberShareStat = {
  kind: 'member' | 'user' | string
  id: string
  name: string
  income_total: number
  expense_total: number
  tx_count: number
}

export type MemberShareStats = {
  ledger_id: string
  ledger_currency: string
  scope: string
  period?: string | null
  start_at?: string | null
  end_at?: string | null
  items: MemberShareStat[]
}

/** 交易对手维度统计的一行(counterparty-stats)。net = lent_total - borrowed_total;
 *  正数表示对方净欠我,负数表示我净欠对方(平帐视角)。 */
export type CounterpartyStat = {
  counterparty: string
  lent_total: number
  borrowed_total: number
  net: number
  loan_count: number
}

export type CounterpartyStats = {
  ledger_id: string
  ledger_currency: string
  scope: string
  period?: string | null
  status: string
  start_at?: string | null
  end_at?: string | null
  items: CounterpartyStat[]
}

export type BudgetCreatePayload = {
  type: 'total' | 'category'
  /** category 预算必填,total 可省略;后端校验。 */
  category_id?: string | null
  amount: number
  period?: 'monthly' | 'weekly' | 'yearly'
  /** 起始日(1-28),默认 1。 */
  start_day?: number
  enabled?: boolean
}

export type BudgetUpdatePayload = {
  amount?: number
  period?: 'monthly' | 'weekly' | 'yearly'
  start_day?: number
  enabled?: boolean
}

export type AccountPayload = {
  name: string
  account_type?: string | null
  currency?: string | null
  initial_balance?: number | null
  note?: string | null
  credit_limit?: number | null
  billing_day?: number | null
  payment_due_day?: number | null
  bank_name?: string | null
  card_last_four?: string | null
  /** 完整卡号(0026)。card_last_four 缺省时服务端从其后四位派生。 */
  card_number?: string | null
  /** 账户图标 key(0026):内置银行/支付图标注册表主键;空 = 按 account_type 回退。 */
  icon?: string | null
  /** 账户别名(0027):展示用短名。空串 / null = 无别名(显示 name)。 */
  alias?: string | null
  /** 出账日计入当期(0027)。true = 账单日当天消费入本期账单;false = 入下期。
   *  update 时不传 = 不改。 */
  billing_day_in_current?: boolean | null
  /** 账户隐藏(issue #240)。create 缺省 false;update 不传 = 不改(服务端
   *  merge 缺键保留,见 snapshot_mutator._apply_account_optional_fields)。 */
  hidden?: boolean | null
}

export type CategoryPayload = {
  name: string
  kind: 'expense' | 'income' | 'transfer'
  level?: number | null
  sort_order?: number | null
  icon?: string | null
  icon_type?: string | null
  custom_icon_path?: string | null
  icon_cloud_file_id?: string | null
  icon_cloud_sha256?: string | null
  parent_name?: string | null
  parent_sync_id?: string | null
}

export type TagPayload = {
  name: string
  color?: string | null
}

export type AdminDevice = {
  id: string
  name: string
  platform: string
  app_version: string | null
  os_version: string | null
  device_model: string | null
  last_ip: string | null
  created_at: string
  last_seen_at: string
  is_online: boolean
  user_id: string
  user_email: string
}

export type AdminDeviceList = {
  total: number
  items: AdminDevice[]
}

export type AttachmentUploadOut = {
  file_id: string
  ledger_id: string
  sha256: string
  size: number
  mime_type: string | null
  file_name: string
  created_at: string
}

export type AttachmentExistsItem = {
  sha256: string
  exists: boolean
  file_id: string | null
  size: number | null
  mime_type: string | null
}

export type AttachmentBatchExistsResponse = {
  items: AttachmentExistsItem[]
}

// === 共享账本 Editor 视角资源 ===
// 对应 server src/routers/shared_resources.py — Editor 进共享账本后通过
// /ledgers/{external_id}/shared-resources 拉一次 Owner 的 user-global 资源
// 快照,前端缓存到独立 state(Map<ledgerId, SharedResourcesBundle>),
// picker / tile / icon lookup 在共享账本场景下走这套数据,不污染用户
// 自己的 user-global state。effacing mobile 端 SharedLedger{Categories,
// Accounts,Tags} 镜像表的思路。
export type SharedCategoryItem = {
  sync_id: string
  name: string | null
  kind: string | null
  icon: string | null
  icon_type: string | null
  icon_cloud_file_id: string | null
  icon_cloud_sha256: string | null
  sort_order: number | null
  level: number | null
  parent_name: string | null
  // 二级分类父子关系的稳定 FK(parent 的 sync_id)。client 优先用它建父子链,
  // parent_name 是显示 / 兜底。
  parent_sync_id: string | null
}

export type SharedAccountItem = {
  sync_id: string
  name: string | null
  account_type: string | null
  currency: string | null
  initial_balance: number | null
  note: string | null
  credit_limit: number | null
  billing_day: number | null
  payment_due_day: number | null
  bank_name: string | null
  card_last_four: string | null
}

export type SharedTagItem = {
  sync_id: string
  name: string | null
  color: string | null
}

export type SharedResourcesBundle = {
  owner_user_id: string
  categories: SharedCategoryItem[]
  accounts: SharedAccountItem[]
  tags: SharedTagItem[]
}

export type ExchangeRatesResponse = {
  base: string
  rate_date: string
  source: string
  fetched_at: string
  stale: boolean
  /** 方向:1 base = x quote(展示折算前需取倒数,与 App 同规则)。 */
  rates: Record<string, string>
}

export type ExchangeRateOverride = {
  sync_id: string
  base_currency: string
  quote_currency: string
  /** 方向:1 quote = rate base。 */
  rate: string
  updated_at: string
}

export type NetWorthHistorySeriesItem = {
  bucket: string
  net_worth: number
  assets: number
  liabilities: number
}

export type NetWorthHistory = {
  series: NetWorthHistorySeriesItem[]
  multi_currency: boolean
}
