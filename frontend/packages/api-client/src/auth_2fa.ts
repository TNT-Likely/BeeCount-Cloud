import { API_BASE, authedGet, authedPost } from './http'
import { extractApiError } from './errors'
import { persistSession } from './auth'
import type {
  LoginResponse,
  TwoFAConfirmResponse,
  TwoFARegenerateResponse,
  TwoFASetupResponse,
  TwoFAStatusResponse
} from './types'

/** GET /auth/2fa/status - 当前用户 2FA 启用状态 */
export async function fetchTwoFAStatus(token: string): Promise<TwoFAStatusResponse> {
  return authedGet<TwoFAStatusResponse>('/auth/2fa/status', token)
}

/** POST /auth/2fa/setup - 申请新 secret(可重复调,会覆盖旧 pending secret) */
export async function setupTwoFA(token: string): Promise<TwoFASetupResponse> {
  return authedPost<TwoFASetupResponse>('/auth/2fa/setup', token, {})
}

/** POST /auth/2fa/confirm - 输 6 位码确认启用,返回一次性 recovery codes(只在这一刻明文) */
export async function confirmTwoFA(
  token: string,
  code: string
): Promise<TwoFAConfirmResponse> {
  return authedPost<TwoFAConfirmResponse>('/auth/2fa/confirm', token, { code })
}

/** POST /auth/2fa/disable - 输密码 + 6 位码双重验证 */
export async function disableTwoFA(
  token: string,
  password: string,
  code: string
): Promise<{ disabled: boolean }> {
  return authedPost<{ disabled: boolean }>('/auth/2fa/disable', token, { password, code })
}

/** POST /auth/2fa/recovery-codes/regenerate - 旧 codes 全失效 */
export async function regenerateRecoveryCodes(
  token: string,
  code: string
): Promise<TwoFARegenerateResponse> {
  return authedPost<TwoFARegenerateResponse>(
    '/auth/2fa/recovery-codes/regenerate',
    token,
    { code }
  )
}

type WebClientInfo = {
  app_version: string | undefined
  os_version: string
  device_model: string
}

/**
 * POST /auth/2fa/verify - 用 challenge_token + 6 位 TOTP / recovery code 兑换真 access/refresh token。
 * verify 成功 → 持久化 session,返回 LoginResponse;失败 → throw ApiError。
 */
export async function verifyTwoFA(params: {
  challenge_token: string
  method: 'totp' | 'recovery_code'
  code: string
  device_id?: string | null
  client_info: WebClientInfo
}): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/2fa/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      challenge_token: params.challenge_token,
      method: params.method,
      code: params.code,
      client_type: 'web',
      device_id: params.device_id || undefined,
      device_name: 'BeeCount Web',
      platform: 'web',
      app_version: params.client_info.app_version,
      os_version: params.client_info.os_version,
      device_model: params.client_info.device_model
    })
  })
  if (!res.ok) {
    throw await extractApiError(res)
  }
  const payload = (await res.json()) as LoginResponse
  persistSession(payload)
  return payload
}
