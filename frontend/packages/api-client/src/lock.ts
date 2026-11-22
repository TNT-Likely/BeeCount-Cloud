import { authedGet, authedPatch } from './http'

/** 用户级锁账配置(0067)。lock_date 是 UTC ISO 字符串(当天 23:59:59)。 */
export interface LockSettings {
  is_locked: boolean
  lock_date: string | null
}

export async function fetchLockSettings(token: string): Promise<LockSettings> {
  return authedGet<LockSettings>('/profile/lock', token)
}

export async function patchLockSettings(
  token: string,
  payload: { is_locked?: boolean; lock_date?: string }
): Promise<LockSettings> {
  return authedPatch<LockSettings>('/profile/lock', token, payload)
}
