import { authedDelete, authedGet, authedPatch, authedPost } from './http'
import type { LinkedTx } from './items'
import type { ISODateTime } from './contacts_loans'

export type PlanType = 'free' | 'fixed' | 'wish'
export type PlanStatus = 'active' | 'completed' | 'abandoned' | 'archived'

export interface SavingPlan {
  id: string
  user_id: string
  ledger_id: string
  name: string
  icon: string | null
  target_amount: number
  currency_code: string | null
  deadline: ISODateTime | null
  account_id: string | null
  account_name: string | null
  plan_type: PlanType
  monthly_amount: number | null
  status: PlanStatus
  note: string | null
  saved_amount: number
  progress: number
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface SavingContribution {
  id: string
  plan_id: string
  amount: number
  /** 按关联流水实时状态派生的有效金额(流水删=0/改额跟随/退款冲减) */
  effective_amount: number
  happened_at: ISODateTime
  note: string | null
  tx_sync_id: string | null
  /** 0046:关联流水的可读摘要(存入流水 / 到账流水)。 */
  tx_sync_details?: LinkedTx[]
  receive_tx_sync_details?: LinkedTx[]
  account_id: string | null
  account_name: string | null
  receive_account_id: string | null
  receive_account_name: string | null
  receive_tx_sync_id: string | null
  category_name: string | null
  currency_code: string | null
  native_amount: number | null
  attachments: unknown[] | null
  exclude_from_stats: boolean
  exclude_from_budget: boolean
  created_at: ISODateTime
}

export async function fetchSavingPlans(token: string, ledgerId: string, status?: string): Promise<SavingPlan[]> {
  const params = new URLSearchParams()
  params.set('ledger_id', ledgerId)
  if (status) params.set('status', status)
  return authedGet<SavingPlan[]>(`/saving-plans?${params.toString()}`, token)
}

export async function createSavingPlan(token: string, body: Record<string, unknown>): Promise<SavingPlan> {
  return authedPost<SavingPlan>('/saving-plans', token, body)
}

export async function fetchSavingPlan(token: string, id: string): Promise<SavingPlan> {
  return authedGet<SavingPlan>(`/saving-plans/${id}`, token)
}

export async function updateSavingPlan(token: string, id: string, body: Record<string, unknown>): Promise<SavingPlan> {
  return authedPatch<SavingPlan>(`/saving-plans/${id}`, token, body)
}

export async function deleteSavingPlan(token: string, id: string): Promise<void> {
  return authedDelete<void>(`/saving-plans/${id}`, token)
}

export async function fetchContributions(token: string, planId: string): Promise<SavingContribution[]> {
  return authedGet<SavingContribution[]>(`/saving-plans/${planId}/contributions`, token)
}

/** 修改一笔存入记录(金额 / 时间 / 备注 / 分类 / 统计开关)。 */
export async function updateContribution(
  token: string,
  id: string,
  body: Record<string, unknown>
): Promise<SavingContribution> {
  return authedPatch<SavingContribution>(
    `/saving-plans/contributions/${encodeURIComponent(id)}`,
    token,
    body
  )
}

export async function contributeToPlan(token: string, planId: string, body: Record<string, unknown>): Promise<SavingContribution> {
  return authedPost<SavingContribution>(`/saving-plans/${planId}/contribute`, token, body)
}

export async function deleteContribution(token: string, id: string): Promise<void> {
  return authedDelete<void>(`/saving-plans/contributions/${id}`, token)
}

export async function abandonPlan(token: string, id: string): Promise<SavingPlan> {
  return authedPost<SavingPlan>(`/saving-plans/${id}/abandon`, token, {})
}

export async function completePlan(token: string, id: string): Promise<SavingPlan> {
  return authedPost<SavingPlan>(`/saving-plans/${id}/complete`, token, {})
}

export async function archivePlan(token: string, id: string): Promise<SavingPlan> {
  return authedPost<SavingPlan>(`/saving-plans/${id}/archive`, token, {})
}