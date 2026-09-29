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

export type SsoStatusResponse = {
  sso_enabled: boolean
  password_login_enabled: boolean
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
  /** 明细行第一行显示方式:'category'(默认,分类+备注括号) | 'note'(备注优先) */
  note_display_mode?: 'category' | 'note'
  /** 分类图示画风:'material'(默认,Material Symbols) | 'cute'(手绘可爱线稿,
   *  跟 mobile `CategoryIconStyle` 对齐,详见 mobile 侧 `theme_providers.dart`)。 */
  category_icon_style?: 'material' | 'cute'
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
  /** "openai"(默认,OpenAI-compatible /chat/completions) | "gemini"(Gemini 原生
   *  generateContent API)。跟 mobile `apiFamily` 对齐,省略时按 'openai' 处理。 */
  apiFamily?: 'openai' | 'gemini'
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
  /** 'adjustment'(§2.10 Phase 5,餘額調整):語意化端點
   *  `POST .../accounts/{accountId}/balance-adjustment` 產生,無分類/無拆帳/
   *  無轉帳對象,只綁 `account_id`。 */
  tx_type: 'expense' | 'income' | 'transfer' | 'adjustment'
  amount: number
  happened_at: string
  /** 延後入帳(§2.10 Phase 5):這筆交易的實際入帳日與 `happened_at`(消費日)
   *  不同時填 —— 比如信用卡商戶延遲請款。對帳 / 信用卡帳單週期歸屬按
   *  `deferred_posting_at ?? happened_at` 計算。null = 未設定(用
   *  `happened_at`)。 */
  deferred_posting_at?: string | null
  /** 對帳模式(§2.10,2026-08-09 改版):非 null = 使用者在對帳模式裡勾選
   *  確認過這筆交易。 */
  reconciled_at?: string | null
  note: string | null
  /** 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。 */
  merchant?: string | null
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
  /** 交易原币种(ISO)。历史交易可能为 null,视作账本本位币。 */
  currency_code?: string | null
  /** 折账本本位币的金额快照(记账时汇率,保存即定)。null 时 fallback 用 amount。 */
  native_amount?: number | null
  /** 跨幣別轉帳(2026-08):僅 tx_type=transfer 且轉出/轉入帳戶幣別不同時有
   *  值——轉入帳戶自身幣別的金額(既有 amount 欄位語意不變,仍是轉出帳戶
   *  自身幣別)。null = 同幣別轉帳,fallback 用 amount。 */
  to_amount?: number | null
  /** 手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction):
   *  base_amount=使用者輸入的原始金額(信用卡回饋計算的權威基準,null=
   *  沒用過這個功能,fallback 用 amount);fee_amount/discount_amount=
   *  額外調整金額;fee_label/discount_label=自訂名稱(null=顯示預設
   *  「手續費」「折扣」)。 */
  base_amount?: number | null
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
  /** 退款(§2.6):这笔交易是对哪笔支出的退款。null = 普通交易。 */
  refund_of_id?: string | null
  /** 分期付款(§2.3):所属分期计划的 id。null = 非分期生成的交易。 */
  installment_plan_id?: string | null
  /** 週期性收支(§2.12.2 Phase 1.5):所属规则的 id。null = 非规则生成的交易。 */
  recurring_rule_id?: string | null
  /** 该笔 occurrence 是否被单独编辑过(之后规则批次更新/视窗续产生会跳过它)。 */
  recurring_occurrence_overridden?: boolean
  /** 退款反查(§2.12.3):这笔支出收到过哪些退款,空数组 = 没有退款。 */
  refunds?: ReadTxRefundSummary[]
  /** 拆帳(§2.4):true = 这笔交易拆到多个分类,category_id/category_name 为 null,明细在 splits。 */
  has_splits?: boolean
  /** 拆帳(§2.4):has_splits=true 时的分类明细,空数组 = 没有拆帳。 */
  splits?: ReadTxSplit[]
  /** 借還款追蹤(§2.5 Phase 3):这笔交易关联的欠款 id。null = 普通交易。
   *  debt_counterparty_name/debt_direction 是反查这笔欠款拿到的展示字段
   *  (体验补强,对齐 category_id+category_name 的既有惯例)。 */
  debt_id?: string | null
  debt_counterparty_name?: string | null
  debt_direction?: DebtDirection | null
  /** 專案(Phase 13,docs/PH13_PROJECT_SD.md):这笔交易关联的專案 id。
   *  null = 沒掛專案。project_name 是反查这个專案拿到的展示字段(体验
   *  补强,对齐 debt_counterparty_name 的既有惯例)。 */
  project_id?: string | null
  project_name?: string | null
  /** 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易走
   *  哪幾條回饋規則的 id 列表,空数组 = 没有勾选任何规则。 */
  reward_rule_ids?: string[]
  /** 信用卡紅利回饋自動入帳(§2.9.5.4 補強):有值 = 这笔交易是逐笔结算
   *  规则自动产生的回饋 income,反查它对应的原始消费交易 id;null = 普通
   *  交易,或 period_end/manual 這種不對應單一原始交易的回饋。 */
  reward_source_tx_id?: string | null
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
}

/** §2.12.3:交易明细页"已退款金额 + 退款交易清单"用的单笔退款摘要。 */
export type ReadTxRefundSummary = {
  id: string
  amount: number
  happened_at: string
}

/** 拆帳(§2.4):一笔交易拆到某个分类下的明细行。 */
export type ReadTxSplit = {
  category_id: string | null
  category_name: string | null
  amount: number
  note: string | null
  sort_order: number
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
  /** 主帳戶(合併帳單,§2.9 Phase 4):子卡的 sync_id 指向主卡,null=沒有掛靠。 */
  parent_account_id?: string | null
  /** 账户隐藏(issue #240):true = 已隐藏 —— 记账/转账选择器不再出现,主列表
   *  退场收进「已隐藏」分区;但仍计入净资产/资产/收支(D1,服务端不做统计过滤)。
   *  缺省 false(旧接口未提供该字段时视为未隐藏)。 */
  hidden?: boolean
  /** 自動扣繳(§2.9,2026-08-04 改版):開關。只在 account_group,或沒有
   *  掛靠任何群組的獨立信用卡上生效。 */
  auto_pay_enabled?: boolean
  /** 自動扣繳來源帳戶 sync_id,null = 未設定。 */
  auto_pay_from_account_id?: string | null
  /** 「可繳款」提醒(§2.9 補強,2026-08-02):只在 billing-root(account_group
   *  或沒有掛靠任何群組的獨立信用卡)且真的欠款(> 0)時才有值。 */
  billing_due_date?: string | null
  billing_remaining_due?: number | null
  /** 帳戶頭像(2026-08-02 補強):`AttachmentFile.id`,null = 沒有自訂頭像。 */
  avatar_cloud_file_id?: string | null
  avatar_cloud_sha256?: string | null
  /** SwipeSmart 卡片對照(Phase 14):對應 SwipeSmart 的 CardId,null = 尚未
   *  對照。 */
  swipesmart_card_id?: string | null
  /** 納入總餘額(Phase 18):false = 這個帳戶的餘額不列入淨資產/資產構成
   *  總額,但帳戶本身、個別餘額顯示、底部分組列表都不受影響(跟 hidden
   *  是兩個獨立維度)。缺省 true(旧接口未提供该字段时视为納入)。 */
  include_in_total?: boolean
  /** 帳戶清單拖曳排序(2026-09-05):null = 舊資料/舊版 App 沒有這個值,
   *  清單顯示時排到最後。 */
  sort_order: number | null
  /** 股票持股(2026-09-28):投資理財帳戶費用設定(camelCase key,同 sync wire
   *  `investmentSettings`)。null = 沒設定過,沿用市場預設值。 */
  investment_settings?: InvestmentSettings | null
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
  color?: string | null
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
  /** account_group 底下有跨幣別子帳戶、且至少一筆因缺匯率被剔除、沒有計入
   *  balance/income_total/expense_total 時為 true。缺省 false(舊接口未提供
   *  該字段時視為完整)。 */
  balance_fx_incomplete?: boolean | null
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

export type ScheduledJobConfig = {
  job_key: string
  interval_seconds: number
  enabled: boolean
  next_run_at?: string | null
  last_run_at?: string | null
  last_run_status?: string | null
  last_run_message?: string | null
}

export type ScheduledJobRunNowResult = {
  job_key: string
  status: string
  message?: string | null
  summary: Record<string, unknown>
  last_run_at?: string | null
  next_run_at?: string | null
}

export type AppVersionConfig = {
  latest_version: string | null
  /** 最低可同步版本(docs/LICENSE_KEYS.md):App 版本低於這個值、或舊版 App
   *  完全沒回報版本,同步一律被 server 以 426 擋下。null = 不限制。 */
  min_sync_version: string | null
  nas_webdav_url: string | null
  nas_webdav_user: string | null
  nas_webdav_password_set: boolean
  last_checked_at: string | null
  last_check_error: string | null
}

// ---------------------------------------------------------------------------
// 授權金鑰(docs/LICENSE_KEYS.md)
// ---------------------------------------------------------------------------

/** GET /license/status、POST /license/activate 的回應。沒有授權時也查得到。 */
export type LicenseStatus = {
  user_id: string
  email: string
  is_admin: boolean
  /** 目前是否可以使用(管理者永遠 true)。 */
  licensed: boolean
  /** 管理者免金鑰。 */
  exempt: boolean
  /** 名下金鑰最晚到期日(可能已過期;從沒啟用過任何金鑰 = null)。 */
  expires_at: string | null
  server_time: string
  /** App 本地授權的離線寬限天數,web 端只做顯示用。 */
  offline_grace_days: number
}

export type AdminLicenseKeyStatus = 'unused' | 'active' | 'expired' | 'revoked'

export type AdminLicenseKeyStatusFilter = 'all' | AdminLicenseKeyStatus

export type AdminLicenseKey = {
  id: string
  key: string
  duration_days: number
  note: string | null
  status: AdminLicenseKeyStatus
  created_at: string
  created_by_email: string | null
  redeemed_by_user_id: string | null
  redeemed_by_email: string | null
  redeemed_at: string | null
  expires_at: string | null
  revoked_at: string | null
}

export type AdminLicenseKeyList = {
  items: AdminLicenseKey[]
  total: number
}

/** 管理者系統公告(server `routers/admin_broadcasts.py`)。 */
export type AdminBroadcast = {
  id: string
  title: string
  body: string | null
  created_at: string
  created_by_email: string | null
  recipient_count: number
  /** 撤回後通知已刪除,固定回 0 */
  read_count: number
  retracted_at: string | null
}

export type AdminBroadcastList = {
  items: AdminBroadcast[]
  total: number
}

export type AdminBroadcastCreatePayload = {
  title: string
  body?: string
}

export type AdminLicenseKeyCreatePayload = {
  /** 1..100 */
  count: number
  /** 1..3650,預設 365。 */
  duration_days?: number
  note?: string
}

export type AppVersionCheckNowResult = {
  status: string
  latest_version: string | null
  last_checked_at: string | null
  last_check_error: string | null
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
  /** 'adjustment' 只由 balance-adjustment 语意化端点在服务端产生,前端不应
   *  直接建这个 tx_type 的交易(见 `balanceAdjustment` in write.ts)。 */
  tx_type: 'expense' | 'income' | 'transfer' | 'adjustment'
  amount: number
  happened_at: string
  /** 交易级多币种(0018):原币种;不传 = 账本本位币(不产生字段)。 */
  currency_code?: string | null
  /** 折账本本位币的金额快照(前端按 server 汇率算好传入)。 */
  native_amount?: number | null
  /** 跨幣別轉帳(2026-08):僅 tx_type=transfer 且轉出/轉入帳戶幣別不同時
   *  需要傳——轉入帳戶自身幣別的金額(前端按匯率算好傳入,或使用者手動
   *  覆蓋)。不傳/同幣別轉帳 = server 端 COALESCE(to_amount, amount) 回退。 */
  to_amount?: number | null
  /** 手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction):
   *  base_amount=使用者輸入的原始金額(server 端據此 + fee/discount 重新
   *  算出權威的 amount,見 write/_shared.py::_normalize_fee_discount_amount);
   *  fee_amount/discount_amount=額外調整金額;fee_label/discount_label=
   *  自訂名稱。create 不傳任一個 = 不使用這個功能(向下相容);update 不傳
   *  = 不改,傳 null = 清空該分量。 */
  base_amount?: number | null
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
  /** 延後入帳(§2.10 Phase 5):create 不传/null = 未设置;update 不传 = 不改,
   *  传 ISO 字符串 = 设置,传 null = 清空。 */
  deferred_posting_at?: string | null
  /** 對帳模式(§2.10,2026-08-09 改版):不传 = 不改,ISO 字符串 = 標記已在
   *  對帳模式勾選確認,null = 取消確認。統計/檢查表都透過既有通用的
   *  `updateTransaction` 帶這個欄位完成,不是專門的 write endpoint。 */
  reconciled_at?: string | null
  note?: string | null
  /** 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。
   *  create 不傳 = 未設定;update 不傳 = 不改,傳字串 = 設置,傳 null = 清空。 */
  merchant?: string | null
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
  /** 退款(§2.6):这笔交易是对 refund_of_id 那笔支出的退款。null = 普通交易/不改。 */
  refund_of_id?: string | null
  /**
   * Phase 1.5(§2.12.2):建交易当下顺便把它设成週期性收支的起点。只在
   * create 有效(update 会被忽略)。
   */
  recurring?: RecurringInlineCreatePayload | null
  /**
   * 拆帳(§2.4):不传/undefined = 维持现行单一 category(向下相容);传入
   * 至少 2 笔、tx_type 只能是 expense/income、金额加总须等于 amount ——
   * server 端校验,详见 src/routers/write/_shared.py `_validate_tx_splits`。
   * update 传空数组 [] = 清空 splits,交易变回单一 category。
   */
  splits?: TxSplitPayload[] | null
  /**
   * 借還款追蹤(§2.5 Phase 3):这笔交易是对 debt_id 那笔欠款的一次还款/
   * 收款。必须指向该账本下已存在的欠款,允许多笔部分还款。null = 普通
   * 交易/不改。
   */
  debt_id?: string | null
  /**
   * 專案(Phase 13,docs/PH13_PROJECT_SD.md):这笔交易手動指定屬於哪個
   * project_id,只支援 expense/income。必须指向该帳本下已存在的專案。
   * null = 不掛專案/不改。
   */
  project_id?: string | null
  /**
   * 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者手動勾選這筆交易走
   * 哪幾條回饋規則(可複選),每個 id 必須是 `account_id` 這張信用卡自己
   * 名下的規則。undefined = 不改(update);[] = 清空;null 等同 []。
   */
  reward_rule_ids?: string[] | null
}

/** 拆帳(§2.4):挂在 `TxPayload.splits` 上的单个分类明细。 */
export type TxSplitPayload = {
  category_id: string
  category_name?: string | null
  amount: number
  note?: string | null
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
  /** 主帳戶(合併帳單,§2.9 Phase 4):子卡挂靠的主卡 id。update 传空字串
   *  解除掛靠;不传 = 不改。 */
  parent_account_id?: string | null
  /** 账户隐藏(issue #240)。create 缺省 false;update 不传 = 不改(服务端
   *  merge 缺键保留,见 snapshot_mutator._apply_account_optional_fields)。 */
  hidden?: boolean | null
  /** 自動扣繳(§2.9,2026-08-04 改版):開關,不传 = 不改。 */
  auto_pay_enabled?: boolean | null
  /** 自動扣繳來源帳戶 sync_id;update 传空字串解除;不传 = 不改。 */
  auto_pay_from_account_id?: string | null
  /** 帳戶頭像(2026-08-02 補強):update 传空字串移除頭像;不传 = 不改。 */
  avatar_cloud_file_id?: string | null
  avatar_cloud_sha256?: string | null
  /** SwipeSmart 卡片對照(Phase 14):update 传空字串解除對照;不传 = 不改。 */
  swipesmart_card_id?: string | null
  /** 納入總餘額(Phase 18):create 缺省 true;update 不传 = 不改。 */
  include_in_total?: boolean | null
  /** 股票持股(2026-09-28):整包取代;null = 清空;不传 = 不改。 */
  investment_settings?: InvestmentSettings | null
  /** 帳戶清單拖曳排序(2026-09-05):不传 = 不改。批次拖曳排序走
   *  `reorderAccounts`,單筆 create/update 一般不用這個欄位。 */
  sort_order?: number | null
}

export type AccountBillingMember = {
  account_id: string
  account_name: string
  cycle_spend: number
  /** 這個成員自己在目前瀏覽週期(period_cycle_start~period_cycle_end)的
   *  新增花費,不是整組合併數字(§2.9.6 Phase 7,2026-08-07)。 */
  period_new_spend: number
  /** 這個成員自己的終身跑動餘額(下限 0),不是整組合併數字。 */
  remaining_due: number
}

export type AccountBillingSummary = {
  account_id: string
  account_name: string
  billing_day: number
  payment_due_day: number
  member_account_ids: string[]
  members: AccountBillingMember[]
  cycle_start: string
  cycle_end: string
  due_date: string
  statement_amount: number
  paid_amount: number
  remaining_due: number
  open_cycle_start: string
  open_cycle_end: string
  open_cycle_due_date: string
  open_cycle_spend: number
  credit_limit: number | null
  available_credit: number | null
  period_cycle_start: string
  period_cycle_end: string
  period_due_date: string
  period_new_spend: number
  period_carryover_due: number
  period_total_due: number
  period_paid_in_cycle: number
  period_remaining_due: number
  period_has_older: boolean
  period_has_newer: boolean
  period_installment_active_count: number
  period_installment_paid_periods: number | null
  period_installment_periods: number | null
}

/** 「選擇區間」清單(2026-09-06)單一選項:`offset` 跟 `AccountBillingSummary`
 *  的 `cycle_offset` 同語意(0=最近一次已結束的週期,+1=目前還在累積中的
 *  那期)。 */
export type AccountBillingPeriodOption = {
  offset: number
  cycle_start: string
  cycle_end: string
}

export type AccountBillingPeriodList = {
  periods: AccountBillingPeriodOption[]
}

export type AccountInterestFreeSuggestion = {
  account_id: string
  as_of: string
  billing_day: number
  payment_due_day: number
  current_cycle_start: string
  current_cycle_end: string
  current_cycle_due_date: string
  next_cycle_start: string
  next_cycle_end: string
  next_cycle_due_date: string
  recommended_purchase_after: string
  min_interest_free_days: number
  max_interest_free_days: number
}

/** 分類智慧推薦(Phase 21):category_sync_id 清單,由高到低排序。 */
export type ReadCategorySuggestions = {
  category_ids: string[]
}

/** 依分類帶入常用帳戶(Phase 21):account_sync_id 清單,由高到低排序。 */
export type ReadAccountSuggestions = {
  account_ids: string[]
}

export type CardPaymentPayload = {
  amount: number
  from_account_id: string
  happened_at?: string | null
  note?: string | null
}

// ────────── 餘額調整 (Balance Adjustment，MOZE_FEATURE_GAP_SD.md §2.10 Phase 5）──────────

/** §2.10 Phase 5:語意化端點,服务端算 `diff = target_balance - 当前计算余额`
 *  后建一笔 `tx_type=adjustment` 的交易。`happened_at` 不传 = 用当下时间。 */
export type BalanceAdjustmentPayload = {
  target_balance: number
  happened_at?: string | null
  note?: string | null
}

// ────────── 比較報表 (Comparison Report，MOZE_FEATURE_GAP_SD.md §2.10 Phase 5）──────────

export type ComparisonReportMetric = {
  current: number
  previous: number
  diff: number
  /** `previous` 为 0 时为 null(避免除以零),前端展示为「—」。 */
  diff_pct: number | null
}

export type ComparisonCategoryBreakdownItem = {
  category_id: string | null
  category_name: string
  category_kind: 'income' | 'expense'
  current: number
  previous: number
  diff: number
}

export type ComparisonReport = {
  scope: 'month' | 'year'
  offset: number
  current_period_start: string
  current_period_end: string
  previous_period_start: string
  previous_period_end: string
  income: ComparisonReportMetric
  expense: ComparisonReportMetric
  balance: ComparisonReportMetric
  /** 按 |diff| 降序排列。 */
  category_breakdown: ComparisonCategoryBreakdownItem[]
}

// ────────── 比較矩陣 (MOZE comparison report,`/read/workspace/comparison-matrix`)──────────

export type ComparisonMatrixDimension =
  | 'record_type'
  | 'expense_category'
  | 'income_category'
  | 'expense_subcategory'
  | 'income_subcategory'
  | 'project'
  | 'account_group'

/** `__none__` = 「(無)」/「未分組」;`record_type` 維度固定 expense/income/balance。 */
export const COMPARISON_MATRIX_NONE_KEY = '__none__'

export type ComparisonMatrixColumn = {
  key: string
  label: string
  /** 子類別維度時是一級分類名稱 */
  parent_label: string | null
}

export type ComparisonMatrixRow = {
  /** 週期標籤 YYYY-MM(依帳本月起始日) */
  month: string
  start: string
  end: string
  values: Record<string, number>
  /** record_type 維度 = 結餘,其餘 = 各欄加總 */
  total: number
  /** 相對上一列的增減百分比;上一列為 0 或第一列時為 null */
  mom_pct: number | null
}

export type ComparisonMatrix = {
  dimension: ComparisonMatrixDimension
  kind: 'expense' | 'income'
  columns: ComparisonMatrixColumn[]
  rows: ComparisonMatrixRow[]
  column_totals: Record<string, number>
  column_averages: Record<string, number>
  grand_total: number
}

export type ComparisonMatrixCellTx = {
  sync_id: string
  ledger_id: string
  happened_at: string
  tx_type: string
  /** 落在該格的金額(拆帳只算對應明細;退款為負值) */
  amount: number
  category_name: string | null
  account_name: string | null
  note: string | null
  merchant: string | null
  is_refund: boolean
}

export type ComparisonMatrixCell = {
  month: string
  column_key: string
  total: number
  items: ComparisonMatrixCellTx[]
}

// ────────── 信用卡紅利回饋 (Card Rewards，MOZE_FEATURE_GAP_SD.md §2.9.5 Phase 4.5）──────────

export type CardRewardRateType = 'percentage' | 'fixed_amount'
/** Phase 8 #4(2026-08 使用者反饋):新增 'keep'(保留小數,不取整)。
 *  `rounding` = 單筆取整方式;`total_rounding`(見下方)= 總額取整方式,
 *  兩者共用同一組合法值。 */
export type CardRewardRounding = 'floor' | 'round' | 'ceil' | 'keep'
export type CardRewardCalcBasis = 'transaction_date' | 'settlement_date'
export type CardRewardInterval = 'billing_cycle' | 'calendar_month'
export type CardRewardRuleStatus = 'ok' | 'no_billing_schedule' | 'expired'
/** 自動入帳(§2.9.5.4):manual = 純顯示不自動化;immediate_after_tx/
 *  after_posting_date 逐筆結算;period_end 整期結束後一次結算。 */
export type CardRewardSettlementType =
  | 'immediate_after_tx'
  | 'after_posting_date'
  | 'period_end'
  | 'manual'

export type ReadCardRewardRule = {
  id: string
  account_id: string
  label: string
  category_ids?: string[] | null
  rate_type: CardRewardRateType
  rate_value: number
  rounding: CardRewardRounding
  total_rounding: CardRewardRounding
  calc_basis: CardRewardCalcBasis
  interval: CardRewardInterval
  min_spend_threshold?: number | null
  min_tx_amount?: number | null
  cap_amount?: number | null
  cap_shared_key?: string | null
  starts_at?: string | null
  ends_at?: string | null
  settlement_type: CardRewardSettlementType
  settlement_days?: number | null
  /** 週期結束後一次結算的回饋入帳日(Phase 8 #15):0 = 當月,1 = 次月...
   *  跟 `settlement_day_of_month` 成對使用,任一為 null 時維持現況行為
   *  (期間結束當天入帳)。 */
  settlement_month_offset?: number | null
  settlement_day_of_month?: number | null
  reward_account_id?: string | null
  note?: string | null
  enabled: boolean
  /** Phase 8 #16:規則已有交易掛著或已有自動入帳紀錄時 true——計算相關
   *  欄位不能再改,只能調整 label/note/enabled/starts_at/ends_at。 */
  locked?: boolean
  last_change_id: number
}

export type CardRewardRuleCreatePayload = {
  label: string
  category_ids?: string[] | null
  rate_type?: CardRewardRateType
  rate_value: number
  rounding?: CardRewardRounding
  total_rounding?: CardRewardRounding
  calc_basis?: CardRewardCalcBasis
  interval?: CardRewardInterval
  min_spend_threshold?: number | null
  min_tx_amount?: number | null
  cap_amount?: number | null
  cap_shared_key?: string | null
  starts_at?: string | null
  ends_at?: string | null
  settlement_type?: CardRewardSettlementType
  settlement_days?: number | null
  settlement_month_offset?: number | null
  settlement_day_of_month?: number | null
  reward_account_id?: string | null
  note?: string | null
  enabled?: boolean
}

/** `account_id` 建立後不可改(綁定的信用卡帳戶不能改)。 */
export type CardRewardRuleUpdatePayload = {
  label?: string
  category_ids?: string[] | null
  rate_type?: CardRewardRateType
  rate_value?: number
  rounding?: CardRewardRounding
  total_rounding?: CardRewardRounding
  calc_basis?: CardRewardCalcBasis
  interval?: CardRewardInterval
  min_spend_threshold?: number | null
  min_tx_amount?: number | null
  cap_amount?: number | null
  cap_shared_key?: string | null
  starts_at?: string | null
  ends_at?: string | null
  settlement_type?: CardRewardSettlementType
  settlement_days?: number | null
  settlement_month_offset?: number | null
  settlement_day_of_month?: number | null
  reward_account_id?: string | null
  note?: string | null
  enabled?: boolean
}

/** §2.9.5.4 補強(2026-08-03):手動入帳,`settlement_type == 'manual'`
 *  的規則用這個端點自己按一下記一筆,`amount`/`reward_account_id` 每次
 *  臨時指定。 */
export type CardRewardManualPayoutPayload = {
  amount: number
  reward_account_id: string
  happened_at?: string | null
  note?: string | null
}

/** 單一規則在單一計算期間的彙總結果(Phase 22,2026-08 使用者反饋)。
 *  `remaining_spend_room` 是反推出來的「還可以刷多少消費金額」,只有
 *  `rate_type === 'percentage'` 的規則才有值,固定金額類規則固定 `null`
 *  (跟消費金額無關,是筆數概念)。 */
export type ReadCardRewardPeriodUsage = {
  period_start: string
  period_end: string
  status: CardRewardRuleStatus
  qualifying_spend: number
  threshold_met: boolean
  raw_reward: number
  capped_reward: number
  remaining_reward_room?: number | null
  remaining_spend_room?: number | null
}

/** 單條規則的計算結果。Phase 22(自然月規則橫跨帳單週期只顯示得到其中一
 *  個月)之前這裡是單一期間的扁平物件,`calendar_month` 規則橫跨帳單週期
 *  時一條規則對應到 1~2 個自然月,改成 `periods` 陣列(`billing_cycle`
 *  規則固定只有 1 筆,前端一致處理,不需要分支)。 */
export type ReadCardRewardRuleUsage = {
  rule_id: string
  label: string
  cap_amount?: number | null
  cap_shared_key?: string | null
  periods: ReadCardRewardPeriodUsage[]
}

export type ReadCardRewards = {
  account_id: string
  as_of: string
  items: ReadCardRewardRuleUsage[]
  total_reward: number
}

/** §2.9.5.3 交易明細彈窗:單一規則命中哪些交易 + 各自回饋金額。
 *  `payout_tx_id`(2026-08 對帳明細可編輯回饋金額):這筆消費實際結算入帳
 *  的回饋交易 sync_id,只有逐筆結算且已到期入帳的項目才有值——有值時才能
 *  編輯這一行的回饋金額(PATCH 這個 id 對應的交易,不是 `tx_id`)。 */
export type ReadCardRewardQualifyingTx = {
  tx_id: string
  happened_at: string
  amount: number
  note?: string | null
  category_name?: string | null
  reward_amount: number
  settlement_date?: string | null
  payout_tx_id?: string | null
}

/** 單一規則在單一計算期間的交易明細(Phase 22)。`remaining_reward_room`/
 *  `remaining_spend_room` 是這條規則所屬共用上限群組在**這個期間**的剩餘
 *  額度/剩餘可刷金額,見 `ReadCardRewardPeriodUsage` docstring。 */
export type ReadCardRewardPeriodTransactions = {
  period_start: string
  period_end: string
  status: CardRewardRuleStatus
  qualifying_spend: number
  raw_reward: number
  capped_reward: number
  remaining_reward_room?: number | null
  remaining_spend_room?: number | null
  items: ReadCardRewardQualifyingTx[]
}

/** `GET .../card-reward-rules/{rule_id}/transactions` 返回。Phase 22 之前
 *  是單一期間的扁平物件,`calendar_month` 規則橫跨帳單週期時改成 `periods`
 *  陣列(`billing_cycle` 規則固定只有 1 筆),見 `ReadCardRewardRuleUsage`
 *  docstring 同一個 breaking change 原因。 */
export type ReadCardRewardRuleTransactions = {
  rule_id: string
  label: string
  cap_amount?: number | null
  cap_shared_key?: string | null
  periods: ReadCardRewardPeriodTransactions[]
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
  color?: string | null
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

/** 淨資產卡片用:跨帳本按幣種彙總未結清、未排除的欠款/應收(見
 *  `src/routers/read/workspace.py` `_workspace_debt_currency_totals`
 *  docstring)。receivable(對方欠我)算資產、payable(我欠對方)算負債,
 *  跟 accounts 的 include_in_total 語意對齐。 */
export type WorkspaceDebtCurrencyTotal = {
  currency: string
  receivable_total: number
  payable_total: number
}

// ────────── 通知中心(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0）──────────
// user-global，非 sync 实体，走普通 REST（GET /notifications 等），跟其余
// ledger-scoped read/write 契约不是一回事。

export type NotificationItem = {
  id: number
  /** 'reminder' | 'budget_alert' | 'card_due' | 'system'，服务端未做枚举校验。 */
  category: string
  title: string
  body: string | null
  payload: Record<string, unknown> | null
  read_at: string | null
  created_at: string
}

export type NotificationListResponse = {
  total: number
  /** 始终是当前用户全部未读数，不受 limit/offset/category/unread_only 影响。 */
  unread_count: number
  items: NotificationItem[]
}

// ────────── 週期性收支 (Recurring Rules，MOZE_FEATURE_GAP_SD.md §2.2 /
// Phase 1.5 修正版 §2.12.2）──────────

export type RecurringFrequency = 'daily' | 'weekly' | 'monthly' | 'yearly'

/** 股票定期定額(2026-09-28,docs/STOCK_HOLDINGS_SD.md §9):'general' = 既有
 * 語意;'stock_dca' 必定搭配 tx_type='transfer'。 */
export type RecurringRuleKind = 'general' | 'stock_dca'

/**
 * 简单 frequency+interval 表达不了的进阶规则(§2.12.2)。`weekly_days.days`
 * 用 **Python `datetime.weekday()` 惯例(Monday=0…Sunday=6)**,跟 JS
 * `Date.getDay()`(Sunday=0)不同 —— 组装这个字段时务必换算,不要直接塞
 * JS 原生 weekday。
 */
export type RecurringAdvancedRule =
  | { type: 'weekly_days'; days: number[] }
  | { type: 'monthly_day'; day: number }

export type ReadRecurringRule = {
  id: string
  tx_type: 'expense' | 'income' | 'transfer' | string
  amount: number
  note?: string | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  /** Phase 24(問題 B 第二層):商家/專案/標籤,可被「連同未來週期」批次更新。 */
  merchant?: string | null
  project_id?: string | null
  project_name?: string | null
  tag_ids?: string[]
  frequency: RecurringFrequency
  interval: number
  next_run_at: string
  end_at?: string | null
  enabled: boolean
  /** 视窗续产生进度(Phase 1.5)。 */
  generated_until_at?: string | null
  advanced_rule_json?: RecurringAdvancedRule | null
  /** 手續費/折扣/信用卡回饋(2026-08 使用者回饋):規則固定屬性,每一期
   *  自動產生的 occurrence 都會繼承,同 ReadTransaction 同名欄位語意。 */
  base_amount?: number | null
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
  reward_rule_ids?: string[]
  /** 股票定期定額:kind 預設 'general',market/symbol/security_name/
   *  stock_fee_rate/stock_fee_min 只有 kind='stock_dca' 才有值。 */
  kind?: RecurringRuleKind
  market?: string | null
  symbol?: string | null
  security_name?: string | null
  stock_fee_rate?: number | null
  stock_fee_min?: number | null
  /** 2026-09-29:transfer/stock_dca 規則真正的下一期(next_run_at 第一期後
   *  就不再變)。一般收支規則/已停用/沒有下一期時為 null,退回顯示 next_run_at。 */
  upcoming_run_at?: string | null
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type RecurringRuleCreatePayload = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  frequency: RecurringFrequency
  interval: number
  next_run_at: string
  end_at?: string | null
  enabled?: boolean
  advanced_rule_json?: RecurringAdvancedRule | null
  base_amount?: number | null
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
  reward_rule_ids?: string[] | null
  /** 股票定期定額:kind='stock_dca' 時 tx_type 必須是 'transfer'，market/
   *  symbol 必填，to_account_id 必須是投資理財帳戶。stock_fee_rate/stock_fee_min
   *  皆為 null = 沿用投資理財帳戶的預設費率。 */
  kind?: RecurringRuleKind
  market?: string | null
  symbol?: string | null
  security_name?: string | null
  stock_fee_rate?: number | null
  stock_fee_min?: number | null
}

export type RecurringRuleUpdatePayload = {
  tx_type?: 'expense' | 'income' | 'transfer'
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  frequency?: RecurringFrequency
  interval?: number
  next_run_at?: string
  end_at?: string | null
  enabled?: boolean
  advanced_rule_json?: RecurringAdvancedRule | null
  base_amount?: number | null
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
  reward_rule_ids?: string[] | null
  /** 股票定期定額手續費覆寫(kind/market/symbol/security_name 建立後鎖定，
   *  不在這裡暴露)。null = 清除，改回沿用帳戶預設。 */
  stock_fee_rate?: number | null
  stock_fee_min?: number | null
}

/** §2.12.2:挂在 `TxPayload.recurring` 上,建交易当下顺便设週期起点。 */
export type RecurringInlineCreatePayload = {
  frequency: RecurringFrequency
  interval: number
  end_at?: string | null
  advanced_rule_json?: RecurringAdvancedRule | null
}

/** §2.12.2:单独编辑某一期已生成的 occurrence 交易(会被标记 overridden)。 */
export type RecurringOccurrenceUpdatePayload = {
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  happened_at?: string
}

/** §2.12.2:修改連同未來 —— 更新规则本身字段 + 该期以后所有未 overridden
 * 的已生成交易(不动 happened_at)。Phase 24 補齊 from_account_id/
 * to_account_id(entity 本來就有,原本這支 payload 漏轉發)+ 新增的
 * merchant/project_id/tag_ids(見 docs/PH17_USER_FEEDBACK_2026-08_SD.md
 * Phase 24 問題 B)。 */
export type RecurringUpdateFromPayload = {
  tx_type?: 'expense' | 'income' | 'transfer'
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  merchant?: string | null
  project_id?: string | null
  tag_ids?: string[]
  frequency?: RecurringFrequency
  interval?: number
  advanced_rule_json?: RecurringAdvancedRule | null
  base_amount?: number | null
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
  reward_rule_ids?: string[] | null
}

// ────────── 分期付款 (Installment Plans，MOZE_FEATURE_GAP_SD.md §2.3 /
// Phase 1.5 修正版 §2.12.1）──────────

export type InstallmentPlanStatus = 'active' | 'settled' | 'terminated'
export type InstallmentRepaymentMethod = 'equal_installment' | 'equal_principal' | 'fixed_interest'
export type InstallmentInterestPeriod = 'monthly' | 'daily'
export type InstallmentRemainderPosition = 'first' | 'last'

export type ReadInstallmentPlan = {
  id: string
  total_amount: number
  periods: number
  period_amount: number
  first_period_at: string
  next_period_at: string
  paid_periods: number
  account_id?: string | null
  category_id?: string | null
  note?: string | null
  status: InstallmentPlanStatus
  repayment_method: InstallmentRepaymentMethod
  interest_period: InstallmentInterestPeriod
  interest_rate: number
  round_amounts: boolean
  remainder_position: InstallmentRemainderPosition
  grace_period_months: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type InstallmentPlanCreatePayload = {
  total_amount: number
  periods: number
  first_period_at: string
  account_id?: string | null
  category_id?: string | null
  note?: string | null
  repayment_method?: InstallmentRepaymentMethod
  interest_period?: InstallmentInterestPeriod
  interest_rate?: number
  round_amounts?: boolean
  remainder_position?: InstallmentRemainderPosition
  grace_period_months?: number
  /** 帳單分期沖銷(§2.9,2026-08-02):把信用卡已欠下的帳單轉成分期時,額外
   *  生成一筆 income 沖銷交易清空原本的應繳金額,避免同一筆錢被算兩次
   *  (原消費 + 分期各期新 expense)。要求 account_id 已設定。 */
  offset_existing_balance?: boolean
}

/** 只支持提前结清(status='settled')/改备注，攤還参数/期数/金额不可改
 * （要调利率/提前还本走下面的差异化端点，不是这个 PATCH）。 */
export type InstallmentPlanUpdatePayload = {
  note?: string | null
  status?: InstallmentPlanStatus
}

/** §2.12.1:分期单期明细(每期本金/利息/合计)。 */
export type ReadInstallmentPeriod = {
  id: string
  plan_id: string
  period_no: number
  due_at: string
  principal_amount: number
  interest_amount: number
  total_amount: number
  status: 'pending' | 'generated' | 'overridden' | 'refunded'
  tx_id?: string | null
  // 单期退款(§2.6/§2.12.1):status === 'refunded' 时才有值。
  refund_tx_id?: string | null
  refund_amount?: number | null
  refunded_at?: string | null
}

/** §2.6/§2.12.1:单期退款 —— 按该期的 tx_id 定位,建一笔 income 退款交易。 */
export type InstallmentPeriodRefundPayload = {
  tx_id: string
  amount?: number | null
  note?: string | null
  happened_at?: string | null
}

/** §2.12.1:编辑单期(金额/日期/备注)，`overridden=true`。 */
export type InstallmentPeriodUpdatePayload = {
  amount?: number
  due_at?: string
  note?: string | null
}

/** §2.12.1:调利率(可选换攤還方式)，连同未来重算未 overridden 的期数。 */
export type InstallmentRebalancePayload = {
  interest_rate: number
  repayment_method?: InstallmentRepaymentMethod
}

/** §2.12.1:部分还本，重算未 overridden 的未来期数。 */
export type InstallmentEarlyRepayPayload = {
  payment_amount: number
  account_id?: string | null
  happened_at?: string
}

/** §2.12.1:提前结清，生成一笔结清交易并删除未到期的未来期。 */
export type InstallmentPayoffPayload = {
  account_id?: string | null
  happened_at?: string
}

// ────────── 借還款追蹤 (Debts，MOZE_FEATURE_GAP_SD.md §2.5 Phase 3）──────────

export type DebtDirection = 'payable' | 'receivable'
/** 'closed' = 手動結案(體驗補強),優先權蓋過其它三種從 remaining_amount
 *  算出來的狀態 —— 不代表已還清全額。 */
export type DebtStatus = 'open' | 'partial' | 'settled' | 'closed'

/** 某笔欠款收到的一笔还款/收款摘要,给詳情頁「還款記錄」清單用。 */
export type ReadDebtRepayment = {
  id: string
  amount: number
  happened_at: string
}

/**
 * `remaining_amount`/`status` 不落库,是 server 读路径从反查交易即时算出
 * 的 derived 字段(见 server `ReadDebtProjection` docstring)。
 */
export type ReadDebt = {
  id: string
  direction: DebtDirection
  counterparty_name: string
  principal_amount: number
  remaining_amount: number
  status: DebtStatus
  due_at?: string | null
  note?: string | null
  repayments: ReadDebtRepayment[]
  /** 結案(體驗補強):非空 = 已手動標記結束。 */
  closed_at?: string | null
  category_id?: string | null
  /** 起點交易反查:mobile 建立欠款時同時寫入的起點交易 sync_id,web 建立
   *  的欠款沒有這個概念,維持 null。 */
  origin_tx_id?: string | null
  /** 起點交易摘要(欠款紀錄本身),跟 `repayments` 平行——`origin_tx_id`
   *  指向的交易若還找得到就帶上金額/日期,null = 沒有起點交易或已被刪除。 */
  origin_transaction?: ReadDebtRepayment | null
  /** 排除計入總額(§5.4 對象管理):只影響淨資產/總額統計,不影響這個清單
   *  本身或通知的可見性。 */
  excluded_from_total: boolean
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type DebtCreatePayload = {
  direction: DebtDirection
  counterparty_name: string
  principal_amount: number
  due_at?: string | null
  note?: string | null
  category_id?: string | null
  excluded_from_total?: boolean
}

/** `principal_amount`/`direction` 建立后不可改,只暴露
 *  counterparty_name/due_at/note/closed_at/category_id/excluded_from_total。
 *  closed_at 傳 ISO 時間 = 結案,傳 `null` = 重新開啟,不傳這個 key = 不變。
 *  `counterparty_name` 改名只影響這一筆——要連動同名的其他記錄,改呼叫
 *  `renameDebtCounterparty`,不要指望這個欄位會 cascade。 */
export type DebtUpdatePayload = {
  counterparty_name?: string
  due_at?: string | null
  note?: string | null
  closed_at?: string | null
  category_id?: string | null
  excluded_from_total?: boolean
}

export type DebtRenameCounterpartyPayload = {
  old_counterparty_name: string
  new_counterparty_name: string
  base_change_id: number
}

export type DebtRenameCounterpartyResponse = {
  ledger_id: string
  base_change_id: number
  new_change_id: number
  server_timestamp: string
  renamed_debt_ids: string[]
}

// ────────── 專案 (Projects，docs/PH13_PROJECT_SD.md Phase 13）──────────

export type ProjectPeriodType = 'fixed' | 'monthly' | 'yearly'
/** 對齊 Moze 總覽頁狀態指標:✅ 正常 / ⚠️ 接近上限(≥80%) / 🚨 超支(≥100%)。
 *  沒設預算(budget_amount=null)一律 'ok'。 */
export type ProjectStatus = 'ok' | 'warning' | 'over'

/**
 * `spent`/`remaining`/`progress_pct`/`status` 不落库,是 server 读路径從
 * `project_sync_id` 反查交易、依 period_type 算出當期起訖窗口即時彙總出的
 * derived 字段(见 server `ReadProjectProjection` docstring)。
 */
/** 每日預算模式(docs/2026-09-06-project-category-budget-period-switch-design.md
 *  §2.1/§6.2):'fixed'=總預算/期間天數;'proportional'=剩餘預算/剩餘天數。 */
export type DailyBudgetMode = 'fixed' | 'proportional'

export type ReadProject = {
  id: string
  name: string
  icon?: string | null
  budget_amount?: number | null
  /** budget_amount 併入本期收入（income_included_in_budget 時）+ 結轉
   *  （carryover_enabled 時）後的有效預算，跟 ReadProjectBreakdown.effective_budget
   *  同一套演算法——列表卡片的進度條/超支判斷應該用這個欄位，不是 budget_amount。 */
  effective_budget?: number | null
  carried_over?: number | null
  period_type: ProjectPeriodType
  period_start?: string | null
  period_end?: string | null
  carryover_enabled: boolean
  visible_on_home: boolean
  enabled: boolean
  sort_order: number
  income_included_in_budget: boolean
  daily_budget_enabled: boolean
  daily_budget_mode?: DailyBudgetMode | null
  reminder_threshold_percent?: number | null
  spent: number
  remaining?: number | null
  progress_pct?: number | null
  status: ProjectStatus
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type ProjectCreatePayload = {
  name: string
  icon?: string | null
  budget_amount?: number | null
  period_type?: ProjectPeriodType
  /** `period_type === 'fixed'` 時必填(server 校驗)。 */
  period_start?: string | null
  period_end?: string | null
  carryover_enabled?: boolean
  visible_on_home?: boolean
  enabled?: boolean
  sort_order?: number
  income_included_in_budget?: boolean
  daily_budget_enabled?: boolean
  daily_budget_mode?: DailyBudgetMode | null
  reminder_threshold_percent?: number | null
}

/** key 不出現 = 不變;`budget_amount` 顯式傳 null = 清空預算(改回純追蹤
 *  用途)。DELETE 若專案底下已有交易掛著,server 會自動改成軟刪除
 *  (`enabled=false`)而非物理刪除,前端不需要另外呼叫這個 update。 */
export type ProjectUpdatePayload = {
  name?: string
  icon?: string | null
  budget_amount?: number | null
  period_type?: ProjectPeriodType
  period_start?: string | null
  period_end?: string | null
  carryover_enabled?: boolean
  visible_on_home?: boolean
  enabled?: boolean
  sort_order?: number
  income_included_in_budget?: boolean
  daily_budget_enabled?: boolean
  daily_budget_mode?: DailyBudgetMode | null
  reminder_threshold_percent?: number | null
}

// ────────── 專案分類子預算 (Project Category Budgets，docs/2026-09-06-
// project-category-budget-period-switch-design.md §2.2/§7.2）──────────

export type ProjectCategoryBudgetMode = 'fixed' | 'percentage'

export type ReadProjectCategoryBudget = {
  id: string
  project_id: string
  category_id: string
  mode: ProjectCategoryBudgetMode
  fixed_amount?: number | null
  percentage?: number | null
  carryover_enabled: boolean
  sort_order: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type ProjectCategoryBudgetCreatePayload = {
  category_id: string
  mode?: ProjectCategoryBudgetMode
  fixed_amount?: number | null
  percentage?: number | null
  carryover_enabled?: boolean
  sort_order?: number
}

/** key 不出現 = 不變;`category_id` 建立後不可改(同 card_reward_rule 的
 *  account_id 慣例,要換分類走刪除重建)。 */
export type ProjectCategoryBudgetUpdatePayload = {
  mode?: ProjectCategoryBudgetMode
  fixed_amount?: number | null
  percentage?: number | null
  carryover_enabled?: boolean
  sort_order?: number
}

// ────────── 專案詳情頁：期間切換 + 分類花費拆解（docs/2026-09-06-project-
// category-budget-period-switch-design.md §4）──────────

export type ReadProjectBreakdownCategory = {
  category_id: string
  /** 該分類這期的支出毛額，不含退款/收入折抵。 */
  spent: number
  /** 該分類這期的收入/退款毛額；`spent - income_spent` 才是要顯示給使用者
   *  的淨額，比照 mobile app 的 expense_total/income_total 雙軌設計。 */
  income_spent: number
  count: number
  has_budget: boolean
  budget_mode?: ProjectCategoryBudgetMode | null
  budget_target?: number | null
  progress_pct?: number | null
}

/** `spent`/`remaining`/`progress_pct`/`status`/分類拆解都是 server 針對指定
 *  `period_offset` 即時彙總出的 derived 值,不落库。`effective_budget` 是
 *  `budget_amount` 併入本期收入(`income_included_in_budget`)後的有效預算,
 *  跟 `ReadProject.remaining`(目前仍固定用 budget_amount)刻意不同步。 */
export type ReadProjectBreakdown = {
  project_id: string
  period_type: ProjectPeriodType
  period_start: string
  period_end: string
  period_offset: number
  period_has_newer: boolean
  period_has_older: boolean
  expense_total: number
  expense_count: number
  income_total: number
  income_count: number
  budget_amount?: number | null
  effective_budget?: number | null
  /** carryover_enabled 時，上一期名目 budget_amount 扣掉上一期實際支出後的
   *  結轉金額（可能為負，代表上期超支）；已併入 effective_budget，未啟用/
   *  fixed 週期/無預算時為 null。 */
  carried_over?: number | null
  spent: number
  remaining?: number | null
  progress_pct?: number | null
  status: ProjectStatus
  allocated_total: number
  unallocated_amount?: number | null
  allocated_categories: ReadProjectBreakdownCategory[]
  unallocated_categories: ReadProjectBreakdownCategory[]
  unset_categories: ReadProjectBreakdownCategory[]
}

// ────────── 對帳模式 (Statement Mode，MOZE_FEATURE_GAP_SD.md §2.10 Phase 5，
// 2026-08-09 改版為 Moze 式逐筆核對清單，取代 v1 的「單筆餘額比對記錄」）──────────

/** 「這期帳單」清單裡的一行交易。`account_id`/`account_name` 是這筆交易實際
 *  掛的那張卡（account_group 場景下用來分組顯示），不一定等於查詢用的
 *  `account_id`。確認/取消確認、延後入帳都透過既有通用的 `updateTransaction`
 *  完成（帶 `reconciled_at`/`deferred_posting_at`），不是專門的 write
 *  endpoint。 */
export type StatementTransaction = {
  id: string
  account_id: string
  account_name: string | null
  tx_type: string
  amount: number
  category_name: string | null
  note: string | null
  happened_at: string
  deferred_posting_at?: string | null
  reconciled_at?: string | null
  /** Phase 6:分類是系統紅利回饋專屬分類時為 true,前端顯示「回饋」標籤。 */
  is_reward?: boolean
  /** 2026-08 使用者反饋:同一個回饋方案(rule)在這期帳單內的所有回饋交易
   *  合併成一行,`amount` 是加總。非 null 時這一行是合併列,點擊可展開看
   *  `reward_rule_id` 對應方案的原始消費明細(呼叫既有的
   *  `fetchCardRewardRuleTransactions`)。手動記的回饋分類交易(查不到對應
   *  規則)維持 null,行為等同合併前的單筆顯示。 */
  reward_rule_id?: string | null
  reward_rule_label?: string | null
  /** 這一行實際包含的原始交易 sync_id——確認/延後入帳要對清單裡每一筆各自
   *  呼叫 `updateTransaction`。非回饋合併列時只含自己這一筆(等同 `[id]`)。 */
  member_tx_ids?: string[]
  fee_amount?: number | null
  fee_label?: string | null
  discount_amount?: number | null
  discount_label?: string | null
}

export type StatementAccountTotal = {
  account_id: string
  account_name: string | null
  count: number
  total: number
}

/** 選單「取消全部選取」:清空指定週期裡所有已確認狀態,不影響其它週期。 */
export type StatementClearConfirmationsPayload = {
  cycle_offset: number
}

export type AccountStatement = {
  account_id: string
  account_name: string | null
  cycle_start: string
  cycle_end: string
  due_date: string
  cycle_offset: number
  has_older: boolean
  has_newer: boolean
  statement_count: number
  statement_total: number
  confirmed_count: number
  confirmed_total: number
  accounts: StatementAccountTotal[]
  transactions: StatementTransaction[]
}

// ────────── 交易範本 (Templates，MOZE_FEATURE_GAP_SD.md §2.7 Phase 3）──────────

export type ReadTxTemplate = {
  id: string
  name: string
  tx_type: 'expense' | 'income' | 'transfer' | string
  amount: number
  note?: string | null
  category_id?: string | null
  category_name?: string | null
  account_id?: string | null
  account_name?: string | null
  from_account_id?: string | null
  from_account_name?: string | null
  to_account_id?: string | null
  to_account_name?: string | null
  tag_ids: string[]
  sort_order: number
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
}

export type TxTemplateCreatePayload = {
  name: string
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  tag_ids?: string[] | null
  sort_order?: number | null
}

export type TxTemplateUpdatePayload = {
  name?: string
  tx_type?: 'expense' | 'income' | 'transfer'
  amount?: number
  note?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  tag_ids?: string[] | null
  sort_order?: number | null
}

/** 把範本內容套成一筆新交易;`amount`/`note` 可選擇性覆蓋範本预设值。 */
export type TxTemplateApplyPayload = {
  happened_at: string
  amount?: number | null
  note?: string | null
}


/** 投資理財帳戶費用設定(股票持股 2026-09-28)。key 對齊 App
 *  lib/models/investment_settings.dart 與 server snapshot_mutator.normalize_investment_settings;
 *  比率一律是小數(0.001425 = 0.1425%),全部選填,缺 = 沿用市場預設。 */
export type InvestmentSettings = {
  market?: string
  feeRate?: number
  feeDiscount?: number
  feeMin?: number
  /** 台股是「普通股」的稅率;ETF / 債券 ETF 看下面兩個(依代號判斷)。 */
  sellTaxRate?: number
  etfSellTaxRate?: number
  bondEtfSellTaxRate?: number
  /** 未實現損益扣預估賣出手續費/交易稅;缺 = 開。 */
  pnlAfterSellCosts?: boolean
  dividendFeeFixed?: number
  dividendFeeRate?: number
  dividendWithholdingRate?: number
  nhiSupplementRate?: number
  nhiThreshold?: number
  reinvestDividends?: boolean
  settlementAccountId?: string
}
