/**
 * 账本数据导入客户端 —— 跟 .docs/web-ledger-import.md 对齐。
 *
 * 4 个端点:
 *  - uploadImport(file, ?ledgerId) —— multipart 上传 + 解析,返 token + summary
 *  - previewImport(token, opts)    —— 重 preview,改 mapping/账本/dedup 等
 *  - executeImport(token)          —— SSE 流,生成 stage / complete / error 事件
 *  - cancelImport(token)           —— DELETE 取消
 */
import { API_BASE, authedDelete, authedPost } from './http'
import { ApiError, extractApiError } from './errors'

export type ImportSourceFormat = 'beecount' | 'alipay' | 'wechat' | 'generic'

export type ImportFieldMapping = {
  tx_type: string | null
  amount: string | null
  happened_at: string | null
  /** 一级分类(顶级 bucket) */
  category_name: string | null
  /** 二级分类(具体 leaf,可选) */
  subcategory_name: string | null
  account_name: string | null
  from_account_name: string | null
  to_account_name: string | null
  note: string | null
  tags: string[]
  datetime_format: string | null
  strip_currency_symbols: boolean
  expense_is_negative: boolean
}

export type ImportEntityDiff = {
  new_names: string[]
  matched_names: string[]
}

export type ImportStats = {
  total_rows: number
  time_range_start: string | null
  time_range_end: string | null
  total_signed_amount: string
  by_type: {
    expense_count: number
    expense_total: string
    income_count: number
    income_total: string
    transfer_count: number
  }
  accounts: ImportEntityDiff
  categories: ImportEntityDiff
  tags: ImportEntityDiff
  skipped_dedup: number
  parse_errors: Array<{
    code: string
    row_number: number
    message: string
    field_name: string | null
  }>
  parse_errors_total: number
  parse_warnings: Array<{
    code: string
    row_number: number
    message: string
  }>
  parse_warnings_total: number
}

export type ImportTransactionSample = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  happened_at: string
  note: string | null
  category_name: string | null
  parent_category_name: string | null
  account_name: string | null
  from_account_name: string | null
  to_account_name: string | null
  tag_names: string[]
  source_row_number: number
}

export type ImportSummary = {
  import_token: string
  expires_at: string
  source_format: ImportSourceFormat
  headers: string[]
  suggested_mapping: ImportFieldMapping
  current_mapping: ImportFieldMapping
  target_ledger_id: string | null
  dedup_strategy: 'skip_duplicates' | 'insert_all'
  auto_tag_names: string[]
  stats: ImportStats
  sample_rows: Array<Record<string, string>>
  sample_transactions: ImportTransactionSample[]
}

/** SSE 事件类型 */
export type ImportSseEvent =
  | { event: 'stage'; data: { stage: 'accounts' | 'categories' | 'tags' | 'transactions'; done: number; total: number; skipped?: number } }
  | { event: 'complete'; data: { created_tx_count: number; skipped_count: number; new_change_id: number } }
  | {
      event: 'error'
      data: {
        code: string
        row_number: number
        field_name: string | null
        message: string
        raw_line?: string
        total_errors?: number
      }
    }

// ──────────────── upload ────────────────

export async function uploadImport(
  token: string,
  options: { file: File; targetLedgerId?: string | null },
): Promise<ImportSummary> {
  const form = new FormData()
  form.append('file', options.file)
  if (options.targetLedgerId) form.append('target_ledger_id', options.targetLedgerId)
  const response = await fetch(`${API_BASE}/import/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })
  if (!response.ok) throw await extractApiError(response)
  return (await response.json()) as ImportSummary
}

// ──────────────── preview ────────────────

export type PreviewImportOptions = {
  mapping?: ImportFieldMapping
  targetLedgerId?: string | null
  dedupStrategy?: 'skip_duplicates' | 'insert_all'
  autoTagNames?: string[]
}

export async function previewImport(
  token: string,
  importToken: string,
  options: PreviewImportOptions,
): Promise<ImportSummary> {
  const body: Record<string, unknown> = {}
  if (options.mapping) body.mapping = options.mapping
  if (options.targetLedgerId !== undefined) body.target_ledger_id = options.targetLedgerId
  if (options.dedupStrategy !== undefined) body.dedup_strategy = options.dedupStrategy
  if (options.autoTagNames !== undefined) body.auto_tag_names = options.autoTagNames
  return authedPost<ImportSummary>(
    `/import/${encodeURIComponent(importToken)}/preview`,
    token,
    body,
  )
}

// ──────────────── execute(SSE) ────────────────

/**
 * 执行导入,返回 async generator yielding SSE 事件。
 * 调用方:
 *   for await (const ev of streamExecuteImport(token, importToken)) { ... }
 *
 * stream 收到 `complete` 或 `error` 后自动结束。
 */
export async function* streamExecuteImport(
  token: string,
  importToken: string,
): AsyncGenerator<ImportSseEvent> {
  const response = await fetch(
    `${API_BASE}/import/${encodeURIComponent(importToken)}/execute`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      },
    },
  )
  if (!response.ok) throw await extractApiError(response)
  if (!response.body) {
    throw new ApiError('response body missing', { status: response.status, code: 'IMPORT_NO_BODY' })
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sepIdx: number
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + 2)
        let eventName = ''
        let dataPayload = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) dataPayload += line.slice(5).trim()
        }
        if (!eventName) continue
        try {
          const parsed = JSON.parse(dataPayload) as ImportSseEvent['data']
          const evt = { event: eventName, data: parsed } as ImportSseEvent
          yield evt
          if (eventName === 'complete' || eventName === 'error') return
        } catch {
          // 半截 chunk,跳过
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

// ──────────────── cancel ────────────────

export async function cancelImport(token: string, importToken: string): Promise<void> {
  await authedDelete(`/import/${encodeURIComponent(importToken)}`, token)
}
