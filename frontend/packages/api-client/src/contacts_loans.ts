/** 联系人 / 借款 Web-first REST 客户端。后端字段为 snake_case,与 Pydantic 序列化一致。 */
import { authedDelete, authedGet, authedPatch, authedPost } from './http'
import type { LinkedTx } from './items'
import type { PartyProfile } from './partyProfile'

export type ISODateTime = string

export type ContactTag = 'friend' | 'family' | 'colleague' | 'merchant'
export type LoanDirection = 'lend' | 'borrow'
export type LoanInterestType = 'none' | 'fixed' | 'annual'
export type LoanStatus = 'unpaid' | 'partial' | 'overdue' | 'settled'

export interface Contact {
  id: string
  user_id: string
  name: string
  note: string | null
  avatar_file_id: string | null
  contact_tag: ContactTag | null
  phone: string | null
  wechat: string | null
  alipay: string | null
  /** 关联人资料(头像 / 多联系方式 / 多银行卡)；旧数据可能为 null。 */
  profile: PartyProfile | null
  /** 归档:数据保留但从默认列表/排行隐藏,可恢复。 */
  is_archived: boolean
  /** 停用:列表可见但禁止新增借款/新关联,可重新启用。 */
  is_active: boolean
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface Loan {
  id: string
  user_id: string
  ledger_id: string
  contact_id: string | null
  contact_name: string | null
  direction: LoanDirection
  amount: number
  currency_code: string | null
  happened_at: ISODateTime
  due_at: ISODateTime | null
  interest_type: LoanInterestType
  interest_rate: number | null
  interest_amount: number | null
  note: string | null
  voucher_file_ids: string[]
  status: LoanStatus
  total_repaid: number
  account_id: string | null
  account_name: string | null
  tx_sync_id: string | null
  /** 0046:关联流水的可读摘要(备注/时间/分类/账户/金额)。 */
  tx_sync_details?: LinkedTx[]
  created_at: ISODateTime
  updated_at: ISODateTime
}

export interface LoanRepayment {
  id: string
  user_id: string
  ledger_id: string
  loan_id: string
  amount: number
  repaid_at: ISODateTime
  note: string | null
  account_id: string | null
  account_name: string | null
  tx_sync_id: string | null
  /** 0046:关联流水的可读摘要(还款流水 / 利息流水)。 */
  tx_sync_details?: LinkedTx[]
  interest_tx_sync_details?: LinkedTx[]
  /** 还款凭证 attachment file id 数组 */
  attachment_file_ids: string[]
  interest_amount: number | null
  interest_account_id: string | null
  interest_account_name: string | null
  interest_tx_sync_id: string | null
  /** 合并还款(0039):所属的「一次实际收付款」组 id。 */
  group_id: string | null
  /** 合并还款(0039):该组的本金合计(合并后的实际收款额)。 */
  group_amount: number | null
  /** 合并还款(0039):该组被合并的借款笔数。 */
  group_size: number
  /** 合并还款(0039):该组被合并的各笔借款本金流水 sync_id。 */
  group_merged_from_sync_ids: string[]
  created_at: ISODateTime
}

export interface ContactSummary {
  contact: Contact
  loan_count: number
  lend_total: number
  borrow_total: number
  receivable: number
  payable: number
}

/** 联系人借款排行的一行(GET /contacts/loan-ranks,默认跨全部账本)。 */
export interface ContactLoanRank {
  contact_id: string
  name: string
  contact_tag: ContactTag | null
  loan_count: number
  lend_total: number
  borrow_total: number
  receivable: number
  payable: number
}

export interface VirtualAccounts {
  ledger_id: string
  receivable: number
  payable: number
}

export interface ReminderItem {
  loan: Loan
  contact_name: string | null
  days_to_due: number | null
}

export interface Reminders {
  near_due: ReminderItem[]
  overdue: ReminderItem[]
}

export interface SuggestContact {
  name: string
  source: string
}

// --------------------------------------------------------------------------
// Contacts
// --------------------------------------------------------------------------

export interface ContactCreateBody {
  name: string
  note?: string | null
  avatar_file_id?: string | null
  contact_tag?: ContactTag | null
  phone?: string | null
  wechat?: string | null
  alipay?: string | null
  /** 头像 / 多联系方式 / 多银行卡。传空对象表示清空全部资料。 */
  profile?: PartyProfile | null
}

export interface ContactUpdateBody {
  name?: string | null
  note?: string | null
  avatar_file_id?: string | null
  contact_tag?: ContactTag | null
  phone?: string | null
  wechat?: string | null
  alipay?: string | null
  profile?: PartyProfile | null
}

/** 联系人列表状态筛选(后端 state 查询参数)。 */
export type ContactState = 'active' | 'archived' | 'disabled' | 'all'

export async function fetchContacts(
  token: string,
  search?: string,
  state: ContactState = 'active'
): Promise<Contact[]> {
  const qs = new URLSearchParams()
  if (state !== 'active') qs.set('state', state)
  if (search) qs.set('search', search)
  const tail = qs.toString()
  return authedGet<Contact[]>(`/contacts${tail ? `?${tail}` : ''}`, token)
}

export async function createContact(token: string, body: ContactCreateBody): Promise<Contact> {
  return authedPost<Contact>('/contacts', token, body)
}

export async function updateContact(
  token: string,
  contactId: string,
  body: ContactUpdateBody
): Promise<Contact> {
  return authedPatch<Contact>(`/contacts/${contactId}`, token, body)
}

export async function deleteContact(token: string, contactId: string): Promise<void> {
  return authedDelete<void>(`/contacts/${contactId}`, token)
}

/** 状态切换:全部幂等,返回更新后的联系人。 */
export async function archiveContact(token: string, contactId: string): Promise<Contact> {
  return authedPost<Contact>(`/contacts/${contactId}/archive`, token, {})
}

export async function unarchiveContact(token: string, contactId: string): Promise<Contact> {
  return authedPost<Contact>(`/contacts/${contactId}/unarchive`, token, {})
}

export async function disableContact(token: string, contactId: string): Promise<Contact> {
  return authedPost<Contact>(`/contacts/${contactId}/disable`, token, {})
}

export async function enableContact(token: string, contactId: string): Promise<Contact> {
  return authedPost<Contact>(`/contacts/${contactId}/enable`, token, {})
}

export async function fetchContactSummary(
  token: string,
  contactId: string
): Promise<ContactSummary> {
  return authedGet<ContactSummary>(`/contacts/${contactId}/summary`, token)
}

/**
 * 一次拿全所有联系人的借款汇总,供联系人页渲染「借出/借入」双条形排行。
 * 可选 ledgerId 只看当前账本;不传则跨全部账本(联系人本身是 user-global)。
 * 只返回有借款记录的联系人,无借款的由调用方按 0 处理。
 */
export async function fetchContactLoanRanks(
  token: string,
  ledgerId?: string | null
): Promise<ContactLoanRank[]> {
  const qs = ledgerId ? `?ledger_id=${encodeURIComponent(ledgerId)}` : ''
  return authedGet<ContactLoanRank[]>(`/contacts/loan-ranks${qs}`, token)
}

export async function fetchContactSuggestions(token: string): Promise<SuggestContact[]> {
  return authedGet<SuggestContact[]>('/contacts/suggest', token)
}

// --------------------------------------------------------------------------
// Loans
// --------------------------------------------------------------------------

export interface LoanCreateBody {
  ledger_id: string
  contact_id?: string | null
  direction: LoanDirection
  amount: number
  currency_code?: string | null
  happened_at: ISODateTime
  due_at?: ISODateTime | null
  interest_type?: LoanInterestType
  interest_rate?: number | null
  interest_amount?: number | null
  note?: string | null
  voucher_file_ids?: string[] | null
  account_id?: string | null
  account_name?: string | null
}

export interface LoanUpdateBody {
  contact_id?: string | null
  direction?: LoanDirection
  amount?: number
  currency_code?: string | null
  happened_at?: ISODateTime
  due_at?: ISODateTime | null
  interest_type?: LoanInterestType
  interest_rate?: number | null
  interest_amount?: number | null
  note?: string | null
  voucher_file_ids?: string[] | null
  account_id?: string | null
  account_name?: string | null
}

export interface LoanListQuery {
  ledger_id: string
  contact_id?: string
  direction?: LoanDirection
  status?: LoanStatus
}

export async function fetchLoans(token: string, query: LoanListQuery): Promise<Loan[]> {
  const params = new URLSearchParams()
  params.set('ledger_id', query.ledger_id)
  if (query.contact_id) params.set('contact_id', query.contact_id)
  if (query.direction) params.set('direction', query.direction)
  if (query.status) params.set('status', query.status)
  return authedGet<Loan[]>(`/loans?${params.toString()}`, token)
}

export async function createLoan(token: string, body: LoanCreateBody): Promise<Loan> {
  return authedPost<Loan>('/loans', token, body)
}

export async function fetchLoan(token: string, loanId: string): Promise<Loan> {
  return authedGet<Loan>(`/loans/${loanId}`, token)
}

export async function updateLoan(
  token: string,
  loanId: string,
  body: LoanUpdateBody
): Promise<Loan> {
  return authedPatch<Loan>(`/loans/${loanId}`, token, body)
}

export async function deleteLoan(token: string, loanId: string): Promise<void> {
  return authedDelete<void>(`/loans/${loanId}`, token)
}

export async function fetchRepayments(
  token: string,
  loanId: string
): Promise<LoanRepayment[]> {
  return authedGet<LoanRepayment[]>(`/loans/${loanId}/repayments`, token)
}

export interface RepaymentCreateBody {
  amount: number
  repaid_at: ISODateTime
  note?: string | null
  account_id?: string | null
  account_name?: string | null
  attachment_file_ids?: string[] | null
  interest_amount?: number | null
  interest_account_id?: string | null
  interest_account_name?: string | null
}

export async function createRepayment(
  token: string,
  loanId: string,
  body: RepaymentCreateBody
): Promise<LoanRepayment> {
  return authedPost<LoanRepayment>(`/loans/${loanId}/repayments`, token, body)
}

/**
 * 合并还款(0039):一次实际收付款可能同时结清同联系人同方向下的多笔借款。
 * 例:张三 2025-01-01 借出 50+50+50,实际收到**一笔** 170 元
 * (150 合并本金 + 20 利息)。服务端按 FIFO 把本金分配到各笔借款,
 * 但交易流水里只生成**一条**本金流水(150)+ 一条利息流水(20)。
 */
export interface MergedRepaymentCreateBody {
  ledger_id: string
  contact_id: string
  direction: LoanDirection
  /** 本次实际收付的总额(本金 + 利息)。 */
  amount: number
  /** 本次利息(可选);不传则按「总额 - 剩余本金」自动拆分。 */
  interest_amount?: number | null
  repaid_at: ISODateTime
  note?: string | null
  account_id?: string | null
  account_name?: string | null
  attachment_file_ids?: string[] | null
  interest_account_id?: string | null
  interest_account_name?: string | null
}

export interface MergedRepaymentAllocation {
  loan_id: string
  amount: number
}

export interface MergedRepayment {
  id: string
  ledger_id: string
  contact_id: string | null
  contact_name: string | null
  direction: LoanDirection
  /** 合并后的本金合计。 */
  amount: number
  interest_amount: number | null
  repaid_at: ISODateTime
  note: string | null
  account_id: string | null
  account_name: string | null
  /** 合并后的唯一一条本金流水。 */
  tx_sync_id: string | null
  /** 利息流水。 */
  interest_tx_sync_id: string | null
  /** 被合并的各笔借款本金流水 sync_id(供「查看原单」)。 */
  merged_from_sync_ids: string[]
  /** 本次实际结清的各笔借款分摊明细。 */
  allocations: MergedRepaymentAllocation[]
  created_at: ISODateTime
}

export async function createMergedRepayment(
  token: string,
  body: MergedRepaymentCreateBody
): Promise<MergedRepayment> {
  return authedPost<MergedRepayment>('/loans/repayments/merged', token, body)
}

export async function deleteRepayment(
  token: string,
  loanId: string,
  repaymentId: string
): Promise<void> {
  return authedDelete<void>(`/loans/${loanId}/repayments/${repaymentId}`, token)
}

export async function fetchVirtualAccounts(
  token: string,
  ledgerId: string
): Promise<VirtualAccounts> {
  return authedGet<VirtualAccounts>(
    `/loans/virtual-accounts?ledger_id=${encodeURIComponent(ledgerId)}`,
    token
  )
}

export async function fetchLoanReminders(
  token: string,
  ledgerId: string
): Promise<Reminders> {
  return authedGet<Reminders>(
    `/loans/reminders?ledger_id=${encodeURIComponent(ledgerId)}`,
    token
  )
}
