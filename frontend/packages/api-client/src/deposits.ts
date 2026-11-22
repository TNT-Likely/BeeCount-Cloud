/** 定期存款 Web-first REST 客户端。后端字段为 snake_case,与 Pydantic 序列化一致。 */
import { authedDelete, authedGet, authedPatch, authedPost } from './http'
import type { LinkedTx } from './items'
import type { ISODateTime } from './contacts_loans'

export type DepositType = 'fixed' | 'installment' | 'cd' | 'structured'
export type DepositStatus = 'holding' | 'matured' | 'withdrawn' | 'renewed'
export type DepositOnMaturity = 'withdraw' | 'auto_renew'
export type DepositInterestSettlement = 'maturity' | 'monthly' | 'quarterly'

export interface Deposit {
  id: string
  user_id: string
  ledger_id: string
  name: string
  deposit_type: DepositType
  principal: number
  currency_code: string | null
  started_at: ISODateTime
  term_months: number
  annual_rate: number | null
  interest_override: number | null
  maturity_at: ISODateTime | null
  on_maturity: DepositOnMaturity
  interest_settlement: DepositInterestSettlement
  settlement_day: number | null
  status: DepositStatus
  note: string | null
  /** 存单照片 attachment file id 数组 */
  certificate_file_ids: string[]
  account_id: string | null
  account_name: string | null
  flow_tx_sync_id: string | null
  /** 0046:关联流水的可读摘要(存入 / 本金回流 / 利息)。 */
  tx_sync_details?: LinkedTx[]
  principal_return_tx_sync_id: string | null
  interest_tx_sync_id: string | null
  /** 派生:预期到期利息(手动覆盖或公式值) */
  expected_interest: number
  /** 派生:本息合计 */
  total_at_maturity: number
  /** 派生:已周期结息总额 */
  settled_interest: number
  /** 派生:距到期天数(负数 = 已过期) */
  days_to_maturity: number | null
  withdrawn_at: ISODateTime | null
  withdrawn_interest: number | null
  renewed_to_deposit_id: string | null
  parent_deposit_id: string | null
  renewed_at: ISODateTime | null
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface DepositInterestPayment {
  id: string
  user_id: string
  ledger_id: string
  deposit_id: string
  amount: number
  settled_at: ISODateTime
  note: string | null
  account_id: string | null
  account_name: string | null
  tx_sync_id: string | null
  /** 0046:关联流水的可读摘要。 */
  tx_sync_details?: LinkedTx[]
  created_at: ISODateTime
}

export interface DepositSummary {
  ledger_id: string
  /** 持有中 + 已到期存单本金总额 =「定期存款」虚拟账户余额 */
  total_principal: number
  total_interest_expected: number
  count: number
}

export interface DepositReminderItem {
  deposit: Deposit
  days_to_maturity: number
}

export interface DepositReminders {
  near_due: DepositReminderItem[]
  overdue: DepositReminderItem[]
}

// --------------------------------------------------------------------------
// CRUD
// --------------------------------------------------------------------------

export interface DepositCreateBody {
  ledger_id: string
  name: string
  deposit_type?: DepositType
  principal: number
  currency_code?: string | null
  happened_at: ISODateTime
  term_months: number
  annual_rate?: number | null
  interest_override?: number | null
  on_maturity?: DepositOnMaturity
  interest_settlement?: DepositInterestSettlement
  settlement_day?: number | null
  note?: string | null
  certificate_file_ids?: string[] | null
  account_id?: string | null
  account_name?: string | null
}

export interface DepositUpdateBody {
  name?: string
  deposit_type?: DepositType
  principal?: number
  currency_code?: string | null
  happened_at?: ISODateTime
  term_months?: number
  annual_rate?: number | null
  interest_override?: number | null
  on_maturity?: DepositOnMaturity
  interest_settlement?: DepositInterestSettlement
  settlement_day?: number | null
  note?: string | null
  certificate_file_ids?: string[] | null
  account_id?: string | null
  account_name?: string | null
}

export async function fetchDeposits(
  token: string,
  ledgerId: string,
  statusFilter?: DepositStatus | null
): Promise<Deposit[]> {
  const params = new URLSearchParams()
  params.set('ledger_id', ledgerId)
  if (statusFilter) params.set('status', statusFilter)
  return authedGet<Deposit[]>(`/deposits?${params.toString()}`, token)
}

export async function fetchDeposit(token: string, depositId: string): Promise<Deposit> {
  return authedGet<Deposit>(`/deposits/${depositId}`, token)
}

export async function createDeposit(
  token: string,
  body: DepositCreateBody
): Promise<Deposit> {
  return authedPost<Deposit>('/deposits', token, body)
}

export async function updateDeposit(
  token: string,
  depositId: string,
  body: DepositUpdateBody
): Promise<Deposit> {
  return authedPatch<Deposit>(`/deposits/${depositId}`, token, body)
}

export async function deleteDeposit(token: string, depositId: string): Promise<void> {
  return authedDelete<void>(`/deposits/${depositId}`, token)
}

export async function fetchDepositSummary(
  token: string,
  ledgerId: string
): Promise<DepositSummary> {
  return authedGet<DepositSummary>(
    `/deposits/summary?ledger_id=${encodeURIComponent(ledgerId)}`,
    token
  )
}

export async function fetchDepositReminders(
  token: string,
  ledgerId?: string | null
): Promise<DepositReminders> {
  const qs = ledgerId ? `?ledger_id=${encodeURIComponent(ledgerId)}` : ''
  return authedGet<DepositReminders>(`/deposits/reminders${qs}`, token)
}

// --------------------------------------------------------------------------
// Actions
// --------------------------------------------------------------------------

export interface DepositWithdrawBody {
  withdrawn_at: ISODateTime
  account_id?: string | null
  account_name?: string | null
  /** 实际支取利息;不传 = 预期利息 - 已结息 */
  interest_amount?: number | null
}

export async function withdrawDeposit(
  token: string,
  depositId: string,
  body: DepositWithdrawBody
): Promise<Deposit> {
  return authedPost<Deposit>(`/deposits/${depositId}/withdraw`, token, body)
}

export interface DepositRenewBody {
  new_rate?: number | null
  new_term_months?: number | null
  new_name?: string | null
  started_at?: ISODateTime | null
  new_principal?: number | null
  include_interest?: boolean
  account_id?: string | null
  account_name?: string | null
}

export async function renewDeposit(
  token: string,
  depositId: string,
  body: DepositRenewBody
): Promise<Deposit> {
  return authedPost<Deposit>(`/deposits/${depositId}/renew`, token, body)
}

export interface DepositInterestSettleBody {
  amount: number
  settled_at: ISODateTime
  note?: string | null
  account_id?: string | null
  account_name?: string | null
}

export async function settleDepositInterest(
  token: string,
  depositId: string,
  body: DepositInterestSettleBody
): Promise<DepositInterestPayment> {
  return authedPost<DepositInterestPayment>(
    `/deposits/${depositId}/settle-interest`,
    token,
    body
  )
}

export async function fetchDepositInterestPayments(
  token: string,
  depositId: string
): Promise<DepositInterestPayment[]> {
  return authedGet<DepositInterestPayment[]>(
    `/deposits/${depositId}/interest-payments`,
    token
  )
}

export async function deleteDepositInterestPayment(
  token: string,
  paymentId: string
): Promise<void> {
  return authedDelete<void>(`/deposits/interest-payments/${paymentId}`, token)
}
