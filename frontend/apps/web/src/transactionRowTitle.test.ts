import { describe, expect, it } from 'vitest'

import { composeTransactionRowTitle } from '@beecount/web-features'

describe('composeTransactionRowTitle', () => {
  it('category 默认:分类为主 + 备注括号', () => {
    expect(composeTransactionRowTitle({
      mode: 'category', categoryName: '餐饮', categoryText: '餐饮', note: '午餐',
    })).toEqual({ primary: '餐饮', parenNote: '午餐' })
  })

  it('category:无备注无括号', () => {
    expect(composeTransactionRowTitle({
      mode: 'category', categoryName: '餐饮', categoryText: '餐饮', note: '',
    })).toEqual({ primary: '餐饮', parenNote: null })
  })

  it('note:有备注 → 纯备注,无括号', () => {
    expect(composeTransactionRowTitle({
      mode: 'note', categoryName: '餐饮', categoryText: '餐饮', note: '午餐',
    })).toEqual({ primary: '午餐', parenNote: null })
  })

  it('note:无备注 → 退回分类名', () => {
    expect(composeTransactionRowTitle({
      mode: 'note', categoryName: '餐饮', categoryText: '餐饮', note: '',
    })).toEqual({ primary: '餐饮', parenNote: null })
  })

  it('转账(无 category_name):note 模式不变,categoryText 为主 + 备注括号', () => {
    expect(composeTransactionRowTitle({
      mode: 'note', categoryName: null, categoryText: '转账', note: '房租',
    })).toEqual({ primary: '转账', parenNote: '房租' })
  })

  it('note 传 null 当作无备注', () => {
    expect(composeTransactionRowTitle({
      mode: 'note', categoryName: '餐饮', categoryText: '餐饮', note: null,
    })).toEqual({ primary: '餐饮', parenNote: null })
  })
})
