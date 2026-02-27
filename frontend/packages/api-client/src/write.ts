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

export async function createWorkspaceAccount(
  token: string,
  payload: AccountPayload,
  userId?: string
): Promise<ReadAccount> {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  return authedPost<ReadAccount>(`/write/workspace/accounts${query}`, token, payload)
}

export async function updateWorkspaceAccount(
  token: string,
  accountId: string,
  payload: Partial<AccountPayload>
): Promise<ReadAccount> {
  return authedPatch<ReadAccount>(`/write/workspace/accounts/${encodeURIComponent(accountId)}`, token, payload)
}

export async function deleteWorkspaceAccount(token: string, accountId: string) {
  return authedDelete<ReadAccount>(`/write/workspace/accounts/${encodeURIComponent(accountId)}`, token)
}

export async function createWorkspaceCategory(
  token: string,
  payload: CategoryPayload,
  userId?: string
): Promise<ReadCategory> {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  return authedPost<ReadCategory>(`/write/workspace/categories${query}`, token, payload)
}

export async function updateWorkspaceCategory(
  token: string,
  categoryId: string,
  payload: Partial<CategoryPayload>
): Promise<ReadCategory> {
  return authedPatch<ReadCategory>(`/write/workspace/categories/${encodeURIComponent(categoryId)}`, token, payload)
}

export async function deleteWorkspaceCategory(token: string, categoryId: string) {
  return authedDelete<ReadCategory>(`/write/workspace/categories/${encodeURIComponent(categoryId)}`, token)
}

export async function createWorkspaceTag(
  token: string,
  payload: TagPayload,
  userId?: string
): Promise<ReadTag> {
  const query = userId ? `?user_id=${encodeURIComponent(userId)}` : ''
  return authedPost<ReadTag>(`/write/workspace/tags${query}`, token, payload)
}

export async function updateWorkspaceTag(
  token: string,
  tagId: string,
  payload: Partial<TagPayload>
): Promise<ReadTag> {
  return authedPatch<ReadTag>(`/write/workspace/tags/${encodeURIComponent(tagId)}`, token, payload)
}

export async function deleteWorkspaceTag(token: string, tagId: string) {
  return authedDelete<ReadTag>(`/write/workspace/tags/${encodeURIComponent(tagId)}`, token)
}
