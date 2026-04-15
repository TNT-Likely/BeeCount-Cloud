/// 单用户模式下，当前用户即为 owner，始终有写权限
export function canWriteTransactions(_role?: string): boolean {
  return true
}

/// 单用户模式下，当前用户即为 owner，始终可管理账本
export function canManageLedger(_role?: string): boolean {
  return true
}
