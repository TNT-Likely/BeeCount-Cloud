/** 借款(0023)的纯业务逻辑。

对应「借款」独立实体:borrowed = 借入(欠别人)、lent = 借出(别人欠我)。
服务端是权威(金额/枚举/日期校验在 write 路径),这里放前端的同款预校验 +
列表派生计算,保持可单测。repay_progress / days_to_due / is_overdue 由服务端
按今天现算,前端直接展示;progress 计算在 loanProgress 里复刻口径,供表单预览。
 */

/** 方向:borrowed=借入 / lent=借出。 */
export const LOAN_DIRECTIONS = ['borrowed', 'lent'] as const

export type LoanDirection = (typeof LOAN_DIRECTIONS)[number]

/** 状态:active=在借 / repaid=已结清 / cancelled=作废。 */
export const LOAN_STATUSES = ['active', 'repaid', 'cancelled'] as const

export type LoanStatus = (typeof LOAN_STATUSES)[number]

export type LoanProgress = {
  /** 已还比例 0..1。 */
  ratio: number
  /** none = 未还;partial = 部分还;full = 已还清。 */
  state: 'none' | 'partial' | 'full'
}

/**
 * 还款进度(repaid_amount / principal)。principal 缺失/非正时恒 0。
 * 与服务端 ReadLoanOut.repay_progress 同口径(浮点容差 0.011)。
 */
export function loanProgress(repaidAmount: number, principal: number): LoanProgress {
  const p = Number(principal)
  if (!Number.isFinite(p) || p <= 0) {
    return { ratio: 0, state: 'none' }
  }
  const total = Math.max(Number(repaidAmount) || 0, 0)
  const ratio = Math.min(total / p, 1)
  const state: LoanProgress['state'] =
    total <= 0 ? 'none' : total >= p - 0.011 ? 'full' : 'partial'
  return { ratio, state }
}

export type LoanInputError =
  | 'counterpartyRequired'
  | 'amountRequired'
  | 'rateInvalid'
  | 'startedAtRequired'
  | 'dueBeforeStart'

/**
 * 保存前预校验(与服务端 validate_loan 同规则,错误 key 由 UI 翻译)。
 *
 * - counterparty 必填
 * - principal > 0
 * - interest_rate >= 0(且有限数)
 * - started_at 必填
 * - due_at(若有)必须 >= started_at
 * - repaid_amount >= 0
 *
 * direction 缺省归 borrowed、非法值也归 borrowed;status 缺省归 active —— 都不报错。
 */
export function validateLoanInput(input: {
  counterparty: string
  principal: number
  interestRate?: number
  startedAt: string
  dueAt?: string | null
  repaidAmount?: number
}): LoanInputError | null {
  if (!input.counterparty.trim()) return 'counterpartyRequired'
  const principal = Number(input.principal)
  if (!Number.isFinite(principal) || principal <= 0) return 'amountRequired'
  if (input.interestRate !== undefined) {
    const rate = Number(input.interestRate)
    if (!Number.isFinite(rate) || rate < 0) return 'rateInvalid'
  }
  if (!input.startedAt) return 'startedAtRequired'
  if (input.dueAt) {
    if (input.dueAt < input.startedAt) return 'dueBeforeStart'
  }
  const repaid = input.repaidAmount
  if (repaid !== undefined) {
    const r = Number(repaid)
    if (!Number.isFinite(r) || r < 0) return 'rateInvalid'
  }
  return null
}

/** 借款方向的中文/展示短标签(供卡片副标题等使用)。 */
export function loanDirectionLabel(direction: string): string {
  return direction === 'lent' ? 'lent' : 'borrowed'
}
