import { API_BASE } from './http'
import { extractApiError } from './errors'
import type { LoginResponse } from './types'

const DEVICE_ID_KEY = `beecount.web.device_id.${API_BASE}`

export async function login(email: string, password: string): Promise<LoginResponse> {
  const existingDeviceId =
    typeof window !== 'undefined' ? window.localStorage.getItem(DEVICE_ID_KEY) || undefined : undefined
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
  if (typeof window !== 'undefined' && payload.device_id) {
    window.localStorage.setItem(DEVICE_ID_KEY, payload.device_id)
  }
  return payload
}
