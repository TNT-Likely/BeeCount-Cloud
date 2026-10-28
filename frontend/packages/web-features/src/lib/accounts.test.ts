import { describe, expect, it } from 'vitest'

import {
  accountDisplayName,
  clampDayToMonth,
  formatMonthDay,
  hasAlias,
  statementPeriod,
} from './accounts'

describe('accountDisplayName / hasAlias', () => {
  it('有别名时展示别名', () => {
    expect(accountDisplayName({ name: '招商银行信用卡(1234)', alias: '招行卡' })).toBe('招行卡')
  })

  it('别名为空 / 只有空白时回退账户名', () => {
    expect(accountDisplayName({ name: '现金', alias: '' })).toBe('现金')
    expect(accountDisplayName({ name: '现金', alias: '   ' })).toBe('现金')
    expect(accountDisplayName({ name: '现金', alias: null })).toBe('现金')
  })

  it('账户对象缺失时返回空串', () => {
    expect(accountDisplayName(null)).toBe('')
    expect(accountDisplayName(undefined)).toBe('')
  })

  it('hasAlias: 只有设了且与账户名不同才为 true', () => {
    expect(hasAlias({ name: 'A', alias: 'B' })).toBe(true)
    expect(hasAlias({ name: 'A', alias: 'A' })).toBe(false)
    expect(hasAlias({ name: 'A', alias: '' })).toBe(false)
    expect(hasAlias({ name: 'A' })).toBe(false)
    expect(hasAlias(null)).toBe(false)
  })
})

describe('clampDayToMonth', () => {
  it('31 号在 2 月夹到月末', () => {
    expect(clampDayToMonth(2026, 2, 31)).toEqual(new Date(2026, 1, 28))
    expect(clampDayToMonth(2024, 2, 31)).toEqual(new Date(2024, 1, 29)) // 闰年
  })

  it('正常日期不动;非法日期夹到 1 号', () => {
    expect(clampDayToMonth(2026, 3, 15)).toEqual(new Date(2026, 2, 15))
    expect(clampDayToMonth(2026, 3, 0)).toEqual(new Date(2026, 2, 1))
  })
})

describe('statementPeriod', () => {
  it('账单日未设置 → null', () => {
    expect(statementPeriod(null, 25, true, new Date(2026, 9, 20))).toBeNull()
    expect(statementPeriod(undefined, 25, true, new Date(2026, 9, 20))).toBeNull()
    expect(statementPeriod(0, 25, true, new Date(2026, 9, 20))).toBeNull()
    expect(statementPeriod(32, 25, true, new Date(2026, 9, 20))).toBeNull()
  })

  it('计入当期:账单日之后的消费落在「本月账单日+1 ~ 次月账单日」', () => {
    const p = statementPeriod(5, 25, true, new Date(2026, 9, 20))!
    expect(formatMonthDay(p.start)).toBe('10-06')
    expect(formatMonthDay(p.end)).toBe('11-05')
    // 还款日落在账单区间结束日的次月
    expect(p.due).toEqual(new Date(2026, 11, 25))
  })

  it('计入当期:账单日之前 → 还在上一期账单', () => {
    const p = statementPeriod(5, 25, true, new Date(2026, 9, 3))!
    expect(formatMonthDay(p.start)).toBe('09-06')
    expect(formatMonthDay(p.end)).toBe('10-05')
  })

  it('计入下期:账单日当天是本期第一天', () => {
    const p = statementPeriod(5, 25, false, new Date(2026, 9, 20))!
    expect(formatMonthDay(p.start)).toBe('10-05')
    expect(formatMonthDay(p.end)).toBe('11-04')
  })

  it('计入下期:未到账单日 → 上一期从账单日开始', () => {
    const p = statementPeriod(5, 25, false, new Date(2026, 9, 3))!
    expect(formatMonthDay(p.start)).toBe('09-05')
    expect(formatMonthDay(p.end)).toBe('10-04')
  })

  it('跨年:12 月的账单日把区间推到次年 1 月', () => {
    const p = statementPeriod(10, 5, true, new Date(2026, 11, 20))!
    expect(formatMonthDay(p.start)).toBe('12-11')
    expect(formatMonthDay(p.end)).toBe('01-10')
    expect(p.due).toEqual(new Date(2027, 1, 5))
    expect(p.monthKey).toBe('2027-01')
  })

  it('账单日 31 号:小月夹到月末,区间仍连续', () => {
    const p = statementPeriod(31, null, true, new Date(2026, 1, 10))!
    expect(formatMonthDay(p.start)).toBe('02-01')
    expect(formatMonthDay(p.end)).toBe('02-28')
    expect(p.due).toBeNull()
    expect(p.daysToDue).toBeNull()
  })

  it('两种口径在同一天得到的区间边界差一天', () => {
    const ref = new Date(2026, 9, 20)
    const cur = statementPeriod(5, null, true, ref)!
    const next = statementPeriod(5, null, false, ref)!
    // 计入下期时区间整体前移一天开始,结束也前移一天
    expect(next.start.getTime()).toBe(cur.start.getTime() - 86400000)
    expect(next.end.getTime()).toBe(cur.end.getTime() - 86400000)
  })

  it('缺省 billing_day_in_current(undefined) 按计入当期处理', () => {
    const p = statementPeriod(5, null, undefined, new Date(2026, 9, 20))!
    expect(formatMonthDay(p.end)).toBe('11-05')
  })
})
