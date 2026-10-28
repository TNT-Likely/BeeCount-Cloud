/** 报销(0023)的纯业务逻辑。

对应「报销」独立实体:title 事由、payer 垫付方、reimburser 给付方。
服务端是权威(金额/必填/枚举校验在 write 路径),这里放前端的同款预校验 +
状态机,保持可单测。status:pending=待报销 / reimbursed=已到账 / cancelled=已取消。
 */

/** 状态:pending=待报销 / reimbursed=已到账 / cancelled=已取消。 */
export const REIMBURSEMENT_STATUSES = ['pending', 'reimbursed', 'cancelled'] as const

export type ReimbursementStatus = (typeof REIMBURSEMENT_STATUSES)[number]

/** pending 的下一档是 reimbursed;终态(reimbursed/cancelled)返回 null。 */
export function nextReimbursementStatus(status: string): ReimbursementStatus | null {
  return status === 'pending' ? 'reimbursed' : null
}

export type ReimbursementInputError = 'titleRequired' | 'amountRequired'

/**
 * 保存前预校验(与服务端 validate_reimbursement 同规则,错误 key 由 UI 翻译)。
 *
 * - title 必填
 * - amount > 0
 */
export function validateReimbursementInput(input: {
  title: string
  amount: number
}): ReimbursementInputError | null {
  if (!input.title.trim()) return 'titleRequired'
  const amount = Number(input.amount)
  if (!Number.isFinite(amount) || amount <= 0) return 'amountRequired'
  return null
}
