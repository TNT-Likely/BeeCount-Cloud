import type { RecurringUpdateFromPayload } from '@beecount/api-client'

/**
 * 「修改連同未來週期」→ update-from 端點的 payload 組裝。
 *
 * 語意:表單的**最終狀態**整份套用到規則 + 這期以後所有未單獨編輯過的期數,
 * 所以「清空」也要明確送出(`null` / `[]`),不能用 `|| undefined` 省略——
 * 省略的鍵 server 視為「不動」,會讓使用者移除專案/備註/標籤/手續費後,
 * 後面的期數仍保留舊值。
 *
 * `txPayload` 是同一份表單給單筆編輯用的 payload;其中手續費/折扣欄位只在
 * 使用者開啟該功能時才存在,這裡以「鍵是否存在」判斷開關。
 */
export type RecurringUpdateFromSource = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  note?: string | null
  merchant?: string | null
  project_id?: string | null
  base_amount?: number
  fee_amount?: number
  fee_label?: string | null
  discount_amount?: number
  discount_label?: string | null
  reward_rule_ids?: string[]
}

export function buildRecurringUpdateFromPayload(args: {
  txPayload: RecurringUpdateFromSource
  categoryId?: string | null
  accountId?: string | null
  fromAccountId?: string | null
  toAccountId?: string | null
  tagIds: string[]
}): RecurringUpdateFromPayload {
  const { txPayload: p } = args
  const isTransfer = p.tx_type === 'transfer'
  const out: RecurringUpdateFromPayload = {
    tx_type: p.tx_type,
    amount: p.amount,
    note: p.note ?? null,
    category_id: isTransfer ? undefined : args.categoryId || undefined,
    account_id: isTransfer ? undefined : args.accountId || undefined,
    from_account_id: isTransfer ? args.fromAccountId || undefined : undefined,
    to_account_id: isTransfer ? args.toAccountId || undefined : undefined,
    merchant: p.merchant ?? null,
    // 轉帳沒有專案;非轉帳清掉專案要明確送 null。
    project_id: isTransfer ? undefined : p.project_id || null,
    tag_ids: args.tagIds,
    reward_rule_ids: p.tx_type === 'expense' ? p.reward_rule_ids ?? [] : [],
  }
  // 手續費/折扣:server 對規則只接受 expense/income(transfer 送這些鍵會 400),
  // 所以只對非轉帳送;表單沒開啟時送 null 把規則與未來期數的舊值清掉。
  if (!isTransfer) {
    const feeOn = p.fee_amount !== undefined
    out.base_amount = feeOn ? p.base_amount ?? null : null
    out.fee_amount = feeOn ? p.fee_amount ?? null : null
    out.fee_label = feeOn ? p.fee_label ?? null : null
    out.discount_amount = feeOn ? p.discount_amount ?? null : null
    out.discount_label = feeOn ? p.discount_label ?? null : null
  }
  return out
}
