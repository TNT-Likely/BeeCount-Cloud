/**
 * 公历 → 农历 转换(1900–2100)。
 *
 * 算法:经典 lunarInfo 位编码表(1900 为基准,1900-01-31 = 农历 1900 正月初一)。
 * 月份名 / 日名 / 生肖 均为模块级常量数组,不出现魔法字符串。
 *
 * 说明:法定节假日(春节 / 清明 / 端午 / 中秋)由 chinaHolidays.ts 维护(有官方文件
 * 依据);本模块只做纯历法换算,不臆测节日。
 */

/** 农历年历编码表:每个 16bit 数编码当年 12(或 13)个月的大小月及闰月。
 *  bit15..bit3 对应正月..腊月(及闰月位),bit16(0x10000)置位表示闰月为大月。 */
const LUNAR_INFO: readonly number[] = [
  0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2, // 1900-1909
  0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977, // 1910-1919
  0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970, // 1920-1929
  0x06566, 0x0d4a0, 0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950, // 1930-1939
  0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557, // 1940-1949
  0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0, // 1950-1959
  0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0, // 1960-1969
  0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6, // 1970-1979
  0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570, // 1980-1989
  0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x055c0, 0x0ab60, 0x096d5, 0x092e0, // 1990-1999
  0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5, // 2000-2009
  0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930, // 2010-2019
  0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530, // 2020-2029
  0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45, // 2030-2039
  0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0, // 2040-2049
  0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06b20, 0x1a6c4, 0x0aae0, // 2050-2059
  0x0a2e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4, // 2060-2069
  0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0, // 2070-2079
  0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160, // 2080-2089
  0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252, // 2090-2099
  0x0d520, // 2100
]

/** 农历月名(下标 = 月数 - 1)。 */
const LUNAR_MONTH_NAMES: readonly string[] = [
  '正月', '二月', '三月', '四月', '五月', '六月',
  '七月', '八月', '九月', '十月', '冬月', '腊月',
]

/** 农历日名(下标 = 日数 - 1)。 */
const LUNAR_DAY_NAMES: readonly string[] = [
  '初一', '初二', '初三', '初四', '初五', '初六', '初七', '初八', '初九', '初十',
  '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十',
  '廿一', '廿二', '廿三', '廿四', '廿五', '廿六', '廿七', '廿八', '廿九', '三十',
]

/** 生肖(下标 = (农历年 - 4) mod 12)。 */
const ZODIAC: readonly string[] = [
  '鼠', '牛', '虎', '兔', '龙', '蛇', '马', '羊', '猴', '鸡', '狗', '猪',
]

const BASE_YEAR = 1900
const BASE_MONTH_INDEX = 0 // 0 = January
const BASE_DAY = 31 // 1900-01-31 = 农历 1900 正月初一
const MAX_YEAR = 2100
const DAY_MS = 86_400_000

export interface LunarDate {
  /** 农历年(可能与公历年不同,如 2026-01-01 仍属 2025 农历年)。 */
  year: number
  /** 农历月 1–12(闰月时 month 仍为该月数字,用 isLeap 区分)。 */
  month: number
  /** 农历日 1–30。 */
  day: number
  /** 是否闰月。 */
  isLeap: boolean
  /** 月名,如「正月」「闰四月」。 */
  monthName: string
  /** 日名,如「初一」「十五」。 */
  dayName: string
  /** 生肖,如「龙」。 */
  yearZodiac: string
  /** 中文全名,如「正月初一」「闰四月初一」。 */
  text: string
}

function lYearDays(y: number): number {
  let sum = 348
  for (let i = 0x8000; i > 0x8; i >>= 1) {
    sum += LUNAR_INFO[y - BASE_YEAR] & i ? 1 : 0
  }
  return sum + leapDays(y)
}

function leapDays(y: number): number {
  if (leapMonth(y) === 0) return 0
  return LUNAR_INFO[y - BASE_YEAR] & 0x10000 ? 30 : 29
}

function leapMonth(y: number): number {
  return LUNAR_INFO[y - BASE_YEAR] & 0xf
}

function monthDays(y: number, m: number): number {
  return LUNAR_INFO[y - BASE_YEAR] & (0x10000 >> m) ? 30 : 29
}

/**
 * 公历日期(YYYY-MM-DD)→ 农历。
 * 超出 1900–2100 抛错(表覆盖范围之外,不静默返回错误结果)。
 */
export function solarToLunar(dateKey: string): LunarDate {
  const parts = dateKey.split('-')
  if (parts.length !== 3) {
    throw new Error(`solarToLunar: invalid dateKey "${dateKey}", expect YYYY-MM-DD`)
  }
  const y = Number(parts[0])
  const m = Number(parts[1])
  const d = Number(parts[2])
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d)) {
    throw new Error(`solarToLunar: non-integer components in "${dateKey}"`)
  }
  if (y < BASE_YEAR || y > MAX_YEAR) {
    throw new Error(`solarToLunar: year ${y} out of supported range ${BASE_YEAR}-${MAX_YEAR}`)
  }

  const baseDate = Date.UTC(BASE_YEAR, BASE_MONTH_INDEX, BASE_DAY)
  const objDate = Date.UTC(y, m - 1, d)
  let offset = Math.round((objDate - baseDate) / DAY_MS)

  let lunarYear = BASE_YEAR
  let temp = 0
  for (lunarYear = BASE_YEAR; lunarYear <= MAX_YEAR && offset > 0; lunarYear++) {
    temp = lYearDays(lunarYear)
    offset -= temp
  }
  if (offset < 0) {
    offset += temp
    lunarYear -= 1
  }

  const leap = leapMonth(lunarYear)
  let isLeap = false
  let lunarMonth = 1
  for (lunarMonth = 1; lunarMonth < 13 && offset > 0; lunarMonth++) {
    if (leap > 0 && lunarMonth === leap + 1 && !isLeap) {
      lunarMonth -= 1
      isLeap = true
      temp = leapDays(lunarYear)
    } else {
      temp = monthDays(lunarYear, lunarMonth)
    }
    if (isLeap && lunarMonth === leap + 1) isLeap = false
    offset -= temp
  }
  if (offset === 0 && leap > 0 && lunarMonth === leap + 1) {
    if (isLeap) {
      isLeap = false
    } else {
      isLeap = true
      lunarMonth -= 1
    }
  }
  if (offset < 0) {
    offset += temp
    lunarMonth -= 1
  }

  const lunarDay = offset + 1
  const monthName = isLeap
    ? `闰${LUNAR_MONTH_NAMES[lunarMonth - 1]}`
    : LUNAR_MONTH_NAMES[lunarMonth - 1]
  const dayName = LUNAR_DAY_NAMES[lunarDay - 1]
  const yearZodiac = ZODIAC[(lunarYear - 4) % 12]
  const text = `${monthName}${dayName}`

  return {
    year: lunarYear,
    month: lunarMonth,
    day: lunarDay,
    isLeap,
    monthName,
    dayName,
    yearZodiac,
    text,
  }
}
