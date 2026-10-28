import { describe, expect, it } from 'vitest'

import { BANK_ICONS, bankIconOf, filterBankIcons, matchBankIcon } from './bankIcons'

describe('matchBankIcon', () => {
  it('命中主流银行(常见写法)', () => {
    expect(matchBankIcon('工商银行')).toBe('icbc')
    expect(matchBankIcon('招商银行股份有限公司')).toBe('cmb')
    expect(matchBankIcon('中国农业银行')).toBe('abc')
    expect(matchBankIcon('中国建设银行')).toBe('ccb')
    expect(matchBankIcon('中国银行')).toBe('boc')
    expect(matchBankIcon('交通银行')).toBe('bocom')
    expect(matchBankIcon('邮储银行')).toBe('psbc')
  })

  it('英文缩写 / 别名命中', () => {
    expect(matchBankIcon('ICBC')).toBe('icbc')
    expect(matchBankIcon('cmb')).toBe('cmb')
    expect(matchBankIcon('深发展')).toBe('pab')
  })

  it('支付工具命中', () => {
    expect(matchBankIcon('支付宝')).toBe('alipay')
    expect(matchBankIcon('微信支付')).toBe('wechat')
    expect(matchBankIcon('云闪付')).toBe('unionpay')
  })

  it('具体银行优先于通用城商行(上海浦东发展 ≠ 上海银行)', () => {
    expect(matchBankIcon('上海浦东发展银行')).toBe('spdb')
    expect(matchBankIcon('上海银行')).toBe('bosc')
  })

  it('未识别返回 null(交给 UI 提示手动设置)', () => {
    expect(matchBankIcon('某某村镇银行')).toBeNull()
    expect(matchBankIcon('')).toBeNull()
    expect(matchBankIcon(null)).toBeNull()
  })
})

describe('bankIconOf', () => {
  it('注册 key 返回定义', () => {
    expect(bankIconOf('icbc')?.label).toBe('工商银行')
  })
  it('未注册 key / 空 → null', () => {
    expect(bankIconOf('nope')).toBeNull()
    expect(bankIconOf(undefined)).toBeNull()
  })
})

describe('filterBankIcons', () => {
  it('空 query 返回全量', () => {
    expect(filterBankIcons('').length).toBe(BANK_ICONS.length)
  })
  it('按 label / 关键词过滤', () => {
    expect(filterBankIcons('招商').map((b) => b.key)).toEqual(['cmb'])
    expect(filterBankIcons('wechat').map((b) => b.key)).toEqual(['wechat'])
  })
})
