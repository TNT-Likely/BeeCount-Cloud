import { authedDelete, authedPatch, authedPost } from './http'
import type {
  AccountPayload,
  BudgetCreatePayload,
  BudgetUpdatePayload,
  CategoryPayload,
  ItemCreatePayload,
  ItemUpdatePayload,
  ItemReturnCreatePayload,
  ItemReturnUpdatePayload,
  LedgerCreatePayload,
  LedgerMetaPayload,
  ReadAccount,
  ReadCategory,
  ReadTag,
  RefundCreatePayload,
  RefundUpdatePayload,
  MemberCreatePayload,
  MemberUpdatePayload,
  LoanCreatePayload,
  LoanUpdatePayload,
  ReimbursementCreatePayload,
  ReimbursementUpdatePayload,
  TagPayload,
  TimeDepositCreatePayload,
  TimeDepositUpdatePayload,
  TxPayload,
  WriteCommitMeta
} from './types'

export async function createLedger(token: string, payload: LedgerCreatePayload): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>('/write/ledgers', token, payload)
}

export async function updateLedgerMeta(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: LedgerMetaPayload
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/meta`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

/** Soft-delete a ledger. Server writes a tombstone SyncChange; history kept. */
export async function deleteLedger(token: string, ledgerId: string): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}`, token)
}

export async function createTransaction(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: TxPayload
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/transactions`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

export async function updateTransaction(
  token: string,
  ledgerId: string,
  txId: string,
  baseChangeId: number,
  payload: Partial<TxPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/transactions/${encodeURIComponent(txId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteTransaction(
  token: string,
  ledgerId: string,
  txId: string,
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/transactions/${encodeURIComponent(txId)}`,
    token,
    { base_change_id: baseChangeId }
  )
}

export type BatchDeleteTxFailure = {
  tx_id: string
  reason: 'not_found' | 'permission_denied' | 'conflict'
  message?: string | null
}

export type BatchDeleteTxResponse = {
  ledger_id: string
  base_change_id: number
  new_change_id: number
  server_timestamp: string
  deleted_tx_ids: string[]
  failed: BatchDeleteTxFailure[]
}

/**
 * POST /write/ledgers/{id}/transactions/batch/delete — 批量删除交易。
 *
 * 设计:.docs/web-tx-batch-actions.md
 * - 单次最多 200 条(server 上限)
 * - 部分失败不阻断:返回 deleted_tx_ids + failed[]
 * - 服务端走 snapshot 锁 + 一次 SyncChange broadcast,跨设备实时更新
 */
export async function batchDeleteTransactions(
  token: string,
  options: {
    ledgerId: string
    txIds: string[]
    baseChangeId?: number
    idempotencyKey?: string
  }
): Promise<BatchDeleteTxResponse> {
  return authedPost<BatchDeleteTxResponse>(
    `/write/ledgers/${encodeURIComponent(options.ledgerId)}/transactions/batch/delete`,
    token,
    {
      tx_ids: options.txIds,
      base_change_id: options.baseChangeId ?? 0,
    },
    options.idempotencyKey
  )
}

export async function createAccount(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: AccountPayload,
  idempotencyKey?: string
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    },
    idempotencyKey
  )
}

export async function updateAccount(
  token: string,
  ledgerId: string,
  accountId: string,
  baseChangeId: number,
  payload: Partial<AccountPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts/${encodeURIComponent(accountId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteAccount(
  token: string,
  ledgerId: string,
  accountId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  // server 端 snapshot_mutator.delete_account 会 raise 如果账户还有任何关联
  // 交易 —— 客户端必须先看 tx_count,>0 时直接拒绝,不要走删除流程。
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts/${encodeURIComponent(accountId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

export async function createBudget(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: BudgetCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/budgets`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateBudget(
  token: string,
  ledgerId: string,
  budgetId: string,
  baseChangeId: number,
  payload: BudgetUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/budgets/${encodeURIComponent(budgetId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteBudget(
  token: string,
  ledgerId: string,
  budgetId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/budgets/${encodeURIComponent(budgetId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 定期存款(0021)
// ---------------------------------------------------------------------------

export async function createTimeDeposit(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: TimeDepositCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/time-deposits`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateTimeDeposit(
  token: string,
  ledgerId: string,
  depositId: string,
  baseChangeId: number,
  payload: TimeDepositUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/time-deposits/${encodeURIComponent(depositId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteTimeDeposit(
  token: string,
  ledgerId: string,
  depositId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/time-deposits/${encodeURIComponent(depositId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 归物 · 物品持有(0029)
// ---------------------------------------------------------------------------

export async function createItem(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: ItemCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/items`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateItem(
  token: string,
  ledgerId: string,
  itemId: string,
  baseChangeId: number,
  payload: ItemUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/items/${encodeURIComponent(itemId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteItem(
  token: string,
  ledgerId: string,
  itemId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/items/${encodeURIComponent(itemId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 归物(退货/退还物品,0021)
// ---------------------------------------------------------------------------

export async function createItemReturn(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: ItemReturnCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/item-returns`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateItemReturn(
  token: string,
  ledgerId: string,
  returnId: string,
  baseChangeId: number,
  payload: ItemReturnUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/item-returns/${encodeURIComponent(returnId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteItemReturn(
  token: string,
  ledgerId: string,
  returnId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/item-returns/${encodeURIComponent(returnId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 账本内自定义成员(0024)
// ---------------------------------------------------------------------------

export async function createMember(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: MemberCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/members`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateMember(
  token: string,
  ledgerId: string,
  memberId: string,
  baseChangeId: number,
  payload: MemberUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/members/${encodeURIComponent(memberId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteMember(
  token: string,
  ledgerId: string,
  memberId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/members/${encodeURIComponent(memberId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 交易退款(0022)
// ---------------------------------------------------------------------------

export async function createRefund(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: RefundCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/refunds`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateRefund(
  token: string,
  ledgerId: string,
  refundId: string,
  baseChangeId: number,
  payload: RefundUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/refunds/${encodeURIComponent(refundId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteRefund(
  token: string,
  ledgerId: string,
  refundId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/refunds/${encodeURIComponent(refundId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 借款(0023)
// ---------------------------------------------------------------------------

export async function createLoan(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: LoanCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/loans`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateLoan(
  token: string,
  ledgerId: string,
  loanId: string,
  baseChangeId: number,
  payload: LoanUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/loans/${encodeURIComponent(loanId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteLoan(
  token: string,
  ledgerId: string,
  loanId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/loans/${encodeURIComponent(loanId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

// ---------------------------------------------------------------------------
// 报销(0023)
// ---------------------------------------------------------------------------

export async function createReimbursement(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: ReimbursementCreatePayload,
  idempotencyKey?: string,
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/reimbursements`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
    idempotencyKey,
  )
}

export async function updateReimbursement(
  token: string,
  ledgerId: string,
  reimbursementId: string,
  baseChangeId: number,
  payload: ReimbursementUpdatePayload,
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/reimbursements/${encodeURIComponent(reimbursementId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload,
    },
  )
}

export async function deleteReimbursement(
  token: string,
  ledgerId: string,
  reimbursementId: string,
  baseChangeId: number,
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/reimbursements/${encodeURIComponent(reimbursementId)}`,
    token,
    { base_change_id: baseChangeId },
  )
}

export async function createCategory(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: CategoryPayload
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/categories`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

export async function updateCategory(
  token: string,
  ledgerId: string,
  categoryId: string,
  baseChangeId: number,
  payload: Partial<CategoryPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/categories/${encodeURIComponent(categoryId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteCategory(
  token: string,
  ledgerId: string,
  categoryId: string,
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/categories/${encodeURIComponent(categoryId)}`,
    token,
    { base_change_id: baseChangeId }
  )
}

export async function createTag(
  token: string,
  ledgerId: string,
  baseChangeId: number,
  payload: TagPayload
): Promise<WriteCommitMeta> {
  return authedPost<WriteCommitMeta>(`/write/ledgers/${encodeURIComponent(ledgerId)}/tags`, token, {
    base_change_id: baseChangeId,
    ...payload
  })
}

export async function updateTag(
  token: string,
  ledgerId: string,
  tagId: string,
  baseChangeId: number,
  payload: Partial<TagPayload>
): Promise<WriteCommitMeta> {
  return authedPatch<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tags/${encodeURIComponent(tagId)}`,
    token,
    {
      base_change_id: baseChangeId,
      ...payload
    }
  )
}

export async function deleteTag(
  token: string,
  ledgerId: string,
  tagId: string,
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/tags/${encodeURIComponent(tagId)}`,
    token,
    { base_change_id: baseChangeId }
  )
}

// NOTE: the old ``createWorkspaceAccount`` / ``updateWorkspaceCategory`` /
// ``deleteWorkspaceTag`` helpers that targeted /write/workspace/* have been
// removed. They were replaced by per-ledger endpoints (createAccount,
// updateCategory, deleteTag above) which carry base_change_id for conflict
// detection. The server-side /write/workspace/* routes were already unwired
// when multi-user collaboration was simplified out.
