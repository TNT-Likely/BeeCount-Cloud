import { describe, expect, it } from 'vitest'

import {
  nextReimbursementStatus,
  validateReimbursementInput,
} from './reimbursements'

describe('nextReimbursementStatus', () => {
  it('pending 推进到 reimbursed', () => {
    expect(nextReimbursementStatus('pending')).toBe('reimbursed')
  })

  it('reimbursed / cancelled 是终态', () => {
    expect(nextReimbursementStatus('reimbursed')).toBeNull()
    expect(nextReimbursementStatus('cancelled')).toBeNull()
  })
})

describe('validateReimbursementInput', () => {
  it('合法输入通过', () => {
    expect(
      validateReimbursementInput({ title: '出差机票', amount: 2000 }),
    ).toBeNull()
  })

  it('事由必填', () => {
    expect(
      validateReimbursementInput({ title: '', amount: 2000 }),
    ).toBe('titleRequired')
    expect(
      validateReimbursementInput({ title: '   ', amount: 2000 }),
    ).toBe('titleRequired')
  })

  it('金额必须为正', () => {
    expect(
      validateReimbursementInput({ title: 'x', amount: 0 }),
    ).toBe('amountRequired')
    expect(
      validateReimbursementInput({ title: 'x', amount: -5 }),
    ).toBe('amountRequired')
  })
})
