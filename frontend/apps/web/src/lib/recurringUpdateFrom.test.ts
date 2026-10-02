import { describe, expect, it } from 'vitest'

import { buildRecurringUpdateFromPayload } from './recurringUpdateFrom'

describe('buildRecurringUpdateFromPayload', () => {
  it('清空的專案/備註/商家/標籤/手續費會明確送 null/[],不是省略', () => {
    const out = buildRecurringUpdateFromPayload({
      txPayload: { tx_type: 'expense', amount: 100, note: null, merchant: null, project_id: null },
      categoryId: 'c1',
      accountId: 'a1',
      tagIds: [],
    })
    expect(out.project_id).toBeNull()
    expect(out.note).toBeNull()
    expect(out.merchant).toBeNull()
    expect(out.tag_ids).toEqual([])
    expect(out.reward_rule_ids).toEqual([])
    expect(out.fee_amount).toBeNull()
    expect(out.base_amount).toBeNull()
    expect(out.discount_amount).toBeNull()
  })

  it('新增專案、手續費與回饋會一併送出', () => {
    const out = buildRecurringUpdateFromPayload({
      txPayload: {
        tx_type: 'expense',
        amount: 105,
        base_amount: 100,
        fee_amount: 5,
        fee_label: 'f',
        discount_amount: 0,
        discount_label: null,
        project_id: 'p1',
        reward_rule_ids: ['r1'],
      },
      categoryId: 'c1',
      accountId: 'a1',
      tagIds: ['t1'],
    })
    expect(out.project_id).toBe('p1')
    expect(out.base_amount).toBe(100)
    expect(out.fee_amount).toBe(5)
    expect(out.fee_label).toBe('f')
    expect(out.reward_rule_ids).toEqual(['r1'])
    expect(out.tag_ids).toEqual(['t1'])
  })

  it('轉帳不送專案與手續費鍵(server 對轉帳規則會 400)', () => {
    const out = buildRecurringUpdateFromPayload({
      txPayload: { tx_type: 'transfer', amount: 10, project_id: 'p1' },
      fromAccountId: 'a1',
      toAccountId: 'a2',
      tagIds: [],
    })
    expect(out.project_id).toBeUndefined()
    expect('fee_amount' in out).toBe(false)
    expect(out.from_account_id).toBe('a1')
    expect(out.account_id).toBeUndefined()
  })
})
