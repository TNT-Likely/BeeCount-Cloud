import { authedGet, authedPost } from './http'

export type RecurringTransaction = {
  amount: number
  tx_type: 'expense' | 'income' | 'transfer'
  note?: string | null
  currency?: string | null
  native_amount?: number | null
  category_id?: string | null
  category?: string | null
  account_id?: string | null
  account?: string | null
  from_account_id?: string | null
  from_account?: string | null
  to_account_id?: string | null
  to_account?: string | null
}

export type RuleConfig = {
  name: string
  transaction: RecurringTransaction
  frequency: 'daily' | 'weekly' | 'monthly' | 'yearly'
  interval: number
  starts_at: string
  ends_at?: string | null
  day_of_month?: number | null
  day_of_week?: number | null
  month_of_year?: number | null
  time_zone: string
  enabled: boolean
}

export type RecurringRuleRow = {
  id: string
  revision: number
  status: 'active' | 'paused' | 'completed' | 'executing'
  next_due_at: string | null
  can_edit: boolean
  data: { rule: RuleConfig; executed_count: number; last_due_at?: string }
}
export type RecurringRuleList = {
  items: RecurringRuleRow[]
  time_zone: string
  can_create: boolean
  scheduler_enabled: boolean
}
type RuleChange = {
  action: 'create' | 'update' | 'pause' | 'resume' | 'delete'
  client_id?: string
  record_id?: string
  expected_revision?: number
  data?: RuleConfig
  confirm: boolean
}
const path = (ledger: string) => `/ledgers/${encodeURIComponent(ledger)}/recurring-rules`
export const fetchRecurringRules = (token: string, ledger: string) =>
  authedGet<RecurringRuleList>(path(ledger), token)
export const changeRecurringRule = (token: string, ledger: string, payload: RuleChange) =>
  authedPost<{ status: string; next_occurrences?: string[] }>(path(ledger), token, payload)
