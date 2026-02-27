export class ApiError extends Error {
  status: number
  code?: string
  latestChangeId?: number
  latestServerTimestamp?: string | null

  constructor(
    message: string,
    options: {
      status: number
      code?: string
      latestChangeId?: number
      latestServerTimestamp?: string | null
    }
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.latestChangeId = options.latestChangeId
    this.latestServerTimestamp = options.latestServerTimestamp
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

  try {
    const json = JSON.parse(text) as any
    const maybeCode = json?.error?.code
    const maybeMessage = json?.error?.message || json?.detail
    const maybeLatestChangeId = json?.latest_change_id
    const maybeLatestServerTimestamp = json?.latest_server_timestamp

    if (typeof maybeCode === 'string' && maybeCode) code = maybeCode
    if (maybeMessage) message = String(maybeMessage)
    if (typeof maybeLatestChangeId === 'number') latestChangeId = maybeLatestChangeId
    if (typeof maybeLatestServerTimestamp === 'string' || maybeLatestServerTimestamp === null) {
      latestServerTimestamp = maybeLatestServerTimestamp
    }
  } catch {
    // keep plain text fallback
  }

  const resolvedMessage = code ? `[${code}] ${message}` : message
  return new ApiError(resolvedMessage, {
    status: res.status,
    code,
    latestChangeId,
    latestServerTimestamp
  })
}
