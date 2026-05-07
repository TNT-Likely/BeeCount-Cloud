/**
 * Admin backup API —— 跟 server `src/routers/admin_backup.py` 对应。
 *
 * Remotes / Schedules / Runs CRUD + run-now + test + 下载 rclone.conf。
 */
import { API_BASE, authedDelete, authedGet, authedPatch, authedPost } from './http'

export type BackupRemote = {
  id: number
  name: string
  backend_type: string
  encrypted: boolean
  config_summary: Record<string, unknown> | null
  last_test_at: string | null
  last_test_ok: boolean | null
  last_test_error: string | null
  created_at: string
}

export type BackupRemoteCreatePayload = {
  name: string
  backend_type: string
  config: Record<string, string>
  encrypted?: boolean
  /** age passphrase — encrypted=true 时必填。丢失即不可恢复。 */
  age_passphrase?: string | null
}

export type BackupRemoteUpdatePayload = Partial<{
  config: Record<string, string>
  age_passphrase: string | null
  encrypted: boolean
}>

export type BackupRemoteTestResponse = {
  ok: boolean
  error: string | null
  listing: string[] | null
}

export type BackupSchedule = {
  id: number
  name: string
  cron_expr: string
  retention_days: number
  include_attachments: boolean
  enabled: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_run_status: string | null
  remote_ids: number[]
  created_at: string
}

export type BackupSchedulePayload = {
  name: string
  cron_expr: string
  retention_days: number
  include_attachments: boolean
  enabled: boolean
  remote_ids: number[]
}

export type BackupRunTarget = {
  id: number
  remote_id: number
  remote_name: string | null
  status: string
  started_at: string | null
  finished_at: string | null
  bytes_transferred: number | null
  error_message: string | null
}

export type BackupRun = {
  id: number
  schedule_id: number | null
  schedule_name: string | null
  started_at: string
  finished_at: string | null
  status: string
  backup_filename: string | null
  bytes_total: number | null
  error_message: string | null
  log_text: string | null
  targets: BackupRunTarget[]
}

export type BackupRunList = {
  items: BackupRun[]
  total: number
}

// ============ Remotes ============

export function listBackupRemotes(token: string): Promise<BackupRemote[]> {
  return authedGet('/admin/backup/remotes', token)
}

export function createBackupRemote(
  token: string,
  payload: BackupRemoteCreatePayload,
): Promise<BackupRemote> {
  return authedPost('/admin/backup/remotes', token, payload)
}

export function updateBackupRemote(
  token: string,
  id: number,
  payload: BackupRemoteUpdatePayload,
): Promise<BackupRemote> {
  return authedPatch(`/admin/backup/remotes/${id}`, token, payload)
}

export function deleteBackupRemote(token: string, id: number): Promise<void> {
  return authedDelete(`/admin/backup/remotes/${id}`, token, {})
}

export function testBackupRemote(
  token: string,
  id: number,
): Promise<BackupRemoteTestResponse> {
  return authedPost(`/admin/backup/remotes/${id}/test`, token, {})
}

export type BackupRemoteRevealed = {
  id: number
  name: string
  backend_type: string
  encrypted: boolean
  /** 明文配置 — 包含敏感字段(secret_access_key 等) + 非敏感字段。
   *  对编辑回填用。 */
  config: Record<string, string>
  /** age passphrase 明文(encrypted=true 时存了一份)。给编辑表单回填用。 */
  age_passphrase: string
}

/**
 * 拉某个 remote 的明文配置(含敏感字段)。给编辑表单回填用。
 * 走 admin scope + audit log。明文不在浏览器持久化,仅 dialog 生命周期内存在。
 */
export function revealBackupRemote(
  token: string,
  id: number,
): Promise<BackupRemoteRevealed> {
  return authedGet(`/admin/backup/remotes/${id}/reveal`, token)
}

// ============ Schedules ============

export function listBackupSchedules(token: string): Promise<BackupSchedule[]> {
  return authedGet('/admin/backup/schedules', token)
}

export function createBackupSchedule(
  token: string,
  payload: BackupSchedulePayload,
): Promise<BackupSchedule> {
  return authedPost('/admin/backup/schedules', token, payload)
}

export function updateBackupSchedule(
  token: string,
  id: number,
  payload: Partial<BackupSchedulePayload>,
): Promise<BackupSchedule> {
  return authedPatch(`/admin/backup/schedules/${id}`, token, payload)
}

export function deleteBackupSchedule(token: string, id: number): Promise<void> {
  return authedDelete(`/admin/backup/schedules/${id}`, token, {})
}

export function runBackupNow(
  token: string,
  scheduleId: number,
): Promise<BackupRun> {
  return authedPost(`/admin/backup/schedules/${scheduleId}/run-now`, token, {})
}

// ============ Runs ============

export function listBackupRuns(
  token: string,
  options?: { schedule_id?: number; limit?: number; offset?: number },
): Promise<BackupRunList> {
  const params = new URLSearchParams()
  if (options?.schedule_id) params.set('schedule_id', String(options.schedule_id))
  if (options?.limit) params.set('limit', String(options.limit))
  if (options?.offset) params.set('offset', String(options.offset))
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return authedGet(`/admin/backup/runs${suffix}`, token)
}

export function getBackupRun(token: string, id: number): Promise<BackupRun> {
  return authedGet(`/admin/backup/runs/${id}`, token)
}

// ============ Restore ============

export type BackupRestore = {
  run_id: number
  phase: string
  started_at: string
  finished_at: string | null
  bytes_total: number | null
  bytes_downloaded: number | null
  error_message: string | null
  extracted_path: string | null
  source_remote_id: number | null
  source_remote_name: string | null
  backup_filename: string | null
}

export function prepareRestore(
  token: string,
  runId: number,
): Promise<BackupRestore> {
  return authedPost(`/admin/backup/runs/${runId}/prepare-restore`, token, {})
}

export function listBackupRestores(
  token: string,
): Promise<{ items: BackupRestore[] }> {
  return authedGet('/admin/backup/restores', token)
}

export function getBackupRestore(
  token: string,
  runId: number,
): Promise<BackupRestore> {
  return authedGet(`/admin/backup/restores/${runId}`, token)
}

export function deleteBackupRestore(
  token: string,
  runId: number,
): Promise<void> {
  return authedDelete(`/admin/backup/restores/${runId}`, token, {})
}

// ============ rclone.conf 下载 ============

/**
 * 返回 rclone.conf 文本(给 CLI 自助 restore 用)。直接 fetch 不走 JSON
 * 解析,返回纯文本。
 */
export async function downloadRcloneConfig(token: string): Promise<string> {
  const res = await fetch(`${API_BASE}/admin/backup/rclone-config`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) {
    throw new Error(`rclone-config fetch failed: ${res.status}`)
  }
  return res.text()
}
