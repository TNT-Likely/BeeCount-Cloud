import { authedGet, authedPatch, authedPost, authedDelete } from './http'
import type { LinkedTx } from './items'
import type { AttachmentRef } from './types'

export type ReimbursementItemStatus = 'pending' | 'reimbursed'

export interface ReimbursementItem {
  id: string
  user_id: string
  ledger_id: string
  tx_sync_id: string
  /** 0046:关联流水的可读摘要。 */
  tx_sync_details?: LinkedTx[]
  amount: number
  happened_at: string
  note: string | null
  category_name: string | null
  account_id: string | null
  account_name: string | null
  currency_code: string | null
  attachments: AttachmentRef[] | null
  /** 报销公司维度(0037)：公司档案快照 */
  company_id: string | null
  company_name: string | null
  status: ReimbursementItemStatus
  reimbursement_id: string | null
  created_at: string
  updated_at: string
}

export interface Reimbursement {
  id: string
  user_id: string
  ledger_id: string
  amount: number
  happened_at: string
  note: string | null
  icon: string | null
  attachments: AttachmentRef[] | null
  receive_account_id: string | null
  receive_account_name: string | null
  tx_sync_id: string | null
  /** 0046:关联流水的可读摘要。 */
  tx_sync_details?: LinkedTx[]
  item_count: number
  items: ReimbursementItem[]
  /** 报销公司维度(0037)：公司档案快照 */
  company_id: string | null
  company_name: string | null
  created_at: string
  updated_at: string
}

export interface ReimbursementCreateBody {
  ledger_id: string
  item_ids: string[]
  amount: number
  happened_at?: string | null
  note?: string | null
  icon?: string | null
  attachments?: AttachmentRef[]
  receive_account_id?: string | null
  receive_account_name?: string | null
  /** 报销公司维度(0037)：公司必填 */
  company_id: string
  company_name?: string | null
}

export async function fetchReimbursementItems(
  token: string,
  ledgerId: string,
  status?: ReimbursementItemStatus,
): Promise<ReimbursementItem[]> {
  const params = new URLSearchParams()
  params.set('ledger_id', ledgerId)
  if (status) params.set('status', status)
  return authedGet<ReimbursementItem[]>(`/reimbursements/items?${params.toString()}`, token)
}

export async function fetchReimbursementItemByTx(
  token: string,
  txSyncId: string,
): Promise<ReimbursementItem | null> {
  return authedGet<ReimbursementItem | null>(
    `/reimbursements/items/by-tx/${encodeURIComponent(txSyncId)}`,
    token,
  )
}

export async function createReimbursementItem(
  token: string,
  body: Record<string, unknown>,
): Promise<ReimbursementItem> {
  return authedPost<ReimbursementItem>('/reimbursements/items', token, body)
}

export async function deleteReimbursementItem(token: string, itemId: string): Promise<void> {
  return authedDelete<void>(`/reimbursements/items/${encodeURIComponent(itemId)}`, token)
}

export async function fetchReimbursements(
  token: string,
  ledgerId: string,
): Promise<Reimbursement[]> {
  const params = new URLSearchParams()
  params.set('ledger_id', ledgerId)
  return authedGet<Reimbursement[]>(`/reimbursements?${params.toString()}`, token)
}

export async function createReimbursement(
  token: string,
  body: ReimbursementCreateBody,
): Promise<Reimbursement> {
  return authedPost<Reimbursement>('/reimbursements', token, body as unknown as Record<string, unknown>)
}

export async function updateReimbursement(
  token: string,
  id: string,
  body: Record<string, unknown>,
): Promise<Reimbursement> {
  return authedPatch<Reimbursement>(`/reimbursements/${encodeURIComponent(id)}`, token, body)
}

export async function deleteReimbursement(token: string, id: string): Promise<void> {
  return authedDelete<void>(`/reimbursements/${encodeURIComponent(id)}`, token)
}
