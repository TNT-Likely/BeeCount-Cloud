import { authedGet, resolveApiUrl } from './http'
import type {
  AnalyticsMetric,
  AnalyticsScope,
  ReadAccount,
  ReadCategory,
  ReadLedger,
  ReadLedgerDetail,
  ReadTag,
  ReadTransaction,
  WorkspaceAccount,
  WorkspaceAnalytics,
  WorkspaceCategory,
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

export async function fetchWorkspaceTransactions(
  token: string,
  options?: {
    ledgerId?: string
    userId?: string
    q?: string
    txType?: string
    accountName?: string
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

export async function fetchWorkspaceAnalytics(
  token: string,
  options?: {
    scope?: AnalyticsScope
    metric?: AnalyticsMetric
    period?: string
    ledgerId?: string
    userId?: string
  }
): Promise<WorkspaceAnalytics> {
  const query = new URLSearchParams()
  if (options?.scope) query.set('scope', options.scope)
  if (options?.metric) query.set('metric', options.metric)
  if (options?.period) query.set('period', options.period)
  if (options?.ledgerId) query.set('ledger_id', options.ledgerId)
  if (options?.userId) query.set('user_id', options.userId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<WorkspaceAnalytics>(`/read/workspace/analytics${suffix}`, token)
}
