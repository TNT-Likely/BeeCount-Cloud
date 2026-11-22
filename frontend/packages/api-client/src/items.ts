import { authedDelete, authedGet, authedPatch, authedPost } from './http'
import type { AttachmentRef } from './types'
import type { ISODateTime } from './contacts_loans'

export type ItemLinkMode = 'new' | 'existing' | 'none'
export type ItemCostMode = 'none' | 'daily_avg' | 'per_day'
export type ItemFlowType = 'income' | 'expense'

/** 关联交易流水的摘要(0046):备注 / 金额 / 时间 / 分类 / 账户。 */
export interface LinkedTx {
  sync_id: string
  tx_type: string
  amount: number
  happened_at: string | null
  note: string | null
  category_name: string | null
  category_kind?: string | null
  account_name: string | null
  currency_code?: string | null
  exclude_from_stats?: boolean
}

export interface Item {
  id: string
  ledger_id: string
  name: string
  price: number
  purchased_at: ISODateTime
  purchase_channel: string | null
  category_name: string | null
  location: string | null
  link_mode: ItemLinkMode
  tx_sync_id: string | null
  /** 关联的交易流水(0040):可关联多笔(如定金/尾款/多平台)。 */
  tx_sync_ids: string[]
  /** 0044:每笔关联流水的金额(syncId → 金额),回填成本合计用。 */
  tx_sync_amounts: Record<string, number>
  /** 0045:关联流水的可读摘要,详情页展示用。 */
  tx_sync_details: LinkedTx[]
  account_id: string | null
  account_name: string | null
  average_mode: ItemCostMode
  target_cost_mode: ItemCostMode
  note: string | null
  attachments: AttachmentRef[] | null
  total_income: number
  total_expense: number
  net_cost: number
  flow_count: number
  days_held: number
  average_cost: number | null
  target_cost: number | null
  created_at: string
  updated_at: string | null
}

export interface ItemCreateBody {
  ledger_id: string
  name: string
  price: number
  purchased_at?: string | null
  purchase_channel?: string | null
  category_name?: string | null
  location?: string | null
  link_mode?: ItemLinkMode
  tx_sync_id?: string | null
  /** 关联已有交易流水(0040):可传多笔。 */
  tx_sync_ids?: string[] | null
  account_id?: string | null
  account_name?: string | null
  average_mode?: ItemCostMode
  target_cost_mode?: ItemCostMode
  note?: string | null
  attachments?: AttachmentRef[] | null
  /** 0044:关联已有流水时价格自动=各笔金额之和(默认 true)。false=用手填价格。 */
  auto_price?: boolean
}

export interface ItemUpdateBody {
  name?: string
  price?: number
  purchased_at?: string | null
  purchase_channel?: string | null
  category_name?: string | null
  location?: string | null
  average_mode?: ItemCostMode
  target_cost_mode?: ItemCostMode
  note?: string | null
  attachments?: AttachmentRef[] | null
  /** 替换关联的交易流水(0040):传数组替换全部,传空数组清空。 */
  tx_sync_ids?: string[] | null
  /** 0044:可改关联方式。 */
  link_mode?: ItemLinkMode
  account_id?: string | null
  account_name?: string | null
  /** 0044:false = 保留手填价格,不做自动汇总。 */
  auto_price?: boolean
}

export interface ItemFlow {
  id: string
  item_id: string
  ledger_id: string
  flow_type: ItemFlowType
  amount: number
  happened_at: ISODateTime
  category_name: string | null
  note: string | null
  link_mode: ItemLinkMode
  tx_sync_id: string | null
  /** 关联的交易流水(0040):可关联多笔。 */
  tx_sync_ids: string[]
  /** 0044:每笔关联流水的金额(syncId → 金额),回填金额合计用。 */
  tx_sync_amounts: Record<string, number>
  /** 0045:关联流水的可读摘要,展示用。 */
  tx_sync_details: LinkedTx[]
  account_id: string | null
  account_name: string | null
  attachments: AttachmentRef[] | null
  created_at: string
  updated_at: string | null
}

export interface ItemFlowCreateBody {
  item_id: string
  flow_type: ItemFlowType
  amount: number
  happened_at?: string | null
  category_name?: string | null
  note?: string | null
  link_mode?: ItemLinkMode
  tx_sync_id?: string | null
  /** 关联已有交易流水(0040):可传多笔。 */
  tx_sync_ids?: string[] | null
  account_id?: string | null
  account_name?: string | null
  attachments?: AttachmentRef[] | null
  /** 0044:关联已有流水时金额自动=各笔金额之和(默认 true)。 */
  auto_amount?: boolean
}

export interface ItemFlowUpdateBody {
  flow_type?: ItemFlowType
  amount?: number
  happened_at?: string | null
  category_name?: string | null
  note?: string | null
  attachments?: AttachmentRef[] | null
  /** 替换关联的交易流水(0040):传数组替换全部,传空数组清空。 */
  tx_sync_ids?: string[] | null
  /** 0044:可改关联方式;false = 保留手填金额,不做自动汇总。 */
  link_mode?: ItemLinkMode
  auto_amount?: boolean
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** 物品管理（0038）。 */
export async function fetchItems(token: string, ledgerId: string): Promise<Item[]> {
  return authedGet<Item[]>(`/items${qs({ ledger_id: ledgerId })}`, token)
}

export async function createItem(token: string, body: ItemCreateBody): Promise<Item> {
  return authedPost<Item>('/items', token, body as unknown as Record<string, unknown>)
}

export async function getItem(token: string, id: string): Promise<Item> {
  return authedGet<Item>(`/items/${encodeURIComponent(id)}`, token)
}

export async function updateItem(token: string, id: string, body: ItemUpdateBody): Promise<Item> {
  return authedPatch<Item>(`/items/${encodeURIComponent(id)}`, token, body as unknown as Record<string, unknown>)
}

export async function deleteItem(token: string, id: string): Promise<void> {
  return authedDelete<void>(`/items/${encodeURIComponent(id)}`, token)
}

export async function fetchItemFlows(token: string, itemId: string): Promise<ItemFlow[]> {
  return authedGet<ItemFlow[]>(`/items/${encodeURIComponent(itemId)}/flows`, token)
}

export async function createItemFlow(token: string, itemId: string, body: ItemFlowCreateBody): Promise<ItemFlow> {
  return authedPost<ItemFlow>(`/items/${encodeURIComponent(itemId)}/flows`, token, body as unknown as Record<string, unknown>)
}

export async function updateItemFlow(token: string, flowId: string, body: ItemFlowUpdateBody): Promise<ItemFlow> {
  return authedPatch<ItemFlow>(`/items/flows/${encodeURIComponent(flowId)}`, token, body as unknown as Record<string, unknown>)
}

export async function deleteItemFlow(token: string, flowId: string): Promise<void> {
  return authedDelete<void>(`/items/flows/${encodeURIComponent(flowId)}`, token)
}
