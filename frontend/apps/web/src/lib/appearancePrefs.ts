import type { ProfileAppearance } from '@beecount/api-client'

/**
 * 外观偏好(appearance)的字段名 / 默认值集中常量。
 *
 * 目标:组件里不再散落魔法字符串(如裸写 'show_holidays')与魔法布尔
 * (如默认 true/false 硬编码在 UI)。所有偏好 key 走 APPEARANCE_KEYS,
 * 默认开关走 APPEARANCE_DEFAULTS,归一化结果走 resolveAppearancePrefs()。
 */

/** appearance JSON 里各偏好字段的 key(与 server 透传的字段名一致)。 */
export const APPEARANCE_KEYS = {
  HEADER_SKIN: 'header_skin',
  COMPACT_AMOUNT: 'compact_amount',
  SHOW_TRANSACTION_TIME: 'show_transaction_time',
  SKIN_ANIMATION: 'skin_animation',
  NOTE_DISPLAY_MODE: 'note_display_mode',
  /** 日历:显示法定节假日(角标/底纹)。 */
  SHOW_HOLIDAYS: 'show_holidays',
  /** 日历:显示农历日期。 */
  SHOW_LUNAR: 'show_lunar',
} as const

/** 偏好开关的默认值(当 appearance 未设置该字段时使用)。 */
export const APPEARANCE_DEFAULTS = {
  /** 节假日默认开启 —— 保持既有日历"一直显示节假日"的行为,不破坏老用户。 */
  show_holidays: true,
  /** 农历默认关闭 —— 新功能,需用户主动开启。 */
  show_lunar: false,
} as const

/** 归一化后的偏好(永远是 boolean,无 undefined)。 */
export interface AppearancePrefs {
  showHolidays: boolean
  showLunar: boolean
}

function resolveBool(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

/** 把 raw appearance(可能缺字段 / 脏值)归一化成稳定布尔偏好。 */
export function resolveAppearancePrefs(
  appearance: ProfileAppearance | null | undefined,
): AppearancePrefs {
  return {
    showHolidays: resolveBool(
      appearance?.[APPEARANCE_KEYS.SHOW_HOLIDAYS],
      APPEARANCE_DEFAULTS.show_holidays,
    ),
    showLunar: resolveBool(
      appearance?.[APPEARANCE_KEYS.SHOW_LUNAR],
      APPEARANCE_DEFAULTS.show_lunar,
    ),
  }
}
