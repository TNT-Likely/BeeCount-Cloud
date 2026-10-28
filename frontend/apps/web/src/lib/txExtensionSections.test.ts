import { describe, expect, it } from 'vitest'

import { resolveTxExtensionSections } from '@beecount/web-features'

/**
 * 交易扩展区块可见性(纯逻辑)。
 *
 * 背景:TxExtensionEditor 里有一段 `if (!isExpense && !isTransfer) return null`
 * 的早退,导致 0029「归物」/ 0030「关联定期存款」这两个 **为收入写的区块永远
 * 不可能渲染** —— 即交易流水里记一笔利息收入时,拿不到「关联定期存款」下拉。
 * 这两个区块随后改成由本函数统一裁决可见性,单测锁住契约,防止再退化成死代码。
 */
describe('resolveTxExtensionSections', () => {
  it('expense: 折扣 + 合并付款 + 成员分摊 + 归物;无手续费、无关联存款', () => {
    const s = resolveTxExtensionSections('expense', {
      hasItems: true,
      hasMembers: true,
      hasDeposits: true,
    })
    expect(s.discount).toBe(true)
    expect(s.legs).toBe(true)
    expect(s.members).toBe(true)
    expect(s.linkedItem).toBe(true)
    expect(s.fee).toBe(false)
    expect(s.linkedDeposit).toBe(false)
  })

  it('income: 可关联成员分摊 + 定期存款(利息收入)+ 归物;无折扣/手续费', () => {
    const s = resolveTxExtensionSections('income', {
      hasItems: true,
      hasMembers: true,
      hasDeposits: true,
    })
    // 本次修复的核心:利息收入要能挂到定期存款上
    expect(s.linkedDeposit).toBe(true)
    expect(s.linkedItem).toBe(true)
    // 收入同样支持成员分摊(谁的收入算到谁头上)
    expect(s.members).toBe(true)
    expect(s.discount).toBe(false)
    expect(s.legs).toBe(false)
    expect(s.fee).toBe(false)
  })

  it('income 但没有任何存款候选 → 不显示关联存款区块', () => {
    const s = resolveTxExtensionSections('income', { hasDeposits: false })
    expect(s.linkedDeposit).toBe(false)
  })

  it('income 且没有物品候选 → 不显示归物区块', () => {
    const s = resolveTxExtensionSections('income', { hasItems: false })
    expect(s.linkedItem).toBe(false)
  })

  it('transfer: 仅手续费区块', () => {
    const s = resolveTxExtensionSections('transfer', {
      hasItems: true,
      hasMembers: true,
      hasDeposits: true,
    })
    expect(s.fee).toBe(true)
    expect(s.discount).toBe(false)
    expect(s.legs).toBe(false)
    expect(s.members).toBe(false)
    expect(s.linkedItem).toBe(false)
    expect(s.linkedDeposit).toBe(false)
  })

  it('未知 / 缺失类型: 所有区块都不渲染', () => {
    for (const txType of ['other', '', undefined]) {
      const s = resolveTxExtensionSections(txType, {
        hasItems: true,
        hasMembers: true,
        hasDeposits: true,
      })
      expect(s, `txType=${String(txType)}`).toEqual({
        discount: false,
        legs: false,
        members: false,
        fee: false,
        linkedItem: false,
        linkedDeposit: false,
      })
    }
  })

  it('不传候选项时默认为空(不渲染依赖候选的区块)', () => {
    const s = resolveTxExtensionSections('income')
    expect(s.linkedDeposit).toBe(false)
    expect(s.linkedItem).toBe(false)
  })
})
