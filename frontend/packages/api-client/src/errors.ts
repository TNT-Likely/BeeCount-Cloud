export class ApiError extends Error {
  status: number
  code?: string
  latestChangeId?: number
  latestServerTimestamp?: string | null
  /** server 端附加的调试 raw payload(目前给 AI parse 错误暴露 LLM 原始输出)。 */
  raw?: string

  constructor(
    message: string,
    options: {
      status: number
      code?: string
      latestChangeId?: number
      latestServerTimestamp?: string | null
      raw?: string
    }
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.latestChangeId = options.latestChangeId
    this.latestServerTimestamp = options.latestServerTimestamp
    this.raw = options.raw
  }
}

export async function extractApiError(res: Response): Promise<ApiError> {
  const text = await res.text()
  if (!text) {
    return new ApiError(`HTTP ${res.status}`, { status: res.status })
  }

  let message = text
  let code: string | undefined
  let latestChangeId: number | undefined
  let latestServerTimestamp: string | null | undefined
  let raw: string | undefined

  try {
    const json = JSON.parse(text) as any
    // server 端 error_handling.py 把 HTTPException 的 detail dict 展开到顶层,
    // 包括我们的 `error_code` / `raw`。优先用顶层的 error_code(更有语义),
    // fallback 到 error.code(generic)
    const maybeCode = json?.error_code || json?.error?.code
    const maybeMessage = json?.error?.message || json?.detail
    const maybeLatestChangeId = json?.latest_change_id
    const maybeLatestServerTimestamp = json?.latest_server_timestamp
    const maybeRaw = json?.raw  // AI parse 失败时 server 附加 LLM 原始输出

    if (typeof maybeCode === 'string' && maybeCode) code = maybeCode
    if (maybeMessage) message = String(maybeMessage)
    if (typeof maybeLatestChangeId === 'number') latestChangeId = maybeLatestChangeId
    if (typeof maybeLatestServerTimestamp === 'string' || maybeLatestServerTimestamp === null) {
      latestServerTimestamp = maybeLatestServerTimestamp
    }
    if (typeof maybeRaw === 'string') raw = maybeRaw
  } catch {
    // keep plain text fallback
  }

  const resolvedMessage = code ? `[${code}] ${message}` : message
  return new ApiError(resolvedMessage, {
    status: res.status,
    code,
    latestChangeId,
    latestServerTimestamp,
    raw
  })
}
