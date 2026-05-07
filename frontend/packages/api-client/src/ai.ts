import { API_BASE, authedPost } from './http'
import { ApiError, extractApiError } from './errors'

export type AskSource = {
  doc_path: string
  doc_title: string
  section: string
  url: string
}

export type AskEvent =
  | { type: 'chunk'; text: string }
  | { type: 'sources'; items: AskSource[] }
  | { type: 'done' }
  | { type: 'error'; error_code: string; message: string }

export type AskRequest = {
  query: string
  /** 'zh' | 'zh-CN' | 'zh-TW' | 'en' */
  locale: string
}

/**
 * SSE stream from POST /api/v1/ai/ask.
 *
 * 用 fetch + ReadableStream 而不是 EventSource — 因为:
 * - EventSource 只支持 GET,我们是 POST
 * - EventSource 不支持自定义 Authorization header
 *
 * 调用方拿到 AsyncIterable<AskEvent>,自己 for-await:
 *   for await (const ev of streamAsk(token, { query, locale })) { ... }
 *
 * 抛 ApiError(对齐 read endpoints):
 * - 400 AI_NO_CHAT_PROVIDER       — 用户没配,前端跳 SettingsAiPage / 跳官网文档
 * - 503 AI_DOCS_INDEX_EMPTY       — 运营者侧:索引没 build
 * - 503 AI_EMBEDDING_UNAVAILABLE  — 运营者侧:server 没配 embedding key
 *
 * Stream 中的 error event(provider 调用失败)以 `{ type: 'error', ... }` 出现,
 * 不抛异常 — 调用方自己 handle UI 状态。
 */
export async function* streamAsk(
  token: string,
  options: AskRequest,
): AsyncGenerator<AskEvent> {
  const response = await fetch(`${API_BASE}/ai/ask`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(options),
  })

  if (!response.ok) {
    throw await extractApiError(response)
  }

  if (!response.body) {
    throw new ApiError('response body missing', { status: response.status, code: 'AI_NO_BODY' })
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 用 \n\n 分隔 event,每条 event 含 1+ 行 `data: ...`
      let sepIdx: number
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + 2)
        const dataLine = raw.split('\n').find((l) => l.startsWith('data:'))
        if (!dataLine) continue
        const payload = dataLine.slice('data:'.length).trim()
        if (!payload) continue
        try {
          const parsed = JSON.parse(payload) as AskEvent
          yield parsed
          if (parsed.type === 'done' || parsed.type === 'error') {
            return
          }
        } catch {
          // 半截 chunk(不应该发生 — server 一次写完整 line),跳过
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

// ============================================================================
// B2 / B3 — 截图记账 / 文字记账
// ============================================================================

export type TxDraft = {
  type: 'expense' | 'income' | 'transfer'
  amount: number
  happened_at: string
  category_name: string
  account_name: string
  from_account_name: string | null
  to_account_name: string | null
  note: string
  tags: string[]
  confidence: 'high' | 'medium' | 'low'
}

export type ParseTxImageResult = {
  tx_drafts: TxDraft[]
  /** server 缓存了原图 30 分钟,batch save 时用这个 id 取出来转成 attachment */
  image_id: string
}

export type ParseTxTextResult = {
  tx_drafts: TxDraft[]
}

/**
 * B2 — 上传图片 → AI 解析。返回 tx_drafts + image_id。
 * image_id 在 30 分钟内可作为 batchCreateTransactions 的 attach_image_id 使用。
 */
export async function parseTxImage(
  token: string,
  options: {
    image: Blob
    ledgerId?: string
    locale?: string
  }
): Promise<ParseTxImageResult> {
  const fd = new FormData()
  fd.append('image', options.image, 'screenshot.jpg')
  if (options.ledgerId) fd.append('ledger_id', options.ledgerId)
  fd.append('locale', options.locale || 'zh')

  const res = await fetch(`${API_BASE}/ai/parse-tx-image`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: fd,
  })
  if (!res.ok) throw await extractApiError(res)
  return res.json()
}

/** B3 — 上传文本 → AI 解析。 */
export async function parseTxText(
  token: string,
  options: {
    text: string
    ledgerId?: string
    locale?: string
  }
): Promise<ParseTxTextResult> {
  return authedPost<ParseTxTextResult>('/ai/parse-tx-text', token, {
    text: options.text,
    ledger_id: options.ledgerId,
    locale: options.locale || 'zh',
  })
}

// ============================================================================
// Batch tx 创建(B2 / B3 confirm UI 一次性入库)
// ============================================================================

export type BatchTxItem = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: number
  happened_at: string
  note?: string | null
  category_name?: string | null
  category_kind?: 'expense' | 'income' | 'transfer' | null
  account_name?: string | null
  from_account_name?: string | null
  to_account_name?: string | null
  category_id?: string | null
  account_id?: string | null
  from_account_id?: string | null
  to_account_id?: string | null
  tags?: string[] | null
}

export type BatchCreateTxResponse = {
  ledger_id: string
  base_change_id: number
  new_change_id: number
  server_timestamp: string
  created_sync_ids: string[]
  attachment_id: string | null
}

/**
 * POST /write/ledgers/{id}/transactions/batch — 批量创建 N 笔交易。
 *
 * - autoAiTag(默认 true):自动加「AI 记账」tag
 * - extraTagName:额外标签(B2: 图片记账 / B3: 文字记账)
 * - attachImageId:B2 only,server 从缓存取原图转 attachment 共享给所有 tx
 */
export async function batchCreateTransactions(
  token: string,
  options: {
    ledgerId: string
    transactions: BatchTxItem[]
    autoAiTag?: boolean
    extraTagName?: string | null
    attachImageId?: string | null
    locale?: string
    baseChangeId?: number
  }
): Promise<BatchCreateTxResponse> {
  return authedPost<BatchCreateTxResponse>(
    `/write/ledgers/${encodeURIComponent(options.ledgerId)}/transactions/batch`,
    token,
    {
      base_change_id: options.baseChangeId ?? 0,
      transactions: options.transactions,
      auto_ai_tag: options.autoAiTag ?? true,
      extra_tag_name: options.extraTagName ?? null,
      attach_image_id: options.attachImageId ?? null,
      locale: options.locale || 'zh',
    }
  )
}
