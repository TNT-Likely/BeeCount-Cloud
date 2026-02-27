import { authedGet, authedPost, resolveApiUrl } from './http'
import type {
  ShareInviteListItem,
  ShareInviteResponse,
  ShareMember,
  ShareMemberAddResponse,
  ShareMemberRemoveResponse
} from './types'

export async function createShareInvite(
  token: string,
  ledgerId: string,
  role: 'editor' | 'viewer',
  maxUses = 1
): Promise<ShareInviteResponse> {
  return authedPost<ShareInviteResponse>('/share/invite', token, {
    ledger_id: ledgerId,
    role,
    max_uses: maxUses
  })
}

export async function revokeShareInvite(token: string, inviteId: string): Promise<any> {
  return authedPost<any>('/share/invite/revoke', token, { invite_id: inviteId })
}

export async function joinShare(token: string, inviteCode: string): Promise<any> {
  return authedPost<any>('/share/join', token, { invite_code: inviteCode })
}

export async function leaveShare(token: string, ledgerId: string): Promise<any> {
  return authedPost<any>('/share/leave', token, { ledger_id: ledgerId })
}

export async function listShareMembers(token: string, ledgerId: string): Promise<ShareMember[]> {
  const rows = await authedGet<ShareMember[]>(`/share/members?ledger_id=${encodeURIComponent(ledgerId)}`, token)
  return rows.map((member) => ({
    ...member,
    user_avatar_url: resolveApiUrl(member.user_avatar_url)
  }))
}

export async function listShareInvites(token: string, ledgerId: string): Promise<ShareInviteListItem[]> {
  return authedGet<ShareInviteListItem[]>(`/share/invites?ledger_id=${encodeURIComponent(ledgerId)}`, token)
}

export async function updateShareMemberRole(
  token: string,
  ledgerId: string,
  userId: string,
  role: 'editor' | 'viewer'
): Promise<any> {
  return authedPost<any>('/share/member/role', token, {
    ledger_id: ledgerId,
    user_id: userId,
    role
  })
}

export async function addShareMember(
  token: string,
  ledgerId: string,
  memberEmail: string,
  role: 'editor' | 'viewer'
): Promise<ShareMemberAddResponse> {
  return authedPost<ShareMemberAddResponse>('/share/member/add', token, {
    ledger_id: ledgerId,
    member_email: memberEmail,
    role
  })
}

export async function removeShareMember(
  token: string,
  ledgerId: string,
  memberEmail: string
): Promise<ShareMemberRemoveResponse> {
  return authedPost<ShareMemberRemoveResponse>('/share/member/remove', token, {
    ledger_id: ledgerId,
    member_email: memberEmail
  })
}
