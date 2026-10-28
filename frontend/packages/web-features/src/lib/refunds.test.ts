import { describe, expect, it } from 'vitest'

import {
  isFullyRefundedFromSplit,
  isFullyRefundedToCard,
  matchRefundTxCandidates,
  netExpenseAmount,
  nextRefundStatus,
  refundCardSplit,
  refundCardSplitFromList,
  refundDetailBreakdown,
  refundProgress,
  refundTxLabel,
  validateRefundInput,
} from './refunds'

describe('nextRefundStatus', () => {
  it('pending 推进到 received', () => {
    expect(nextRefundStatus('pending')).toBe('received')
  })

  it('received / cancelled 是终态', () => {
    expect(nextRefundStatus('received')).toBeNull()
    expect(nextRefundStatus('cancelled')).toBeNull()
  })
})

describe('refundProgress', () => {
  it('未退款', () => {
    expect(refundProgress(500, 0)).toEqual({ ratio: 0, state: 'none' })
  })

  it('部分退款按比例', () => {
    const p = refundProgress(500, 200)
    expect(p.ratio).toBeCloseTo(0.4)
    expect(p.state).toBe('partial')
  })

  it('退满(含浮点容差)', () => {
    expect(refundProgress(100, 100).state).toBe('full')
    expect(refundProgress(100, 99.995).state).toBe('full')
    // 99.98 < 100 - 0.011 = 99.989 → 仍算部分退
    expect(refundProgress(100, 99.98).state).toBe('partial')
  })

  it('超额钳到 1', () => {
    expect(refundProgress(100, 120).ratio).toBe(1)
  })

  it('原单金额缺失时无进度', () => {
    expect(refundProgress(null, 50)).toEqual({ ratio: 0, state: 'none' })
    expect(refundProgress(0, 50)).toEqual({ ratio: 0, state: 'none' })
  })
})

describe('netExpenseAmount', () => {
  it('无退款 → 原额', () => {
    expect(netExpenseAmount({ amount: 500 })).toBe(500)
    expect(netExpenseAmount({ amount: 500, refunded_total: 0 })).toBe(500)
  })

  it('部分退款 → 扣掉已退部分', () => {
    expect(netExpenseAmount({ amount: 500, refunded_total: 200 })).toBe(300)
  })

  it('全额退款 → 0', () => {
    expect(netExpenseAmount({ amount: 100, refunded_total: 100 })).toBe(0)
  })

  it('脏数据超退钳 0,不产生负支出', () => {
    expect(netExpenseAmount({ amount: 100, refunded_total: 120 })).toBe(0)
  })

  it('老服务端缺 refunded_total → 按 0 扣(退化原额)', () => {
    expect(netExpenseAmount({ amount: 88.5, refunded_total: undefined })).toBe(88.5)
    expect(netExpenseAmount({ amount: 88.5, refunded_total: null })).toBe(88.5)
  })

  it('金额非法/缺失 → 0', () => {
    expect(netExpenseAmount({ amount: null, refunded_total: 10 })).toBe(0)
    expect(netExpenseAmount({ amount: 'abc', refunded_total: 10 })).toBe(0)
  })

  it('全局口径:跨卡退款也扣(钱已退回用户,支出未真实发生)', () => {
    // 与 isFullyRefundedToCard 的卡维度分工:统计聚合扣全部退款,
    // 删除线/账户页才看 refunded_to_account。
    expect(netExpenseAmount({ amount: 100, refunded_total: 100 })).toBe(0)
  })
})

describe('validateRefundInput', () => {
  it('合法输入通过', () => {
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 100, remainingRefundable: 300 }),
    ).toBeNull()
  })

  it('缺原单', () => {
    expect(
      validateRefundInput({ originalTxId: '', amount: 100, remainingRefundable: 300 }),
    ).toBe('txRequired')
  })

  it('金额必须为正', () => {
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 0, remainingRefundable: 300 }),
    ).toBe('amountRequired')
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: -5, remainingRefundable: 300 }),
    ).toBe('amountRequired')
  })

  it('超额拒绝(容差 0.011)', () => {
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 100.02, remainingRefundable: 100 }),
    ).toBe('overRefund')
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 100.01, remainingRefundable: 100 }),
    ).toBeNull()
  })

  it('编辑时把本笔加回额度后可原额保存', () => {
    // 已退 100/500,编辑那笔 100 → remaining+100 = 400,保存 100 合法
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 100, remainingRefundable: 400 }),
    ).toBeNull()
    // 但改成 500 仍超额
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 500, remainingRefundable: 400 }),
    ).toBe('overRefund')
  })

  it('remaining 缺失(原单已删)时只校验正数', () => {
    expect(
      validateRefundInput({ originalTxId: 'tx-1', amount: 9999, remainingRefundable: null }),
    ).toBeNull()
  })
})

describe('matchRefundTxCandidates', () => {
  const txs = [
    { id: 'tx-1', tx_type: 'expense', amount: 100, note: '键盘', category_name: '数码', happened_at: '2026-10-01T00:00:00Z' },
    { id: 'tx-2', tx_type: 'income', amount: 50, note: '报销', category_name: '其他', happened_at: '2026-10-02T00:00:00Z' },
    { id: 'tx-3', tx_type: 'expense', amount: 200, note: null, category_name: '餐饮', happened_at: '2026-10-03T00:00:00Z' },
    { id: 'tx-4', tx_type: 'transfer', amount: 9, note: null, category_name: null, happened_at: '2026-10-04T00:00:00Z' },
  ]

  it('只列支出交易,日期新的在前', () => {
    const out = matchRefundTxCandidates(txs, '')
    expect(out.map((t) => t.id)).toEqual(['tx-3', 'tx-1'])
  })

  it('按备注/分类/金额模糊匹配', () => {
    expect(matchRefundTxCandidates(txs, '键盘').map((t) => t.id)).toEqual(['tx-1'])
    expect(matchRefundTxCandidates(txs, '餐饮').map((t) => t.id)).toEqual(['tx-3'])
    expect(matchRefundTxCandidates(txs, '200').map((t) => t.id)).toEqual(['tx-3'])
  })

  it('limit 截断', () => {
    expect(matchRefundTxCandidates(txs, '', 1).map((t) => t.id)).toEqual(['tx-3'])
  })

  it('不修改入参', () => {
    const before = txs.map((t) => t.id)
    matchRefundTxCandidates(txs, '')
    expect(txs.map((t) => t.id)).toEqual(before)
  })
})

describe('refundTxLabel', () => {
  it('日期 + 分类 + 金额', () => {
    expect(
      refundTxLabel({ amount: 100, category_name: '数码', happened_at: '2026-10-01T00:00:00Z' }),
    ).toBe('10-01 数码 ¥100.00')
  })

  it('备注追加在尾部', () => {
    expect(
      refundTxLabel({ amount: 12.5, category_name: null, note: '书', happened_at: '2026-01-05T00:00:00Z' }),
    ).toBe('01-05 — ¥12.50 书')
  })
})

describe('isFullyRefundedToCard(卡维度删除线)', () => {
  it('同卡全额退款 → 画删除线', () => {
    expect(
      isFullyRefundedToCard({ amount: 100, refunded_total: 100, refunded_to_account: 100 }),
    ).toBe(true)
  })

  it('同卡部分退款 → 不画', () => {
    expect(
      isFullyRefundedToCard({ amount: 100, refunded_total: 40, refunded_to_account: 40 }),
    ).toBe(false)
  })

  it('跨卡全额退款(钱到别卡,本卡支出仍成立)→ 不画', () => {
    expect(
      isFullyRefundedToCard({ amount: 100, refunded_total: 100, refunded_to_account: 0 }),
    ).toBe(false)
  })

  it('分别退款混合:仅本卡部分 ≥ 原单才画', () => {
    // 30 回本卡 + 70 去别卡:本卡只回 30,不画
    expect(
      isFullyRefundedToCard({ amount: 100, refunded_total: 100, refunded_to_account: 30 }),
    ).toBe(false)
    // 两笔都退回到本卡,累计 100:画
    expect(
      isFullyRefundedToCard({ amount: 100, refunded_total: 100, refunded_to_account: 100 }),
    ).toBe(true)
  })

  it('容差 0.011 内算全额(浮点累计误差)', () => {
    expect(
      isFullyRefundedToCard({ amount: 100, refunded_total: 100, refunded_to_account: 99.995 }),
    ).toBe(true)
  })

  it('老服务端缺 refunded_to_account → 退化总口径', () => {
    expect(isFullyRefundedToCard({ amount: 80, refunded_total: 80 })).toBe(true)
    expect(isFullyRefundedToCard({ amount: 80, refunded_total: 0 })).toBe(false)
  })
})

describe('isFullyRefundedFromSplit(交易详情置灰+删除线)', () => {
  it('退回本卡累计 ≥ 原单 → 置灰画线', () => {
    expect(
      isFullyRefundedFromSplit(100, { total: 100, backToCard: 100, toOtherCards: 0 }),
    ).toBe(true)
  })

  it('部分退回本卡 → 不置灰', () => {
    expect(
      isFullyRefundedFromSplit(100, { total: 100, backToCard: 40, toOtherCards: 60 }),
    ).toBe(false)
  })

  it('全退到别卡(本卡支出仍成立)→ 不置灰', () => {
    expect(
      isFullyRefundedFromSplit(100, { total: 100, backToCard: 0, toOtherCards: 100 }),
    ).toBe(false)
  })

  it('容差 0.011 内算全额', () => {
    expect(
      isFullyRefundedFromSplit(100, { total: 100, backToCard: 99.995, toOtherCards: 0.005 }),
    ).toBe(true)
  })

  it('原单金额缺失 / 为 0 → 不误判', () => {
    expect(
      isFullyRefundedFromSplit(0, { total: 0, backToCard: 0, toOtherCards: 0 }),
    ).toBe(false)
    expect(
      isFullyRefundedFromSplit(null, { total: 0, backToCard: 0, toOtherCards: 0 }),
    ).toBe(false)
  })

  it('与 refundCardSplitFromList 现算口径一致(跨卡 split 退款)', () => {
    // 100 分两腿:30 回本卡、70 去别卡 → 详情不置灰,跟流水行结论相同
    const split = refundCardSplitFromList(
      [
        {
          amount: 100,
          status: 'received',
          refund_mode: 'split',
          refund_legs: [
            { accountId: 'a1', amount: 30 },
            { accountId: 'a2', amount: 70 },
          ],
        },
      ],
      'a1',
    )
    expect(split.backToCard).toBe(30)
    expect(isFullyRefundedFromSplit(100, split)).toBe(false)
  })
})

describe('refundCardSplit(服务端口径分解)', () => {
  it('全回本卡 → toOtherCards 0', () => {
    expect(refundCardSplit({ refunded_total: 100, refunded_to_account: 100 })).toEqual({
      total: 100,
      backToCard: 100,
      toOtherCards: 0,
    })
  })

  it('跨卡全退 → backToCard 0', () => {
    expect(refundCardSplit({ refunded_total: 100, refunded_to_account: 0 })).toEqual({
      total: 100,
      backToCard: 0,
      toOtherCards: 100,
    })
  })

  it('混合 → 两部分相加等于 total', () => {
    const s = refundCardSplit({ refunded_total: 100, refunded_to_account: 30 })
    expect(s).toEqual({ total: 100, backToCard: 30, toOtherCards: 70 })
  })

  it('缺省 refunded_to_account → 按全回本卡计', () => {
    expect(refundCardSplit({ refunded_total: 60 })).toEqual({
      total: 60,
      backToCard: 60,
      toOtherCards: 0,
    })
  })
})

describe('refundCardSplitFromList(退款记录现算)', () => {
  const leg = (accountId: string, amount: number) => ({ accountId, amount })

  it('unified 退到本卡 → 全计 backToCard', () => {
    const s = refundCardSplitFromList(
      [{ amount: 100, status: 'received', account_id: 'acc-1', refund_mode: 'unified' }],
      'acc-1',
    )
    expect(s).toEqual({ total: 100, backToCard: 100, toOtherCards: 0 })
  })

  it('unified 退到别卡 → 全计 toOtherCards', () => {
    const s = refundCardSplitFromList(
      [{ amount: 100, status: 'received', account_id: 'acc-2', refund_mode: 'unified' }],
      'acc-1',
    )
    expect(s).toEqual({ total: 100, backToCard: 0, toOtherCards: 100 })
  })

  it('split 按腿匹配:本卡那部分才计 backToCard', () => {
    const s = refundCardSplitFromList(
      [
        {
          amount: 100,
          status: 'received',
          refund_mode: 'split',
          refund_legs: [leg('acc-1', 30), leg('acc-2', 70)],
        },
      ],
      'acc-1',
    )
    expect(s).toEqual({ total: 100, backToCard: 30, toOtherCards: 70 })
  })

  it('cancelled 不计入;pending(未选账户)不计 backToCard', () => {
    const s = refundCardSplitFromList(
      [
        { amount: 50, status: 'cancelled', account_id: 'acc-1', refund_mode: 'unified' },
        { amount: 20, status: 'pending', account_id: null, refund_mode: 'unified' },
        { amount: 30, status: 'received', account_id: 'acc-1', refund_mode: 'unified' },
      ],
      'acc-1',
    )
    expect(s).toEqual({ total: 50, backToCard: 30, toOtherCards: 20 })
  })

  it('原单无账户(脏数据)→ 不误报,整体按本卡计', () => {
    const s = refundCardSplitFromList(
      [{ amount: 40, status: 'received', account_id: 'acc-2', refund_mode: 'unified' }],
      null,
    )
    expect(s).toEqual({ total: 40, backToCard: 40, toOtherCards: 0 })
  })
})

describe('refundDetailBreakdown(交易详情退款明细弹窗)', () => {
  it('按退款日期正序,多次部分退按时间线排', () => {
    const b = refundDetailBreakdown(
      [
        { id: 'b', happened_at: '2026-03-02', amount: 30, status: 'received' },
        { id: 'a', happened_at: '2026-03-01', amount: 20, status: 'pending' },
      ],
      100,
    )
    expect(b.ordered.map((r) => r.id)).toEqual(['a', 'b'])
    expect(b.refunded).toBe(50)
    expect(b.remaining).toBe(50)
    expect(b.ratio).toBeCloseTo(0.5)
  })

  it('cancelled 仍在列表里但不计入已退', () => {
    const b = refundDetailBreakdown(
      [
        { id: 'a', happened_at: '2026-03-01', amount: 80, status: 'cancelled' },
        { id: 'b', happened_at: '2026-03-02', amount: 20, status: 'received' },
      ],
      100,
    )
    expect(b.ordered).toHaveLength(2)
    expect(b.refunded).toBe(20)
    expect(b.remaining).toBe(80)
  })

  it('超退 / 原单非正 → remaining 钳 0,ratio 不超过 1', () => {
    const over = refundDetailBreakdown(
      [{ id: 'a', happened_at: '2026-03-01', amount: 120, status: 'received' }],
      100,
    )
    expect(over.remaining).toBe(0)
    expect(over.ratio).toBe(1)
    const zero = refundDetailBreakdown(
      [{ id: 'a', happened_at: '2026-03-01', amount: 10, status: 'received' }],
      0,
    )
    expect(zero).toEqual({ ordered: zero.ordered, refunded: 10, remaining: 0, ratio: 0 })
  })

  it('空列表 / 缺日期不炸', () => {
    const empty = refundDetailBreakdown([], 100)
    expect(empty).toEqual({ ordered: [], refunded: 0, remaining: 100, ratio: 0 })
    const noDate = refundDetailBreakdown(
      [{ id: 'a', amount: 10, status: 'received' }],
      100,
    )
    expect(noDate.refunded).toBe(10)
  })
})
