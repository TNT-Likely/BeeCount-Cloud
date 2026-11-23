import { authedGet, authedPost } from './http'

export type SyncFailure = {
  direction: 'push' | 'pull'
  count: number
  reason: 'changes_rejected' | 'server_error'
  first_failed_at: string
  last_failed_at: string
}
export type DeviceSyncHealth = {
  device_id: string
  device_name: string
  failures: SyncFailure[]
}

type NotificationPayloads = {
  category_missing_parent: {
    count: number
    categories: { id: string; name: string | null; parent_name: string | null }[]
  }
  backup_schedule_issue: {
    schedule_id: number
    schedule_name: string
    reason: 'failed' | 'partial' | 'overdue'
    count: number
    run_id: number | null
    at: string
  }
  device_sync_failure: DeviceSyncHealth
}

export type NotificationKind = keyof NotificationPayloads
export type CloudNotification<K extends NotificationKind = NotificationKind> = {
  [P in K]: { id: string; type: P; unread: boolean } & NotificationPayloads[P]
}[K]

export function fetchNotifications(token: string): Promise<CloudNotification[]> {
  return authedGet('/notifications', token)
}

export async function markNotificationsRead(token: string, ids: string[]): Promise<void> {
  // Mark only the incidents the user saw, including accounts with many devices.
  for (let start = 0; start < ids.length; start += 200) {
    await authedPost('/notifications/read', token, { ids: ids.slice(start, start + 200) })
  }
}

export function fetchDeviceSyncHealth(token: string, deviceId: string): Promise<DeviceSyncHealth> {
  return authedGet(`/devices/${encodeURIComponent(deviceId)}/sync-health`, token)
}
