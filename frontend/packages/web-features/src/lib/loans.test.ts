import { describe, expect, it } from 'vitest'

import {
  loanProgress,
  validateLoanInput,
} from './loans'

describe('loanProgress', () => {
  it('未还', () => {
    expect(loanProgress(0, 1000)).toEqual({ ratio: 0, state: 'none' })
  })

  it('部分还款按比例', () => {
    const p = loanProgress(400, 1000)
    expect(p.ratio).toBeCloseTo(0.4)
    expect(p.state).toBe('partial')
  })

  it('还清(含浮点容差)', () => {
    expect(loanProgress(1000, 1000).state).toBe('full')
    expect(loanProgress(999.995, 1000).state).toBe('full')
    expect(loanProgress(999.98, 1000).state).toBe('partial')
  })

  it('超额钳到 1', () => {
    expect(loanProgress(1200, 1000).ratio).toBe(1)
  })

  it('本金缺失时无进度', () => {
    expect(loanProgress(500, 0)).toEqual({ ratio: 0, state: 'none' })
    expect(loanProgress(500, null as unknown as number)).toEqual({ ratio: 0, state: 'none' })
  })
})

describe('validateLoanInput', () => {
  const base = {
    counterparty: '张三',
    principal: 1000,
    interestRate: 0.05,
    startedAt: '2026-01-01',
    dueAt: '2026-12-31',
    repaidAmount: 0,
  }

  it('合法输入通过', () => {
    expect(validateLoanInput(base)).toBeNull()
  })

  it('对手方必填', () => {
    expect(validateLoanInput({ ...base, counterparty: '' })).toBe('counterpartyRequired')
    expect(validateLoanInput({ ...base, counterparty: '   ' })).toBe('counterpartyRequired')
  })

  it('本金必须为正', () => {
    expect(validateLoanInput({ ...base, principal: 0 })).toBe('amountRequired')
    expect(validateLoanInput({ ...base, principal: -5 })).toBe('amountRequired')
  })

  it('利率不能为负', () => {
    expect(validateLoanInput({ ...base, interestRate: -0.1 })).toBe('rateInvalid')
  })

  it('起借日必填', () => {
    expect(validateLoanInput({ ...base, startedAt: '' })).toBe('startedAtRequired')
  })

  it('到期日不能早于起借日', () => {
    expect(validateLoanInput({ ...base, dueAt: '2025-12-31' })).toBe('dueBeforeStart')
    // 无到期日合法
    expect(validateLoanInput({ ...base, dueAt: '' })).toBeNull()
    expect(validateLoanInput({ ...base, dueAt: null })).toBeNull()
  })
})
