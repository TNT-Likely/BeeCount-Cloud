/**
 * Decode a JWT access token's `sub` claim (user id) without verifying the
 * signature. We only use this for client-side state scoping (cache keys,
 * component remount keys) — all authorization is enforced server-side, so
 * a forged `sub` here can't elevate privileges.
 */
export function jwtUserId(token: string): string {
  try {
    const [, payload] = token.split('.')
    if (!payload) return ''
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = `${base64}${'='.repeat((4 - (base64.length % 4)) % 4)}`
    const raw = atob(padded)
    const parsed = JSON.parse(raw) as { sub?: string }
    return typeof parsed.sub === 'string' ? parsed.sub : ''
  } catch {
    return ''
  }
}
