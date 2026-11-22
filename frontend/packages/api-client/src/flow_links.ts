import { authedGet } from './http'

/** 关联实体类型(0043):流水 → 业务实体的跳转入口。 */
export type FlowLinkKind =
  | 'loan'
  | 'saving_plan'
  | 'item'
  | 'reimbursement'
  | 'deposit'
  | 'contact'
  | 'member'

/** 跳转目标:页面路由 + 该页面的定位参数。 */
export interface FlowLinkTarget {
  /** 前端页面标识:loans / saving-plans / items / reimbursements / deposits / contacts / members */
  page: string
  loan_id?: string
  plan_id?: string
  item_id?: string
  reimbursement_id?: string
  reimbursement_item_id?: string
  deposit_id?: string
  contact_id?: string
  /** 成员(0055):收支归属/分摊的成员 sync_id。 */
  member_id?: string
}

/** 一条关联记录。 */
export interface FlowLink {
  kind: FlowLinkKind
  /** 主标题,如「张三 · 300」 */
  title: string
  /** 副标题,如「2025-01-01 10:00 · 合并 3 笔收款」 */
  subtitle: string
  target: FlowLinkTarget
  amount: number
}

export interface FlowLinksResult {
  links: FlowLink[]
  count: number
}

/**
 * 查询某条流水关联的业务实体(0043)。
 *
 * 所有「关联交易流水」都在这里统一反查:借款/还款、储蓄罐存入、物品、
 * 物品流水、报销项/报销单、定期存款。返回的每一项都可点击跳转。
 */
export async function fetchFlowLinks(
  token: string,
  txSyncId: string,
  ledgerId?: string
): Promise<FlowLinksResult> {
  const query = new URLSearchParams()
  if (ledgerId) query.set('ledger_id', ledgerId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<FlowLinksResult>(
    `/read/transactions/${encodeURIComponent(txSyncId)}/links${suffix}`,
    token
  )
}
