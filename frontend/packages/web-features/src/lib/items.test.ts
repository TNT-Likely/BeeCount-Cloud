import { describe, expect, it } from 'vitest'

import type { ReadItem } from '@beecount/api-client'

import { groupItemsByStatus, isWarrantyExpired, itemCostParts, warrantyStatusOf } from './items'

function item(overrides: Partial<ReadItem> = {}): ReadItem {
  return {
    id: 'it1',
    name: 'MacBook',
    purchase_date: '2026-01-01',
    purchase_amount: 6000,
    warranty_until: null,
    status: 'in_use',
    disposal_type: null,
    disposal_date: null,
    disposal_amount: null,
    held_days: 100,
    total_cost: 6000,
    daily_cost: 60,
    linked_expense: 0,
    linked_income: 0,
    warranty_status: 'none',
    warranty_days_remaining: null,
    is_disposed: false,
    linked_tx_count: 0,
    last_change_id: 1,
    ...overrides,
  }
}

describe('itemCostParts(归物成本拆分)', () => {
  it('只有购买金额时净成本=购买金额,日均=净成本/持有天数', () => {
    const parts = itemCostParts(item({ purchase_amount: 6000, held_days: 100 }))
    expect(parts.purchase).toBe(6000)
    expect(parts.linked).toBe(0)
    expect(parts.disposalIncome).toBe(0)
    expect(parts.net).toBe(6000)
    expect(parts.daily).toBe(60)
  })

  it('关联交易支出计入成本', () => {
    const parts = itemCostParts(item({ purchase_amount: 6000, linked_expense: 400, held_days: 100 }))
    expect(parts.net).toBe(6400)
    expect(parts.daily).toBe(64)
  })

  it('二手卖出收入抵减成本(净成本);卖得比投入高 → 净成本为负', () => {
    const sold = itemCostParts(
      item({
        purchase_amount: 6000,
        linked_expense: 400,
        disposal_type: 'sold',
        disposal_amount: 3000,
        held_days: 200,
      }),
    )
    expect(sold.disposalIncome).toBe(3000)
    expect(sold.net).toBe(3400)

    const profit = itemCostParts(
      item({
        purchase_amount: 1000,
        disposal_type: 'sold',
        disposal_amount: 1500,
        held_days: 10,
      }),
    )
    expect(profit.net).toBe(-500)
    expect(profit.daily).toBe(-50)
  })

  it('关联交易收入(回收款 / 折价补偿 / 保修理赔等)抵减净成本', () => {
    const parts = itemCostParts(
      item({
        purchase_amount: 6000,
        linked_expense: 400,
        linked_income: 500,
        held_days: 100,
      }),
    )
    expect(parts.linked).toBe(400)
    expect(parts.linkedIncome).toBe(500)
    // 6000 + 400(支出) - 500(收入) = 5900
    expect(parts.net).toBe(5900)
    expect(parts.daily).toBe(59)
  })

  it('报废不认回收款(即使服务端残留了金额)', () => {
    const parts = itemCostParts(
      item({
        purchase_amount: 500,
        disposal_type: 'scrapped',
        disposal_amount: 999,
        held_days: 50,
      }),
    )
    expect(parts.disposalIncome).toBe(0)
    expect(parts.net).toBe(500)
  })

  it('持有天数 0 / 脏数据 → 除数至少 1,不产生 Infinity', () => {
    const parts = itemCostParts(item({ purchase_amount: 100, held_days: 0 }))
    expect(parts.heldDays).toBe(1)
    expect(Number.isFinite(parts.daily)).toBe(true)
    expect(parts.daily).toBe(100)
  })
})

describe('groupItemsByStatus(在用 / 已处置分组)', () => {
  it('按状态分组;已处置不隐藏,只是归档', () => {
    const groups = groupItemsByStatus([
      item({ id: 'a', status: 'in_use', purchase_date: '2026-01-01' }),
      item({ id: 'b', status: 'disposed', is_disposed: true, disposal_date: '2026-05-01' }),
      item({ id: 'c', status: 'in_use', purchase_date: '2026-03-01' }),
    ])
    expect(groups.inUse.map((i) => i.id)).toEqual(['c', 'a']) // 购买日倒序
    expect(groups.disposed.map((i) => i.id)).toEqual(['b'])
  })

  it('is_disposed 为真但 status 字段缺失时也归入已处置', () => {
    const groups = groupItemsByStatus([item({ id: 'x', is_disposed: true })])
    expect(groups.disposed).toHaveLength(1)
    expect(groups.inUse).toHaveLength(0)
  })

  it('空列表不炸', () => {
    expect(groupItemsByStatus([])).toEqual({ inUse: [], disposed: [] })
  })
})

describe('warrantyStatusOf(保修状态)', () => {
  it('过保 ≠ 处置:已过保的物品仍然在用', () => {
    const expired = item({ warranty_status: 'expired', status: 'in_use' })
    expect(warrantyStatusOf(expired)).toBe('expired')
    expect(isWarrantyExpired(expired)).toBe(true)
    expect(expired.status).toBe('in_use')
  })

  it('没记保修 / 脏值 → none', () => {
    expect(warrantyStatusOf(item({ warranty_status: 'none' }))).toBe('none')
    expect(warrantyStatusOf(item({ warranty_status: 'weird' as string }))).toBe('none')
    expect(isWarrantyExpired(item({ warranty_status: 'active' }))).toBe(false)
  })
})
