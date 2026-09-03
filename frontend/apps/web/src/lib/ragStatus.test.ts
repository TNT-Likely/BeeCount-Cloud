import { describe, expect, it } from 'vitest'

import { getRagLatestState } from './ragStatus'

describe('RAG latest-version state', () => {
  it('prioritizes a failed check over the last known version result', () => {
    expect(getRagLatestState({ is_latest: true, last_error: 'timeout' })).toBe('failed')
  })

  it.each([
    [{ is_latest: true, last_error: null }, 'latest'],
    [{ is_latest: false, last_error: null }, 'outdated'],
    [{ is_latest: null, last_error: null }, 'pending'],
  ] as const)('maps %o to %s', (status, expected) => {
    expect(getRagLatestState(status)).toBe(expected)
  })
})
