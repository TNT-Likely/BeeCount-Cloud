export function canWriteTransactions(role?: string): boolean {
  return role === 'owner' || role === 'editor'
}

export function canManageLedger(role?: string): boolean {
  return role === 'owner'
}
