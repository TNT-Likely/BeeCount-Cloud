import { describe, expect, it } from 'vitest'

import { buildMemberSplits, memberSplitsValid, txDefaults } from '@beecount/web-features'

/** 成员分摊（0024）表单 → 写接口 payload 的口径回归。 */
describe('member splits form helpers', () => {
  it('single member uses the full transaction amount', () => {
    const form = { ...txDefaults(), tx_type: 'expense' as const, amount: '38.5', member_id: 'mem_1' }
    expect(buildMemberSplits(form, new Map([['mem_1', '张三']]))).toEqual([
      { member_id: 'mem_1', name: '张三', amount: 38.5 },
    ])
  })

  it('transfer always clears members', () => {
    const form = {
      ...txDefaults(),
      tx_type: 'transfer' as const,
      amount: '100',
      member_id: 'mem_1',
      split_enabled: true,
      member_splits: [{ member_id: 'mem_1', name: '张三', amount: '100' }],
    }
    expect(buildMemberSplits(form)).toEqual([])
  })

  it('multi-member split keeps each amount and rejects blank rows', () => {
    const form = {
      ...txDefaults(),
      tx_type: 'expense' as const,
      amount: '100',
      split_enabled: true,
      member_splits: [
        { member_id: 'mem_1', name: '张三', amount: '60' },
        { member_id: '', name: '', amount: '40' },
      ],
    }
    expect(buildMemberSplits(form)).toEqual([
      { member_id: 'mem_1', name: '张三', amount: 60 },
    ])
  })

  it('accepts a three-way even split of 100 (33.33 x 3 = 99.99)', () => {
    const splits = [
      { amount: 33.33 },
      { amount: 33.33 },
      { amount: 33.33 },
    ]
    expect(memberSplitsValid(splits, 100)).toBe(true)
  })

  it('rejects a split whose sum is genuinely off', () => {
    expect(memberSplitsValid([{ amount: 50 }, { amount: 30 }], 100)).toBe(false)
  })
})
