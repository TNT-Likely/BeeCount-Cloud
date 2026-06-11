import { API_BASE, authedGet, resolveApiUrl } from './http'
import { extractApiError } from './errors'
import type {
  AnalyticsMetric,
  AnalyticsScope,
  NetWorthHistory,
  ReadAccount,
  ReadBudget,
  ReadCategory,
  ReadLedger,
  ReadLedgerDetail,
  ReadTag,
  ReadTransaction,
  SharedResourcesBundle,
  WorkspaceAccount,
  WorkspaceAnalytics,
  WorkspaceCategory,
  WorkspaceLedgerCounts,
  WorkspaceTag,
  WorkspaceTransaction,
  WorkspaceTransactionPage
} from './types'

export async function fetchReadLedgers(token: string): Promise<ReadLedger[]> {
  return authedGet<ReadLedger[]>('/read/ledgers', token)
}

export async function fetchReadLedgerDetail(token: string, ledgerId: string): Promise<ReadLedgerDetail> {
  return authedGet<ReadLedgerDetail>(`/read/ledgers/${encodeURIComponent(ledgerId)}`, token)
}

export async function fetchReadTransactions(
  token: string,
  ledgerId: string,
  options?: { limit?: number; q?: string; txType?: string }
): Promise<ReadTransaction[]> {
  const query = new URLSearchParams()
  if (options?.limit) query.set('limit', `${options.limit}`)
  if (options?.q) query.set('q', options.q)
  if (options?.txType) query.set('tx_type', options.txType)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const rows = await authedGet<ReadTransaction[]>(
    `/read/ledgers/${encodeURIComponent(ledgerId)}/transactions${suffix}`,
    token
  )
  return rows.map((row) => ({
    ...row,
    created_by_avatar_url: resolveApiUrl(row.created_by_avatar_url)
  }))
}

export async function fetchReadSummary(token: string, ledgerId: string): Promise<any> {
  return authedGet<any>(`/read/summary?ledger_id=${encodeURIComponent(ledgerId)}`, token)
}

export async function fetchReadAccounts(token: string, ledgerId: string): Promise<ReadAccount[]> {
  return authedGet<ReadAccount[]>(`/read/ledgers/${encodeURIComponent(ledgerId)}/accounts`, token)
}

export async function fetchReadCategories(token: string, ledgerId: string): Promise<ReadCategory[]> {
  return authedGet<ReadCategory[]>(`/read/ledgers/${encodeURIComponent(ledgerId)}/categories`, token)
}

export async function fetchReadTags(token: string, ledgerId: string): Promise<ReadTag[]> {
  return authedGet<ReadTag[]>(`/read/ledgers/${encodeURIComponent(ledgerId)}/tags`, token)
}

export async function fetchReadBudgets(token: string, ledgerId: string): Promise<ReadBudget[]> {
  return authedGet<ReadBudget[]>(`/read/ledgers/${encodeURIComponent(ledgerId)}/budgets`, token)
}

export type ReadBudgetUsageItem = {
  budget_id: string
  used: number
}

export type ReadBudgetUsage = {
  items: ReadBudgetUsageItem[]
}

/**
 * 后端 SQL 聚合每个 budget 当周期已用金额。分类预算的 used 含子分类支出。
 * 取代旧的"循环 fetch transactions + 前端 reduce"路径(N 次 HTTP + 1000 条
 * limit 隐患)。详见后端 list_budgets_usage。
 */
export async function fetchReadBudgetUsage(
  token: string,
  ledgerId: string,
): Promise<ReadBudgetUsage> {
  return authedGet<ReadBudgetUsage>(
    `/read/ledgers/${encodeURIComponent(ledgerId)}/budgets/usage`,
    token,
  )
}

/**
 * 单账本统计 — server 直接返回 transaction_count + attachment_count + budget_count 等。
 * 用于:删除账本确认弹窗(让用户清楚知道删了什么)、mobile 深度同步差异检测。
 */
export type ReadLedgerStats = {
  transaction_count: number
  transaction_total: number
  attachment_count: number
  attachment_total: number
  category_attachment_total: number
  budget_count: number
  budget_total: number
  account_count: number
  account_total: number
  category_count: number
  category_total: number
  tag_count: number
  tag_total: number
}

export async function fetchReadLedgerStats(
  token: string,
  ledgerId: string,
): Promise<ReadLedgerStats> {
  return authedGet<ReadLedgerStats>(
    `/read/ledgers/${encodeURIComponent(ledgerId)}/stats`,
    token,
  )
}

export async function fetchWorkspaceTransactions(
  token: string,
  options?: {
    ledgerId?: string
    userId?: string
    q?: string
    txType?: string
    accountName?: string
    txSyncId?: string
    tagSyncId?: string
    categorySyncId?: string
    accountSyncId?: string
    /** 金额下限(含),按 abs 比较 */
    amountMin?: number
    /** 金额上限(含) */
    amountMax?: number
    /** happened_at >= dateFrom (ISO 8601) */
    dateFrom?: string
    /** happened_at < dateTo (ISO 8601, 独占)。前端通常传"次日 00:00"包含整天。 */
    dateTo?: string
    limit?: number
    offset?: number
  }
): Promise<WorkspaceTransactionPage> {
  const query = new URLSearchParams()
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  if (options?.q) query.set('q', options.q)
  if (options?.txType) query.set('tx_type', options.txType)
  if (options?.accountName) query.set('account_name', options.accountName)
  if (options?.txSyncId) query.set('tx_sync_id', options.txSyncId)
  if (options?.tagSyncId) query.set('tag_sync_id', options.tagSyncId)
  if (options?.categorySyncId) query.set('category_sync_id', options.categorySyncId)
  if (options?.accountSyncId) query.set('account_sync_id', options.accountSyncId)
  if (typeof options?.amountMin === 'number') query.set('amount_min', `${options.amountMin}`)
  if (typeof options?.amountMax === 'number') query.set('amount_max', `${options.amountMax}`)
  if (options?.dateFrom) query.set('date_from', options.dateFrom)
  if (options?.dateTo) query.set('date_to', options.dateTo)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.offset === 'number') query.set('offset', `${options.offset}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await authedGet<WorkspaceTransactionPage | WorkspaceTransaction[]>(
    `/read/workspace/transactions${suffix}`,
    token
  )

  // Backward compatibility: older backend returned array directly.
  if (Array.isArray(response)) {
    const normalizedItems = response.map((item) => ({
      ...item,
      created_by_avatar_url: resolveApiUrl(item.created_by_avatar_url)
    }))
    return {
      items: normalizedItems,
      total: normalizedItems.length,
      limit: options?.limit ?? normalizedItems.length,
      offset: options?.offset ?? 0
    }
  }

  return {
    ...response,
    items: (response.items || []).map((item) => ({
      ...item,
      created_by_avatar_url: resolveApiUrl(item.created_by_avatar_url)
    }))
  }
}

export async function fetchWorkspaceAccounts(
  token: string,
  options?: { ledgerId?: string; userId?: string; q?: string; limit?: number; offset?: number }
): Promise<WorkspaceAccount[]> {
  const query = new URLSearchParams()
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  if (options?.q) query.set('q', options.q)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.offset === 'number') query.set('offset', `${options.offset}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<WorkspaceAccount[]>(`/read/workspace/accounts${suffix}`, token)
}

export async function fetchWorkspaceCategories(
  token: string,
  options?: { ledgerId?: string; userId?: string; q?: string; limit?: number; offset?: number }
): Promise<WorkspaceCategory[]> {
  const query = new URLSearchParams()
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  if (options?.q) query.set('q', options.q)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.offset === 'number') query.set('offset', `${options.offset}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<WorkspaceCategory[]>(`/read/workspace/categories${suffix}`, token)
}

export async function fetchWorkspaceTags(
  token: string,
  options?: { ledgerId?: string; userId?: string; q?: string; limit?: number; offset?: number }
): Promise<WorkspaceTag[]> {
  const query = new URLSearchParams()
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  if (options?.q) query.set('q', options.q)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.offset === 'number') query.set('offset', `${options.offset}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<WorkspaceTag[]>(`/read/workspace/tags${suffix}`, token)
}

/**
 * 拉共享账本的 Owner user-global 资源快照(categories/accounts/tags)。
 *
 * 用法:Editor 进入共享账本后,前端 lazy 调一次落到独立的
 * `sharedLedgerResources` state(Map<ledgerId, SharedResourcesBundle>);
 * picker / tx tile icon lookup / tag color 在共享账本场景下走这套数据,
 * 不污染用户自己的 user-global state。
 *
 * server endpoint: GET /api/v1/ledgers/{ledgerId}/shared-resources
 * 详见 .docs/shared-ledger/01-product-design.md §7 + 04-server-details.md §3.3
 */
export async function fetchSharedResources(
  token: string,
  ledgerId: string
): Promise<SharedResourcesBundle> {
  return authedGet<SharedResourcesBundle>(
    `/ledgers/${encodeURIComponent(ledgerId)}/shared-resources`,
    token
  )
}

export async function fetchWorkspaceLedgerCounts(
  token: string,
  options?: { ledgerId?: string; userId?: string }
): Promise<WorkspaceLedgerCounts> {
  const query = new URLSearchParams()
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<WorkspaceLedgerCounts>(
    `/read/workspace/ledger-counts${suffix}`,
    token
  )
}

export async function fetchWorkspaceAnalytics(
  token: string,
  options?: {
    scope?: AnalyticsScope
    metric?: AnalyticsMetric
    period?: string
    ledgerId?: string
    userId?: string
    tzOffsetMinutes?: number
    naturalMonth?: boolean
  }
): Promise<WorkspaceAnalytics> {
  const query = new URLSearchParams()
  if (options?.scope) query.set('scope', options.scope)
  if (options?.metric) query.set('metric', options.metric)
  if (options?.period) query.set('period', options.period)
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  if (typeof options?.tzOffsetMinutes === 'number') query.set('tz_offset_minutes', `${options.tzOffsetMinutes}`)
  if (options?.naturalMonth) query.set('natural_month', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<WorkspaceAnalytics>(`/read/workspace/analytics${suffix}`, token)
}

export async function fetchNetWorthHistory(
  token: string,
  options?: { scope?: AnalyticsScope; ledgerId?: string; userId?: string; tzOffsetMinutes?: number }
): Promise<NetWorthHistory> {
  const query = new URLSearchParams()
  if (options?.scope) query.set('scope', options.scope)
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  if (typeof options?.tzOffsetMinutes === 'number') query.set('tz_offset_minutes', `${options.tzOffsetMinutes}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<NetWorthHistory>(`/read/workspace/net-worth-history${suffix}`, token)
}

// ============================================================================
// CSV 导出 — 见 .docs/web-csv-export-design.md
// ============================================================================

export type DownloadCsvOptions = {
  ledgerId: string
  /** date_from(ISO 8601);happenedAt >= dateFrom */
  dateFrom?: string
  /** date_to(ISO 8601,独占,前端通常传"次日 00:00") */
  dateTo?: string
  txType?: string
  q?: string
  accountName?: string
  accountSyncId?: string
  categorySyncId?: string
  tagSyncId?: string
  amountMin?: number
  amountMax?: number
  /** 客户端本地时区偏移,Time 列按这个折算。默认 -getTimezoneOffset() */
  tzOffsetMinutes?: number
  /** 表头 / Type 列语言。zh-CN / zh-TW / en;省略走 server 默认(en) */
  lang?: string
  /** 当 server 没给 Content-Disposition filename 时的 fallback 文件名 */
  fallbackFilename?: string
  /** 批量选中场景:按 sync_id 集合导出。传入则忽略其它 filter 参数。
   *  上限跟 batch delete 一致(200)。 */
  txIds?: string[]
}

/**
 * 下载 workspace tx CSV 导出。
 *
 * 实现细节:
 * - 用 fetch + Authorization header 调 server 的 /transactions.csv 端点
 *   (不能直接 <a download=> 跳 URL,token 进 access log 不安全)
 * - 流式接收 → blob → URL.createObjectURL → 程序化触发 <a> click 下载
 * - 优先用 server 给的 Content-Disposition filename(支持 RFC 5987 中文)
 *
 * 抛 ApiError(失败时,跟其他 read endpoint 一致)。
 */
export async function downloadWorkspaceTransactionsCsv(
  token: string,
  options: DownloadCsvOptions,
): Promise<void> {
  const query = new URLSearchParams()
  query.set('ledger_id', options.ledgerId)
  // 批量选中导出:走 sync_id IN(...) 直接限定;server 端会忽略其它 filter,
  // 这里也尽量不发,避免 URL 噪声 + 防误解读。
  if (options.txIds && options.txIds.length > 0) {
    for (const id of options.txIds) query.append('tx_ids', id)
  } else {
    if (options.dateFrom) query.set('date_from', options.dateFrom)
    if (options.dateTo) query.set('date_to', options.dateTo)
    if (options.txType) query.set('tx_type', options.txType)
    if (options.q) query.set('q', options.q)
    if (options.accountName) query.set('account_name', options.accountName)
    if (options.accountSyncId) query.set('account_sync_id', options.accountSyncId)
    if (options.categorySyncId) query.set('category_sync_id', options.categorySyncId)
    if (options.tagSyncId) query.set('tag_sync_id', options.tagSyncId)
    if (typeof options.amountMin === 'number')
      query.set('amount_min', `${options.amountMin}`)
    if (typeof options.amountMax === 'number')
      query.set('amount_max', `${options.amountMax}`)
  }
  const tzOffset =
    typeof options.tzOffsetMinutes === 'number'
      ? options.tzOffsetMinutes
      : typeof window !== 'undefined'
        ? -new Date().getTimezoneOffset()
        : 0
  query.set('tz_offset_minutes', `${tzOffset}`)
  if (options.lang) query.set('lang', options.lang)

  const url = `${API_BASE}/read/workspace/transactions.csv?${query.toString()}`
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    throw await extractApiError(response)
  }

  const disposition = response.headers.get('Content-Disposition') || ''
  const filename =
    parseFilenameFromDisposition(disposition) ||
    options.fallbackFilename ||
    'beecount-export.csv'

  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } finally {
    // 即时回收 — 浏览器下载已经从 blob 拿走数据,不需要 URL 长期持有
    URL.revokeObjectURL(objectUrl)
  }
}

/** 解析 Content-Disposition,优先 RFC 5987 filename*=UTF-8'',fallback 普通 filename。 */
function parseFilenameFromDisposition(value: string): string | null {
  // RFC 5987 优先(中文文件名场景)
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(value)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      // ignore,落 plain
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(value)
  return plain ? plain[1].trim() : null
}
