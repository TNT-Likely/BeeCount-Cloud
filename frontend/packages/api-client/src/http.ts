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

// ---------------------------------------------------------------------------
// Auth token coordination
// ---------------------------------------------------------------------------
//
// Without a global 401 handler every caller has to remember to check for
// ``status === 401`` and trigger a logout. In practice they don't, which means
// one expired token mid-session leaves the UI half-alive: reads fail silently,
// writes succeed until the next refresh. This module centralizes the retry:
// call sites keep passing the old token; if the server rejects it, we do a
// single-flight refresh here and replay the request transparently.

type RefreshFn = () => Promise<string>
type LogoutFn = () => void

let refreshFn: RefreshFn | null = null
let logoutFn: LogoutFn | null = null
let refreshInFlight: Promise<string> | null = null

/**
 * Wire the http layer to app-level auth callbacks. Call once after login
 * succeeds; no-op safe to call repeatedly.
 */
export function configureHttp(opts: { refreshToken?: RefreshFn | null; onLogout?: LogoutFn | null }): void {
  refreshFn = opts.refreshToken ?? null
  logoutFn = opts.onLogout ?? null
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

async function doRefresh(): Promise<string> {
  if (!refreshFn) throw new Error('no refresh configured')
  if (!refreshInFlight) {
    refreshInFlight = refreshFn().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

type FetchMaker = (token: string) => Promise<Response>

/**
 * Perform an authed fetch with transparent single-flight token refresh on 401.
 * Callers provide a factory that builds the request given the current token
 * string so we can replay the call with a refreshed token.
 */
async function authedFetch(makeRequest: FetchMaker, token: string): Promise<Response> {
  const res = await makeRequest(token)
  if (res.status !== 401) return res
  // Drain the body so we don't leak the connection on node/fetch implementations.
  try {
    await res.text()
  } catch (_) {
    // ignore
  }
  if (!refreshFn) {
    // No refresh path configured — surface 401 so caller logs out explicitly.
    logoutFn?.()
    return res
  }
  try {
    const fresh = await doRefresh()
    return await makeRequest(fresh)
  } catch (_) {
    logoutFn?.()
    return res
  }
}

export async function authedGet<T>(path: string, token: string): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        headers: authHeaders(tok),
        // 数据是事件日志 + 最新快照驱动的，任何缓存命中都会让 refresh-after-write
        // 看到上一份数据。显式拒绝，避免浏览器/中间 CDN 给同路径返回旧响应。
        cache: 'no-store'
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedPost<T>(
  path: string,
  token: string,
  body: unknown,
  idempotencyKey?: string
): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: {
          ...authHeaders(tok, idempotencyKey),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedPatch<T>(path: string, token: string, body: unknown): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'PATCH',
        headers: {
          ...authHeaders(tok),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedDelete<T>(path: string, token: string, body?: unknown): Promise<T> {
  const hasBody = typeof body !== 'undefined'
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'DELETE',
        headers: hasBody
          ? {
              ...authHeaders(tok),
              'Content-Type': 'application/json'
            }
          : authHeaders(tok),
        body: hasBody ? JSON.stringify(body) : undefined
      }),
    token
  )
  return parseResponse<T>(res)
}
