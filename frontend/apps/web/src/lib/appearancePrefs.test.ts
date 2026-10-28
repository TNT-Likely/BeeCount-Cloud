import { describe, expect, it } from 'vitest'

import {
  APPEARANCE_DEFAULTS,
  APPEARANCE_KEYS,
  resolveAppearancePrefs,
  type AppearancePrefs,
} from './appearancePrefs'

/**
 * 外观偏好常量与默认值解析。
 *
 * 目标:把偏好字段名 / 默认开关值集中成常量(禁止魔法字符串 / 魔法布尔),
 * 组件只消费 APPEARANCE_KEYS / APPEARANCE_DEFAULTS,不散落裸字符串。
 */

describe('APPEARANCE_KEYS 常量(禁止魔法字符串)', () => {
  it('新增的节假日 / 农历偏好 key 与后端 appearance 字段名一致', () => {
    expect(APPEARANCE_KEYS.SHOW_HOLIDAYS).toBe('show_holidays')
    expect(APPEARANCE_KEYS.SHOW_LUNAR).toBe('show_lunar')
  })

  it('既有偏好 key 也集中在此,避免组件裸写', () => {
    expect(APPEARANCE_KEYS.HEADER_SKIN).toBe('header_skin')
    expect(APPEARANCE_KEYS.COMPACT_AMOUNT).toBe('compact_amount')
    expect(APPEARANCE_KEYS.SHOW_TRANSACTION_TIME).toBe('show_transaction_time')
    expect(APPEARANCE_KEYS.SKIN_ANIMATION).toBe('skin_animation')
    expect(APPEARANCE_KEYS.NOTE_DISPLAY_MODE).toBe('note_display_mode')
  })
})

describe('APPEARANCE_DEFAULTS 常量(禁止魔法布尔)', () => {
  it('节假日默认开启(保持既有日历行为),农历默认关闭(新功能)', () => {
    expect(APPEARANCE_DEFAULTS.show_holidays).toBe(true)
    expect(APPEARANCE_DEFAULTS.show_lunar).toBe(false)
  })
})

describe('resolveAppearancePrefs 归一化', () => {
  it('appearance 为 undefined / 空 → 回退默认值', () => {
    const a = resolveAppearancePrefs(undefined)
    const b = resolveAppearancePrefs({})
    const expected: AppearancePrefs = {
      showHolidays: APPEARANCE_DEFAULTS.show_holidays,
      showLunar: APPEARANCE_DEFAULTS.show_lunar,
    }
    expect(a).toEqual(expected)
    expect(b).toEqual(expected)
  })

  it('显式 false / true 被尊重', () => {
    expect(
      resolveAppearancePrefs({
        [APPEARANCE_KEYS.SHOW_HOLIDAYS]: false,
        [APPEARANCE_KEYS.SHOW_LUNAR]: true,
      }),
    ).toEqual({ showHolidays: false, showLunar: true })
  })

  it('非布尔脏值按默认处理(容错)', () => {
    const dirty = { show_holidays: 'yes', show_lunar: 0 } as Record<string, unknown>
    expect(resolveAppearancePrefs(dirty)).toEqual({
      showHolidays: true,
      showLunar: false,
    })
  })
})
