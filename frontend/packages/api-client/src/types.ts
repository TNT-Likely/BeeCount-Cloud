export type LoginResponse = {
  access_token: string
  refresh_token: string
  expires_in: number
  device_id: string
  scopes: string[]
  user: { id: string; email: string; is_admin?: boolean }
}

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

export type AccountPayload = {
  name: string
  account_type?: string | null
  currency?: string | null
  initial_balance?: number | null
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
