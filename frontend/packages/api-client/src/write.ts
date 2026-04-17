import { authedDelete, authedPatch, authedPost } from './http'
import type {
  AccountPayload,
  CategoryPayload,
  LedgerCreatePayload,
  LedgerMetaPayload,
  ReadAccount,
  ReadCategory,
  ReadTag,
  TagPayload,
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
  baseChangeId: number
): Promise<WriteCommitMeta> {
  return authedDelete<WriteCommitMeta>(
    `/write/ledgers/${encodeURIComponent(ledgerId)}/accounts/${encodeURIComponent(accountId)}`,
    token,
    { base_change_id: baseChangeId }
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
