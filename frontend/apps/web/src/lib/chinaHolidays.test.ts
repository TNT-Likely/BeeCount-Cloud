import { describe, expect, it } from 'vitest'

import {
  CHINA_HOLIDAY_YEARS,
  getChinaHoliday,
} from './chinaHolidays'

/**
 * 数据来源(多源核对):
 *  - 2025 年:国办发明电〔2024〕12 号(2024-11-12)
 *  - 2026 年:国办发明电〔2025〕7 号(2025-11-04)
 */

describe('chinaHolidays 2026', () => {
  it('元旦 1/1-1/3 放假,1/4 周日补班', () => {
    for (const day of ['01', '02', '03']) {
      expect(getChinaHoliday(`2026-01-${day}`)).toEqual({
        nameKey: 'newYear',
        kind: 'holiday',
      })
    }
    expect(getChinaHoliday('2026-01-04')).toEqual({
      nameKey: 'makeupWork',
      kind: 'workday',
    })
  })

  it('春节 2/15-2/23 整天命中,2/14 与 2/28 补班', () => {
    const days = Array.from({ length: 9 }, (_, i) => `2026-02-${String(15 + i).padStart(2, '0')}`)
    for (const day of days) {
      expect(getChinaHoliday(day)?.kind).toBe('holiday')
      expect(getChinaHoliday(day)?.nameKey).toBe('springFestival')
    }
    expect(getChinaHoliday('2026-02-14')?.kind).toBe('workday')
    expect(getChinaHoliday('2026-02-28')?.kind).toBe('workday')
  })

  it('清明 4/4-4/6、端午 6/19-6/21、中秋 9/25-9/27 均无调休', () => {
    expect(getChinaHoliday('2026-04-05')?.nameKey).toBe('qingming')
    expect(getChinaHoliday('2026-06-20')?.nameKey).toBe('dragonBoat')
    expect(getChinaHoliday('2026-09-26')?.nameKey).toBe('midAutumn')
    // 三个假期都不带补班日
    for (const day of ['2026-04-07', '2026-06-22', '2026-09-28']) {
      expect(getChinaHoliday(day)).toBeNull()
    }
  })

  it('劳动节 5/1-5/5,国庆 10/1-10/7,补班 5/9、9/20、10/10', () => {
    expect(getChinaHoliday('2026-05-03')?.nameKey).toBe('labourDay')
    expect(getChinaHoliday('2026-10-07')?.nameKey).toBe('nationalDay')
    expect(getChinaHoliday('2026-05-09')?.kind).toBe('workday')
    expect(getChinaHoliday('2026-09-20')?.kind).toBe('workday')
    expect(getChinaHoliday('2026-10-10')?.kind).toBe('workday')
  })

  it('普通工作日 / 普通周末都返回 null', () => {
    expect(getChinaHoliday('2026-03-10')).toBeNull()
    expect(getChinaHoliday('2026-03-14')).toBeNull() // 普通周六
    expect(getChinaHoliday('2026-11-11')).toBeNull()
  })
})

describe('chinaHolidays 2025', () => {
  it('元旦仅 1/1 一天', () => {
    expect(getChinaHoliday('2025-01-01')?.nameKey).toBe('newYear')
    expect(getChinaHoliday('2025-01-02')).toBeNull()
  })

  it('春节 1/28-2/4,补班 1/26、2/8', () => {
    expect(getChinaHoliday('2025-01-28')?.nameKey).toBe('springFestival')
    expect(getChinaHoliday('2025-02-04')?.nameKey).toBe('springFestival')
    expect(getChinaHoliday('2025-01-26')?.kind).toBe('workday')
    expect(getChinaHoliday('2025-02-08')?.kind).toBe('workday')
  })

  it('国庆中秋合并 10/1-10/8,补班 9/28、10/11', () => {
    expect(getChinaHoliday('2025-10-01')?.nameKey).toBe('nationalDay')
    expect(getChinaHoliday('2025-10-08')?.nameKey).toBe('nationalDay')
    expect(getChinaHoliday('2025-09-28')?.kind).toBe('workday')
    expect(getChinaHoliday('2025-10-11')?.kind).toBe('workday')
  })

  it('劳动节 5/1-5/5,补班 4/27', () => {
    expect(getChinaHoliday('2025-05-01')?.nameKey).toBe('labourDay')
    expect(getChinaHoliday('2025-05-05')?.nameKey).toBe('labourDay')
    expect(getChinaHoliday('2025-04-27')?.kind).toBe('workday')
  })
})

describe('chinaHolidays fallback 与边界', () => {
  it('未收录年份走公历固定节日(元旦/劳动节/国庆头三天)', () => {
    expect(getChinaHoliday('2028-01-01')?.nameKey).toBe('newYear')
    expect(getChinaHoliday('2030-05-01')?.nameKey).toBe('labourDay')
    expect(getChinaHoliday('2027-10-02')?.nameKey).toBe('nationalDay')
    // 农历节日无权威表不猜
    expect(getChinaHoliday('2027-02-17')).toBeNull()
  })

  it('已收录年份不吃 fallback(2/14 情人节不是节日)', () => {
    expect(getChinaHoliday('2026-02-14')?.kind).toBe('workday') // 2026 是补班
    expect(getChinaHoliday('2025-02-14')).toBeNull()
  })

  it('非法 / 空输入返回 null', () => {
    expect(getChinaHoliday('')).toBeNull()
    expect(getChinaHoliday('2026-1-1')).toBeNull()
    expect(getChinaHoliday('not-a-date')).toBeNull()
    expect(getChinaHoliday('2026-13-45')).toBeNull()
  })

  it('内置年份覆盖 2025 + 2026', () => {
    expect(CHINA_HOLIDAY_YEARS).toEqual([2025, 2026])
  })
})
