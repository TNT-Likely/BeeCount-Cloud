import { authedDelete, authedGet, authedPatch, authedPost, resolveApiUrl } from './http'
import type {
  AdminBackupArtifact,
  AdminBackupCreateResponse,
  AdminBackupRestoreResponse,
  AdminDeviceList,
  AdminHealth,
  AdminLogList,
  AdminOverview,
  AdminSyncErrors,
  UserAdmin,
  UserAdminCreatePayload,
  UserAdminList
} from './types'

function mapUserAdminAvatar(user: UserAdmin): UserAdmin {
  return {
    ...user,
    avatar_url: resolveApiUrl(user.avatar_url)
  }
}

export async function fetchAdminDevices(
  token: string,
  options?: {
    q?: string
    user_id?: string
    online_only?: boolean
    active_within_days?: number
    limit?: number
    offset?: number
  }
): Promise<AdminDeviceList> {
  const query = new URLSearchParams()
  if (options?.q) query.set('q', options.q)
  if (options?.user_id) query.set('user_id', options.user_id)
  if (typeof options?.online_only === 'boolean') query.set('online_only', options.online_only ? 'true' : 'false')
  if (typeof options?.active_within_days === 'number') query.set('active_within_days', `${options.active_within_days}`)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.offset === 'number') query.set('offset', `${options.offset}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<AdminDeviceList>(`/admin/devices${suffix}`, token)
}

export async function fetchAdminUsers(
  token: string,
  options?: { q?: string; status?: 'enabled' | 'disabled' | 'all'; limit?: number; offset?: number }
): Promise<UserAdminList> {
  const query = new URLSearchParams()
  if (options?.q) query.set('q', options.q)
  if (options?.status) query.set('status', options.status)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.offset === 'number') query.set('offset', `${options.offset}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await authedGet<UserAdminList>(`/admin/users${suffix}`, token)
  return {
    ...response,
    items: response.items.map(mapUserAdminAvatar)
  }
}

export async function patchAdminUser(
  token: string,
  userId: string,
  payload: { is_admin?: boolean; is_enabled?: boolean }
): Promise<UserAdmin> {
  const user = await authedPatch<UserAdmin>(`/admin/users/${encodeURIComponent(userId)}`, token, payload)
  return mapUserAdminAvatar(user)
}

export async function createAdminUser(token: string, payload: UserAdminCreatePayload): Promise<UserAdmin> {
  const user = await authedPost<UserAdmin>('/admin/users', token, payload)
  return mapUserAdminAvatar(user)
}

export async function deleteAdminUser(token: string, userId: string): Promise<UserAdmin> {
  const user = await authedDelete<UserAdmin>(`/admin/users/${encodeURIComponent(userId)}`, token, {})
  return mapUserAdminAvatar(user)
}

export async function fetchAdminOverview(token: string): Promise<AdminOverview> {
  return authedGet<AdminOverview>('/admin/overview', token)
}

export async function fetchAdminHealth(token: string): Promise<AdminHealth> {
  return authedGet<AdminHealth>('/admin/health', token)
}

export async function fetchAdminSyncErrors(token: string): Promise<AdminSyncErrors> {
  return authedGet<AdminSyncErrors>('/admin/sync/errors', token)
}

export async function fetchAdminLogs(
  token: string,
  options?: {
    level?: string
    q?: string
    source?: string
    limit?: number
    since_seq?: number
  }
): Promise<AdminLogList> {
  const query = new URLSearchParams()
  if (options?.level) query.set('level', options.level)
  if (options?.q) query.set('q', options.q)
  if (options?.source) query.set('source', options.source)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  if (typeof options?.since_seq === 'number') query.set('since_seq', `${options.since_seq}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<AdminLogList>(`/admin/logs${suffix}`, token)
}

export async function listAdminBackupArtifacts(
  token: string,
  options?: { ledger_id?: string; kind?: 'db' | 'snapshot'; limit?: number }
): Promise<AdminBackupArtifact[]> {
  const query = new URLSearchParams()
  if (options?.ledger_id) query.set('ledger_id', options.ledger_id)
  if (options?.kind) query.set('kind', options.kind)
  if (typeof options?.limit === 'number') query.set('limit', `${options.limit}`)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<AdminBackupArtifact[]>(`/admin/backups/artifacts${suffix}`, token)
}

export async function createAdminBackup(
  token: string,
  payload: { ledger_id: string; note?: string | null }
): Promise<AdminBackupCreateResponse> {
  return authedPost<AdminBackupCreateResponse>('/admin/backups/create', token, payload)
}

export async function restoreAdminBackup(
  token: string,
  payload: { snapshot_id: string; device_id?: string | null }
): Promise<AdminBackupRestoreResponse> {
  return authedPost<AdminBackupRestoreResponse>('/admin/backups/restore', token, payload)
}
