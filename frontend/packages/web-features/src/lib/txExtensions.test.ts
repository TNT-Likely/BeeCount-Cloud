import { describe, expect, it } from 'vitest'

import {
  autoFillLegAmount,
  computeDiscount,
  discountFromWire,
  discountToWire,
  legsFromWire,
  legsRemaining,
  legsToWire,
  legsTotal,
  transferArrivalPreview,
  transferFeeFromWire,
  transferFeeToWire,
  transferSourceDeductionPreview,
} from './txExtensions'

describe('computeDiscount', () => {
  it('percent 模式:8.5 折买 85 块 → 原价 100,优惠 15', () => {
    const r = computeDiscount('percent', '8.5', 85)
    expect(r).toEqual({ originalAmount: 100, discountAmount: 15, finalAmount: 85 })
  })

  it('percent 模式也接受小数输入(0.85)', () => {
    const r = computeDiscount('percent', '0.85', 85)
    expect(r?.originalAmount).toBe(100)
  })

  it('amount 模式:满 100 减 20 买 100 → 原价 120', () => {
    const r = computeDiscount('amount', '20', 100)
    expect(r).toEqual({ originalAmount: 120, discountAmount: 20, finalAmount: 100 })
  })

  it('非法输入返回 null', () => {
    expect(computeDiscount('percent', '0', 85)).toBeNull()
    expect(computeDiscount('percent', '10', 85)).toBeNull() // 10 > 1 视作 100 折,非法
    expect(computeDiscount('amount', '0', 85)).toBeNull()
    expect(computeDiscount('percent', '8.5', 0)).toBeNull()
  })
})

describe('discount form ↔ wire', () => {
  it('round-trip 保持语义', () => {
    const form = { discount_enabled: true, discount_mode: 'percent' as const, discount_value: '8.5', discount_coupon_name: '满100减20' }
    const wire = discountToWire(form, 85)
    expect(wire).toEqual({
      mode: 'percent',
      value: 8.5,
      couponName: '满100减20',
      originalAmount: 100,
      discountAmount: 15,
      finalAmount: 85,
    })
    const back = discountFromWire(wire)
    expect(back.discount_enabled).toBe(true)
    expect(back.discount_mode).toBe('percent')
    expect(back.discount_value).toBe('8.5')
    expect(back.discount_coupon_name).toBe('满100减20')
  })

  it('关闭态不发字段', () => {
    expect(discountToWire({ discount_enabled: false, discount_mode: 'percent', discount_value: '8.5', discount_coupon_name: '' }, 85)).toBeNull()
  })

  it('wire 的 percent value 是小数,回显成"折"数', () => {
    const form = discountFromWire({ mode: 'percent', value: 0.85, finalAmount: 85 })
    expect(form.discount_value).toBe('8.5')
  })
})

describe('payment legs', () => {
  const byName = new Map([['支付宝', 'acc-1'], ['微信', 'acc-2']])

  it('合计等于 amount 才转 wire', () => {
    const legs = [
      { account_name: '支付宝', amount: '60' },
      { account_name: '微信', amount: '40' },
    ]
    const wire = legsToWire(legs, 100, byName)
    expect(wire).toEqual([
      { accountId: 'acc-1', accountName: '支付宝', amount: 60 },
      { accountId: 'acc-2', accountName: '微信', amount: 40 },
    ])
  })

  it('合计不符 → null', () => {
    const legs = [{ account_name: '支付宝', amount: '60' }]
    expect(legsToWire(legs, 100, byName)).toBeNull()
  })

  it('重复账户 → null', () => {
    const legs = [
      { account_name: '支付宝', amount: '50' },
      { account_name: '支付宝', amount: '50' },
    ]
    expect(legsToWire(legs, 100, byName)).toBeNull()
  })

  it('未知账户名 → null', () => {
    const legs = [{ account_name: '现金', amount: '100' }]
    expect(legsToWire(legs, 100, byName)).toBeNull()
  })

  it('legsTotal / legsRemaining / autoFill', () => {
    const legs = [{ account_name: '支付宝', amount: '60' }]
    expect(legsTotal(legs)).toBe(60)
    expect(legsRemaining(legs, 100)).toBe(40)
    expect(autoFillLegAmount(40)).toBe('40')
    expect(autoFillLegAmount(-10)).toBe('0')
  })

  it('wire → form round-trip', () => {
    const form = legsFromWire([
      { accountId: 'acc-1', accountName: '支付宝', amount: 60 },
      { accountId: 'acc-2', amount: 40 },
    ])
    expect(form).toEqual([
      { account_name: '支付宝', amount: '60' },
      { account_name: '', amount: '40' },
    ])
  })
})

describe('transfer fee', () => {
  const byName = new Map([['现金', 'acc-cash']])

  it('deduct_from_target 到账 = amount - fee', () => {
    const form = { transfer_fee_enabled: true, transfer_fee_amount: '5', transfer_fee_mode: 'deduct_from_target' as const, transfer_fee_account_name: '' }
    const wire = transferFeeToWire(form, byName)
    expect(wire).toEqual({ amount: 5, mode: 'deduct_from_target' })
    expect(transferArrivalPreview(1000, wire)).toBe(995)
  })

  it('separate_account 带上手续费账户', () => {
    const form = { transfer_fee_enabled: true, transfer_fee_amount: '5', transfer_fee_mode: 'separate_account' as const, transfer_fee_account_name: '现金' }
    const wire = transferFeeToWire(form, byName)
    expect(wire).toEqual({ amount: 5, mode: 'separate_account', feeAccountId: 'acc-cash', feeAccountName: '现金' })
  })

  it('关闭 / 零额不发字段', () => {
    expect(transferFeeToWire({ transfer_fee_enabled: false, transfer_fee_amount: '5', transfer_fee_mode: 'deduct_from_source', transfer_fee_account_name: '' }, byName)).toBeNull()
    expect(transferFeeToWire({ transfer_fee_enabled: true, transfer_fee_amount: '0', transfer_fee_mode: 'deduct_from_source', transfer_fee_account_name: '' }, byName)).toBeNull()
  })

  it('wire → form round-trip', () => {
    const form = transferFeeFromWire({ amount: 5, mode: 'separate_account', feeAccountId: 'acc-cash', feeAccountName: '现金' })
    expect(form.transfer_fee_enabled).toBe(true)
    expect(form.transfer_fee_amount).toBe('5')
    expect(form.transfer_fee_mode).toBe('separate_account')
    expect(form.transfer_fee_account_name).toBe('现金')
  })

  it('0026 discount:转出方实扣 amount-fee,到账方全收 amount', () => {
    const form = { transfer_fee_enabled: true, transfer_fee_amount: '20', transfer_fee_mode: 'discount' as const, transfer_fee_account_name: '' }
    const wire = transferFeeToWire(form, byName)
    expect(wire).toEqual({ amount: 20, mode: 'discount' })
    // 到账方全收:优惠不影响到账额
    expect(transferArrivalPreview(50, wire)).toBe(50)
    // 转出方实扣 = amount - 优惠 = 50 - 20 = 30
    expect(transferSourceDeductionPreview(50, wire)).toBe(30)
  })

  it('deduct_from_source:实扣 = amount + fee', () => {
    const wire = { amount: 5, mode: 'deduct_from_source' as const }
    expect(transferSourceDeductionPreview(1000, wire)).toBe(1005)
    // 无手续费时实扣 = amount
    expect(transferSourceDeductionPreview(1000, null)).toBe(1000)
  })
})
