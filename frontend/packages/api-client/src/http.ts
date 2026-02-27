import { extractApiError } from './errors'

export const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL || '/api/v1'

function resolveApiBaseUrl(): string | null {
  const normalized = `${API_BASE || ''}`.trim()
  if (!normalized) return null
  try {
    return new URL(normalized).toString()
  } catch (_) {
    if (typeof window === 'undefined') return null
    try {
      return new URL(normalized, window.location.origin).toString()
    } catch (_) {
      return null
    }
  }
}

export function resolveApiUrl(value?: string | null): string | null {
  const normalized = `${value || ''}`.trim()
  if (!normalized) return null
  try {
    return new URL(normalized).toString()
  } catch (_) {
    const base = resolveApiBaseUrl()
    if (!base) return normalized
    try {
      return new URL(normalized, base).toString()
    } catch (_) {
      return normalized
    }
  }
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw await extractApiError(res)
  }
  return res.json()
}

function authHeaders(token: string, idempotencyKey?: string): Record<string, string> {
  const out: Record<string, string> = {
    Authorization: `Bearer ${token}`
  }
  if (idempotencyKey) out['Idempotency-Key'] = idempotencyKey
  return out
}

export async function authedGet<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(token)
  })
  return parseResponse<T>(res)
}

export async function authedPost<T>(
  path: string,
  token: string,
  body: unknown,
  idempotencyKey?: string
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      ...authHeaders(token, idempotencyKey),
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  })
  return parseResponse<T>(res)
}

export async function authedPatch<T>(path: string, token: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: {
      ...authHeaders(token),
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  })
  return parseResponse<T>(res)
}

export async function authedDelete<T>(path: string, token: string, body?: unknown): Promise<T> {
  const hasBody = typeof body !== 'undefined'
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: hasBody
      ? {
          ...authHeaders(token),
          'Content-Type': 'application/json'
        }
      : authHeaders(token),
    body: hasBody ? JSON.stringify(body) : undefined
  })
  return parseResponse<T>(res)
}
