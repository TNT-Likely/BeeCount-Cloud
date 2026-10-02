import { authedDelete, authedGet, authedPatch, authedPost, authedPut, resolveApiUrl } from './http'
import type {
  AdminBackupArtifact,
  AdminBackupCreateResponse,
  AdminBackupRestoreResponse,
  AdminBroadcast,
  AdminBroadcastCreatePayload,
  AdminBroadcastList,
  AdminDeviceList,
  AdminHealth,
  AdminLicenseKey,
  AdminLicenseKeyCreatePayload,
  AdminLicenseKeyList,
  AdminLicenseKeyStatusFilter,
  AdminLogList,
  AdminOverview,
  AdminSyncErrors,
  AppVersionCheckNowResult,
  AppVersionConfig,
  DataCleanupRecord,
  DataCleanupResult,
  DataCleanupScanReport,
  ScheduledJobConfig,
  SecurityDataSourceConfig,
  SecurityDataSourceProvider,
  SecurityDataSourceTestResult,
  ScheduledJobRunNowResult,
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
  payload: {
    email?: string
    is_enabled?: boolean
  }
): Promise<UserAdmin> {
  const user = await authedPatch<UserAdmin>(`/admin/users/${encodeURIComponent(userId)}`, token, payload)
  return mapUserAdminAvatar(user)
}

export async function changeAdminUserPassword(
  token: string,
  userId: string,
  payload: { admin_password: string; new_password: string }
): Promise<UserAdmin> {
  const user = await authedPost<UserAdmin>(
    `/admin/users/${encodeURIComponent(userId)}/password`,
    token,
    payload
  )
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

export async function fetchDataCleanupScan(
  token: string,
): Promise<DataCleanupScanReport> {
  return authedGet<DataCleanupScanReport>('/admin/data-cleanup/scan', token)
}

export async function executeDataCleanup(
  token: string,
  records: DataCleanupRecord[],
): Promise<DataCleanupResult> {
  return authedPost<DataCleanupResult>('/admin/data-cleanup/clean', token, {
    records,
  })
}

export async function fetchScheduledJobs(token: string): Promise<ScheduledJobConfig[]> {
  return authedGet<ScheduledJobConfig[]>('/admin/scheduled-jobs', token)
}

export async function updateScheduledJob(
  token: string,
  jobKey: string,
  payload: { interval_seconds?: number; enabled?: boolean },
): Promise<ScheduledJobConfig> {
  return authedPatch<ScheduledJobConfig>(
    `/admin/scheduled-jobs/${encodeURIComponent(jobKey)}`,
    token,
    payload,
  )
}

export async function runScheduledJobNow(
  token: string,
  jobKey: string,
): Promise<ScheduledJobRunNowResult> {
  return authedPost<ScheduledJobRunNowResult>(
    `/admin/scheduled-jobs/${encodeURIComponent(jobKey)}/run-now`,
    token,
    {},
  )
}

export async function fetchAppVersionConfig(token: string): Promise<AppVersionConfig> {
  return authedGet<AppVersionConfig>('/admin/app-version-config', token)
}

export async function updateAppVersionConfig(
  token: string,
  payload: {
    latest_version?: string
    /** 不帶 = 不變更;空字串 = 清除限制;格式須為 x.y.z(server 會 400)。 */
    min_sync_version?: string
    nas_webdav_url?: string
    nas_webdav_user?: string
    nas_webdav_password?: string
  },
): Promise<AppVersionConfig> {
  return authedPut<AppVersionConfig>('/admin/app-version-config', token, payload)
}

export async function checkAppVersionNow(token: string): Promise<AppVersionCheckNowResult> {
  return authedPost<AppVersionCheckNowResult>('/admin/app-version-config/check-now', token, {})
}

// 股票資料來源(Phase 3,docs/STOCK_HOLDINGS_SD.md §11)。admin + ops scope。
export async function fetchSecurityDataSource(token: string): Promise<SecurityDataSourceConfig> {
  return authedGet<SecurityDataSourceConfig>('/admin/security-data-source', token)
}

export async function updateSecurityDataSource(
  token: string,
  payload: {
    provider?: SecurityDataSourceProvider
    /** 留空/不帶 = 不變更。 */
    api_key?: string
    clear_api_key?: boolean
  },
): Promise<SecurityDataSourceConfig> {
  return authedPut<SecurityDataSourceConfig>('/admin/security-data-source', token, payload)
}

export async function testSecurityDataSource(token: string): Promise<SecurityDataSourceTestResult> {
  return authedPost<SecurityDataSourceTestResult>('/admin/security-data-source/test', token, {})
}

// 授權金鑰管理(docs/LICENSE_KEYS.md)。server 端疊 require_admin_user +
// SCOPE_OPS_WRITE,跟 app-version-config 同一組權限。

export async function fetchAdminLicenseKeys(
  token: string,
  options?: { status?: AdminLicenseKeyStatusFilter; q?: string; limit?: number },
): Promise<AdminLicenseKeyList> {
  const params = new URLSearchParams()
  if (options?.status) params.set('status', options.status)
  if (options?.q?.trim()) params.set('q', options.q.trim())
  if (options?.limit) params.set('limit', String(options.limit))
  const query = params.toString()
  return authedGet<AdminLicenseKeyList>(`/admin/licenses${query ? `?${query}` : ''}`, token)
}

/** 批次產生金鑰,回傳的只有這次新建的那幾把。 */
export async function createAdminLicenseKeys(
  token: string,
  payload: AdminLicenseKeyCreatePayload,
): Promise<AdminLicenseKeyList> {
  return authedPost<AdminLicenseKeyList>('/admin/licenses', token, payload)
}

export async function revokeAdminLicenseKey(token: string, id: string): Promise<AdminLicenseKey> {
  return authedPost<AdminLicenseKey>(`/admin/licenses/${encodeURIComponent(id)}/revoke`, token, {})
}

/** 只能刪「從未被啟用」的金鑰;已啟用的 server 回 409,要改用撤銷。 */
export async function deleteAdminLicenseKey(token: string, id: string): Promise<{ deleted: boolean }> {
  return authedDelete<{ deleted: boolean }>(`/admin/licenses/${encodeURIComponent(id)}`, token)
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

/** 系統公告歷史(新到舊)。 */
export async function fetchAdminBroadcasts(token: string): Promise<AdminBroadcastList> {
  return authedGet<AdminBroadcastList>('/admin/broadcasts', token)
}

/** 目前會收到公告的人數(所有啟用中的使用者,含管理者自己)。 */
export async function fetchAdminBroadcastRecipientCount(token: string): Promise<{ count: number }> {
  return authedGet<{ count: number }>('/admin/broadcasts/recipient-count', token)
}

export async function createAdminBroadcast(
  token: string,
  payload: AdminBroadcastCreatePayload,
): Promise<AdminBroadcast> {
  return authedPost<AdminBroadcast>('/admin/broadcasts', token, payload)
}

/** 撤回:刪掉所有使用者名下那一筆通知(已跳出的系統通知收不回來)。 */
export async function retractAdminBroadcast(token: string, id: string): Promise<AdminBroadcast> {
  return authedPost<AdminBroadcast>(`/admin/broadcasts/${encodeURIComponent(id)}/retract`, token, {})
}
