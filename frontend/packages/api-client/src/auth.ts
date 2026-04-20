import { API_BASE } from './http'
import { extractApiError } from './errors'
import type { LoginResponse } from './types'

const DEVICE_ID_KEY = `beecount.web.device_id.${API_BASE}`
const REFRESH_TOKEN_KEY = `beecount.refresh_token.${API_BASE}`
const USER_ID_KEY = `beecount.user_id.${API_BASE}`

function persistSession(payload: LoginResponse): void {
  if (typeof window === 'undefined') return
  if (payload.device_id) {
    window.localStorage.setItem(DEVICE_ID_KEY, payload.device_id)
  }
  if (payload.refresh_token) {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, payload.refresh_token)
  }
  if (payload.user?.id) {
    window.localStorage.setItem(USER_ID_KEY, payload.user.id)
  }
}

export function getStoredDeviceId(): string | null {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(DEVICE_ID_KEY)
}

export function getStoredUserId(): string | null {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(USER_ID_KEY)
}

export function getStoredRefreshToken(): string | null {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function clearStoredSession(): void {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(REFRESH_TOKEN_KEY)
  window.localStorage.removeItem(USER_ID_KEY)
  // 顺带清 device_id:同一浏览器切换账户时,保留旧 user 的 device_id 会在
  // 后端跨 user 撞 PK(已有后端兜底自动换新 id,但这里清干净让前端行为更
  // 可预测)。同一 user 重新登录新 id 无伤,server 会建新 device 行。
  window.localStorage.removeItem(DEVICE_ID_KEY)
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const existingDeviceId = getStoredDeviceId() || undefined
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email,
      password,
      client_type: 'web',
      device_id: existingDeviceId,
      device_name: 'BeeCount Web',
      platform: 'web'
    })
  })
  if (!res.ok) {
    throw await extractApiError(res)
  }
  const payload = (await res.json()) as LoginResponse
  persistSession(payload)
  return payload
}

/**
 * Exchange the stored refresh token for a fresh access token. Throws if no
 * refresh token is stored or if the exchange fails — caller should then log
 * the user out.
 */
export async function refreshAuth(): Promise<string> {
  const refreshToken = getStoredRefreshToken()
  if (!refreshToken) throw new Error('no refresh token')
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken })
  })
  if (!res.ok) {
    throw await extractApiError(res)
  }
  const payload = (await res.json()) as LoginResponse
  persistSession(payload)
  return payload.access_token
}
