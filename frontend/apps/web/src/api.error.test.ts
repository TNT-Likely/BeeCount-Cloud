import { describe, expect, it } from 'vitest'

import { ApiError, extractApiError } from '@beecount/api-client'

describe('extractApiError', () => {
  it('parses write conflict metadata', async () => {
    const response = new Response(
      JSON.stringify({
        error: { code: 'WRITE_CONFLICT', message: 'Write conflict' },
        detail: 'Write conflict',
        latest_change_id: 42,
        latest_server_timestamp: '2026-02-24T12:00:00+00:00'
      }),
      {
        status: 409,
        headers: { 'Content-Type': 'application/json' }
      }
    )

    const err = await extractApiError(response)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('WRITE_CONFLICT')
    expect(err.latestChangeId).toBe(42)
    expect(err.latestServerTimestamp).toBe('2026-02-24T12:00:00+00:00')
    expect(err.message).toBe('[WRITE_CONFLICT] Write conflict')
  })

  it('falls back to plain text message', async () => {
    const response = new Response('boom', { status: 500 })
    const err = await extractApiError(response)

    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(500)
    expect(err.message).toBe('boom')
  })
})
