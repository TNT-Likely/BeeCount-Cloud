// 共享账本 API client — 成员管理 + 邀请管理(对应 server src/routers/
// members.py + invites.py)。
//
// Endpoint 总览:
//   GET    /ledgers/{id}/members             list_members
//   PATCH  /ledgers/{id}/members/{user_id}   update_member_role(Phase 1 no-op,留接口)
//   DELETE /ledgers/{id}/members/{user_id}   remove_member(踢人 / 退出)
//   POST   /ledgers/{id}/invites             create_invite
//   GET    /ledgers/{id}/invites             list_invites
//   DELETE /invites/{code}                   revoke_invite
//   POST   /invites/{code}/preview           preview_invite(未登录也能调)
//   POST   /invites/{code}/accept            accept_invite

import { API_BASE, authedGet } from './http'
import { extractApiError } from './errors'

// === Types ===

export type LedgerMember = {
  user_id: string
  email: string
  display_name: string | null
  role: string
  joined_at: string
  invited_by_user_id: string | null
  is_self: boolean
  avatar_url: string | null
  avatar_version: number
}

export type LedgerInvite = {
  code: string
  formatted_code: string
  target_role: string
  expires_at: string
  created_at: string
  invited_by_user_id: string
  share_url: string
}

/**
 * server `POST /ledgers/{id}/invites` 返结构,跟 list invites 不同 —
 * 不含 `invited_by_user_id`(创建时是当前 caller,服务器没必要回显)。
 */
export type LedgerInviteCreateResponse = Omit<LedgerInvite, 'invited_by_user_id'>


export type LedgerInvitePreview = {
  code: string
  ledger_external_id: string
  ledger_name: string | null
  ledger_currency: string
  invited_by_display: string
  target_role: string
  expires_at: string
}

export type LedgerInviteAcceptResponse = {
  ledger_external_id: string
  ledger_name: string | null
  ledger_currency: string
  role: string
  member_count: number
}

// === Member Stats (按 caller 维度的本期收入/支出) ===

export type MemberStatScope = 'month' | 'year' | 'all'

export type MemberStatItem = {
  user_id: string
  email: string | null
  display_name: string | null
  avatar_url: string | null
  avatar_version: number
  role: string
  income_total: number
  expense_total: number
  tx_count: number
}

export type MemberStatsResponse = {
  ledger_id: string
  ledger_currency: string
  scope: MemberStatScope
  period: string | null
  start_at: string | null
  end_at: string | null
  items: MemberStatItem[]
}

export async function fetchMemberStats(
  token: string,
  ledgerId: string,
  options?: { scope?: MemberStatScope; period?: string; tzOffsetMinutes?: number },
): Promise<MemberStatsResponse> {
  const params = new URLSearchParams()
  if (options?.scope) params.set('scope', options.scope)
  if (options?.period) params.set('period', options.period)
  if (typeof options?.tzOffsetMinutes === 'number') {
    params.set('tz_offset_minutes', String(options.tzOffsetMinutes))
  }
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return authedGet<MemberStatsResponse>(
    `/ledgers/${encodeURIComponent(ledgerId)}/member-stats${suffix}`,
    token,
  )
}

// === Members ===

export async function fetchLedgerMembers(
  token: string,
  ledgerId: string,
): Promise<LedgerMember[]> {
  return authedGet<LedgerMember[]>(
    `/ledgers/${encodeURIComponent(ledgerId)}/members`,
    token,
  )
}

export async function removeLedgerMember(
  token: string,
  ledgerId: string,
  userId: string,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/ledgers/${encodeURIComponent(ledgerId)}/members/${encodeURIComponent(userId)}`,
    {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    },
  )
  if (!res.ok) throw await extractApiError(res)
}

// === Invites ===

export async function createLedgerInvite(
  token: string,
  ledgerId: string,
  options?: { role?: 'editor'; expires_in_hours?: number },
): Promise<LedgerInviteCreateResponse> {
  const res = await fetch(
    `${API_BASE}/ledgers/${encodeURIComponent(ledgerId)}/invites`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        role: options?.role || 'editor',
        expires_in_hours: options?.expires_in_hours ?? 24,
      }),
    },
  )
  if (!res.ok) throw await extractApiError(res)
  return res.json()
}

export async function fetchLedgerInvites(
  token: string,
  ledgerId: string,
): Promise<LedgerInvite[]> {
  return authedGet<LedgerInvite[]>(
    `/ledgers/${encodeURIComponent(ledgerId)}/invites`,
    token,
  )
}

export async function revokeLedgerInvite(
  token: string,
  ledgerId: string,
  code: string,
): Promise<void> {
  // server: DELETE /ledgers/{ledger_external_id}/invites/{code}
  // (并非 /invites/{code} — 那条路径不存在,会 404)
  const res = await fetch(
    `${API_BASE}/ledgers/${encodeURIComponent(ledgerId)}/invites/${encodeURIComponent(code)}`,
    {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    },
  )
  if (!res.ok) throw await extractApiError(res)
}

export async function previewLedgerInvite(
  token: string,
  code: string,
): Promise<LedgerInvitePreview> {
  const res = await fetch(
    `${API_BASE}/invites/${encodeURIComponent(code)}/preview`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    },
  )
  if (!res.ok) throw await extractApiError(res)
  return res.json()
}

export async function acceptLedgerInvite(
  token: string,
  code: string,
): Promise<LedgerInviteAcceptResponse> {
  const res = await fetch(
    `${API_BASE}/invites/${encodeURIComponent(code)}/accept`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    },
  )
  if (!res.ok) throw await extractApiError(res)
  return res.json()
}
