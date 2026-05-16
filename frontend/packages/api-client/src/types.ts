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
  /** 月显示装饰风格:'icons' | 'particles' | 'honeycomb' | … */
  header_decoration_style?: string
  /** 紧凑金额显示(万/亿) */
  compact_amount?: boolean
  /** 交易行是否显示时间 */
  show_transaction_time?: boolean
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
}

export type LedgerMetaPayload = {
  ledger_name?: string | null
  currency?: string | null
}

export type ReadLedger = {
  ledger_id: string
  ledger_name: string
  currency: string
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
  last_change_id: number
  ledger_id?: string | null
  ledger_name?: string | null
  created_by_user_id?: string | null
  created_by_email?: string | null
  created_by_display_name?: string | null
  created_by_avatar_url?: string | null
  created_by_avatar_version?: number | null
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

export type AdminIntegrityIssueSample = {
  sync_id: string
  label: string
  extra?: Record<string, unknown> | null
}

export type AdminIntegrityIssue = {
  issue_type: string
  ledger_id: string
  ledger_name: string
  owner_email: string | null
  count: number
  samples: AdminIntegrityIssueSample[]
}

export type AdminIntegrityScan = {
  scanned_at: string
  ledgers_total: number
  issues_total: number
  issues: AdminIntegrityIssue[]
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
