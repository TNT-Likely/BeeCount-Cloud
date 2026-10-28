import { describe, expect, it } from 'vitest'

import {
  buildMonthGrid,
  daysInMonth,
  isSameMonth,
  parseMonth,
  sameDayInMonth,
  shiftMonth,
} from './calendarMonth'

describe('shiftMonth', () => {
  it('跨年前后翻月', () => {
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
    expect(shiftMonth('2025-12', 1)).toBe('2026-01')
  })

  it('同年内翻月', () => {
    expect(shiftMonth('2026-10', -1)).toBe('2026-09')
    expect(shiftMonth('2026-10', 1)).toBe('2026-11')
  })
})

describe('daysInMonth', () => {
  it('大小月 / 闰年 2 月', () => {
    expect(daysInMonth('2026-01')).toBe(31)
    expect(daysInMonth('2026-04')).toBe(30)
    expect(daysInMonth('2024-02')).toBe(29)
    expect(daysInMonth('2026-02')).toBe(28)
  })
})

describe('sameDayInMonth(翻月时选中日跟随)', () => {
  it('目标月有这一天 → 同日', () => {
    expect(sameDayInMonth('2026-09', '2026-10-26')).toBe('2026-09-26')
  })

  it('31 号 → 小月取月末,不跨到下月', () => {
    expect(sameDayInMonth('2026-09', '2026-10-31')).toBe('2026-09-30')
    expect(sameDayInMonth('2026-02', '2026-01-31')).toBe('2026-02-28')
    expect(sameDayInMonth('2024-02', '2026-01-31')).toBe('2024-02-29')
  })

  it('跨年翻月也落在目标月内', () => {
    expect(sameDayInMonth('2025-12', '2026-01-15')).toBe('2025-12-15')
    expect(sameDayInMonth('2026-01', '2025-12-15')).toBe('2026-01-15')
  })

  it('dateKey 缺失/非法 → 回退到目标月 1 号', () => {
    expect(sameDayInMonth('2026-09', null)).toBe('2026-09-01')
    expect(sameDayInMonth('2026-09', '')).toBe('2026-09-01')
    expect(sameDayInMonth('2026-09', 'bad')).toBe('2026-09-01')
  })
})

describe('buildMonthGrid', () => {
  it('恒 42 格,首格按周起始对齐', () => {
    // 2026-10-01 是周四;周一开头 → 前面补 3 天(9/28、9/29、9/30)
    const mon = buildMonthGrid('2026-10', 1)
    expect(mon.dateKeys).toHaveLength(42)
    expect(mon.dateKeys[0]).toBe('2026-09-28')
    expect(mon.dateKeys[3]).toBe('2026-10-01')
    expect(mon.firstOfMonth).toBe('2026-10-01')
    expect(mon.lastOfMonth).toBe('2026-10-31')

    // 周日开头 → 周四前面补 4 天
    const sun = buildMonthGrid('2026-10', 0)
    expect(sun.dateKeys).toHaveLength(42)
    expect(sun.dateKeys[0]).toBe('2026-09-27')
    expect(sun.dateKeys[4]).toBe('2026-10-01')
  })

  it('2 月闰年 / 跨年补尾不越界', () => {
    const grid = buildMonthGrid('2024-02', 1)
    expect(grid.firstOfMonth).toBe('2024-02-01')
    expect(grid.lastOfMonth).toBe('2024-02-29')
    expect(grid.dateKeys).toHaveLength(42)
    // 末格必须连续递增,不能出现 2024-02-30 这类非法日
    expect(grid.dateKeys[grid.dateKeys.length - 1]).toBe('2024-03-10')
  })

  it('月末边界:12 月补到次年 1 月', () => {
    const grid = buildMonthGrid('2026-12', 1)
    expect(grid.lastOfMonth).toBe('2026-12-31')
    expect(grid.dateKeys[grid.dateKeys.length - 1]).toBe('2027-01-10')
  })
})

describe('isSameMonth / parseMonth', () => {
  it('按前缀判断,不误判同前缀的日', () => {
    expect(isSameMonth('2026-10-26', '2026-10')).toBe(true)
    expect(isSameMonth('2026-09-26', '2026-10')).toBe(false)
    // '2026-1' 这种非法月份不该把 2026-11 算进来
    expect(isSameMonth('2026-11-01', '2026-1')).toBe(true)
  })

  it('parseMonth 解析 YYYY-MM', () => {
    expect(parseMonth('2026-10')).toEqual({ year: 2026, month: 10 })
  })
})
