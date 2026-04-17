import { describe, expect, it } from 'vitest'

import { canManageLedger, canWriteTransactions } from '@beecount/web-features'

// Single-user-per-ledger: whoever sees a ledger is its owner. The role field
// stays on the wire for back-compat but no longer gates permissions.
describe('permission matrix (single-user-per-ledger)', () => {
  it('canWriteTransactions returns true regardless of role', () => {
    expect(canWriteTransactions('owner')).toBe(true)
    expect(canWriteTransactions('editor')).toBe(true)
    expect(canWriteTransactions('viewer')).toBe(true)
    expect(canWriteTransactions(undefined)).toBe(true)
  })

  it('canManageLedger returns true regardless of role', () => {
    expect(canManageLedger('owner')).toBe(true)
    expect(canManageLedger('editor')).toBe(true)
    expect(canManageLedger('viewer')).toBe(true)
    expect(canManageLedger(undefined)).toBe(true)
  })
})
