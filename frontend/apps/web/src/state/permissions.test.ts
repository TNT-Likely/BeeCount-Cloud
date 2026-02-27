import { describe, expect, it } from 'vitest'

import { canManageLedger, canWriteTransactions } from '@beecount/web-features'

describe('permission matrix', () => {
  it('allows owner and editor to write transactions', () => {
    expect(canWriteTransactions('owner')).toBe(true)
    expect(canWriteTransactions('editor')).toBe(true)
    expect(canWriteTransactions('viewer')).toBe(false)
  })

  it('allows only owner to manage ledger metadata', () => {
    expect(canManageLedger('owner')).toBe(true)
    expect(canManageLedger('editor')).toBe(false)
    expect(canManageLedger('viewer')).toBe(false)
    expect(canManageLedger(undefined)).toBe(false)
  })
})
