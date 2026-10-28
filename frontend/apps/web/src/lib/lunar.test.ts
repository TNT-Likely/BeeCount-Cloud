import { describe, expect, it } from 'vitest'

import { solarToLunar } from './lunar'

/**
 * 公历→农历转换单元测试。
 *
 * 锚定公历节日（公开权威日期），验证算法正确性：
 *  - 2024-02-10 甲辰龙年 正月初一
 *  - 2025-01-29 乙巳蛇年 正月初一
 *  - 2026-02-17 丙午马年 正月初一
 *  - 2023-01-22 癸卯兔年 正月初一
 *  - 2020-01-25 庚子鼠年 正月初一(该年闰四月)
 * 并验证日名（初一/十五）与中文 text 拼装。
 */

describe('solarToLunar 已知春节锚点', () => {
  it('2024-02-10 → 甲辰年 正月初一', () => {
    const r = solarToLunar('2024-02-10')
    expect(r.year).toBe(2024)
    expect(r.month).toBe(1)
    expect(r.day).toBe(1)
    expect(r.isLeap).toBe(false)
    expect(r.monthName).toBe('正月')
    expect(r.dayName).toBe('初一')
    expect(r.yearZodiac).toBe('龙')
    expect(r.text).toBe('正月初一')
  })

  it('2025-01-29 → 乙巳年 正月初一', () => {
    const r = solarToLunar('2025-01-29')
    expect(r.month).toBe(1)
    expect(r.day).toBe(1)
    expect(r.yearZodiac).toBe('蛇')
    expect(r.text).toBe('正月初一')
  })

  it('2026-02-17 → 丙午年 正月初一', () => {
    const r = solarToLunar('2026-02-17')
    expect(r.month).toBe(1)
    expect(r.day).toBe(1)
    expect(r.yearZodiac).toBe('马')
    expect(r.text).toBe('正月初一')
  })

  it('2023-01-22 → 癸卯年 正月初一(兔年)', () => {
    const r = solarToLunar('2023-01-22')
    expect(r.month).toBe(1)
    expect(r.day).toBe(1)
    expect(r.yearZodiac).toBe('兔')
    expect(r.text).toBe('正月初一')
  })
})

describe('solarToLunar 日名与节日', () => {
  it('2024-02-24 → 正月十五(元宵节)', () => {
    const r = solarToLunar('2024-02-24')
    expect(r.month).toBe(1)
    expect(r.day).toBe(15)
    expect(r.dayName).toBe('十五')
    expect(r.text).toBe('正月十五')
  })

  it('2026-03-03 → 正月十五', () => {
    const r = solarToLunar('2026-03-03')
    expect(r.text).toBe('正月十五')
  })

  it('月末三十 日名正确', () => {
    // 2024-03-10 为 二月初一
    expect(solarToLunar('2024-03-10').text).toBe('二月初一')
    // 2024-04-08 为 二月三十（2024 二月为大月）
    const r = solarToLunar('2024-04-08')
    expect(r.month).toBe(2)
    expect(r.day).toBe(30)
    expect(r.dayName).toBe('三十')
  })
})

describe('solarToLunar 闰月', () => {
  it('2020 为闰四月年,4 月末出现闰四月', () => {
    // 2020-05-23 为 闰四月初一 (庚子鼠年闰四月)
    const r = solarToLunar('2020-05-23')
    expect(r.isLeap).toBe(true)
    expect(r.month).toBe(4)
    expect(r.day).toBe(1)
    expect(r.monthName).toBe('闰四月')
    expect(r.text).toBe('闰四月初一')
  })
})

describe('solarToLunar 边界', () => {
  it('农历年跟公历年不总一致(跨年)', () => {
    // 2026-01-01 仍属 2025 农历年(乙巳);该年无闰月,腊月自 ~2026-01-18 起,
    // 故 2026-01-01 落于冬月十三。
    const r = solarToLunar('2026-01-01')
    expect(r.year).toBe(2025)
    expect(r.month).toBe(11)
    expect(r.day).toBe(13)
    expect(r.monthName).toBe('冬月')
    expect(r.text).toBe('冬月十三')
  })
})
