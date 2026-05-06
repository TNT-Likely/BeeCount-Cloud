import { API_BASE } from './http'
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
