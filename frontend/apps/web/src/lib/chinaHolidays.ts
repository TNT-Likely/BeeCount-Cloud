/**
 * 中国法定节假日 + 调休补班日(内置静态数据,来源:国务院办公厅年度通知)。
 *
 * 背景:日历页要给用户标出节假日,但不能依赖外部 API ——
 *  1) 部署环境(FnOS 内网)不保证能出网;
 *  2) 放假安排一年一定,构建期内置足够覆盖;
 *  3) 权威数据一年只发布一次(一般前一年 11 月),更新频率极低。
 *
 * 数据(经 gov.cn / news.cn 多源核对):
 *  - 2025 年:国办发明电〔2024〕12 号(2024-11-12 发布)
 *  - 2026 年:国办发明电〔2025〕7 号(2025-11-04 发布)
 *
 * 未收录年份(如 2027+):走公历固定节日 fallback(元旦 / 劳动节 /
 * 国庆头三天)。农历节日(春节 / 清明 / 端午 / 中秋)无权威表不猜,
 * 宁可标不出来也不标错 —— 新一年通知发布后把区间补进
 * HOLIDAY_RANGES 即可。
 */

/** 'holiday' = 放假休息;'workday' = 周末补班(调休上班)。 */
export type ChinaHolidayKind = 'holiday' | 'workday'

export type ChinaHolidayInfo = {
  /** 节日名 i18n key 后缀(调用方拼 `calendar.holiday.${nameKey}` 后 t())。 */
  nameKey: string
  kind: ChinaHolidayKind
}

/** 声明式区间:程序展开成逐日 map,比手写 100+ 行日期不易错。 */
type HolidayRange = {
  /** YYYY-MM-DD(含) */
  start: string
  /** YYYY-MM-DD(含) */
  end: string
  nameKey: string
}

const HOLIDAY_RANGES: Record<number, HolidayRange[]> = {
  // 2025 年(国办发明电〔2024〕12 号)
  2025: [
    { start: '2025-01-01', end: '2025-01-01', nameKey: 'newYear' }, // 元旦(1 天,不调休)
    { start: '2025-01-28', end: '2025-02-04', nameKey: 'springFestival' }, // 春节 8 天
    { start: '2025-04-04', end: '2025-04-06', nameKey: 'qingming' }, // 清明 3 天
    { start: '2025-05-01', end: '2025-05-05', nameKey: 'labourDay' }, // 劳动节 5 天
    { start: '2025-05-31', end: '2025-06-02', nameKey: 'dragonBoat' }, // 端午 3 天
    { start: '2025-10-01', end: '2025-10-08', nameKey: 'nationalDay' }, // 国庆+中秋合并 8 天
  ],
  // 2026 年(国办发明电〔2025〕7 号)
  2026: [
    { start: '2026-01-01', end: '2026-01-03', nameKey: 'newYear' }, // 元旦 3 天(1/4 周日补班)
    { start: '2026-02-15', end: '2026-02-23', nameKey: 'springFestival' }, // 春节 9 天(2/14、2/28 补班)
    { start: '2026-04-04', end: '2026-04-06', nameKey: 'qingming' }, // 清明 3 天(不调休)
    { start: '2026-05-01', end: '2026-05-05', nameKey: 'labourDay' }, // 劳动节 5 天(5/9 补班)
    { start: '2026-06-19', end: '2026-06-21', nameKey: 'dragonBoat' }, // 端午 3 天(不调休)
    { start: '2026-09-25', end: '2026-09-27', nameKey: 'midAutumn' }, // 中秋 3 天(不调休)
    { start: '2026-10-01', end: '2026-10-07', nameKey: 'nationalDay' }, // 国庆 7 天(9/20、10/10 补班)
  ],
}

/** 调休补班日(周末要上班)。 */
const MAKEUP_WORKDAYS: Record<number, string[]> = {
  2025: ['2025-01-26', '2025-02-08', '2025-04-27', '2025-09-28', '2025-10-11'],
  2026: ['2026-01-04', '2026-02-14', '2026-02-28', '2026-05-09', '2026-09-20', '2026-10-10'],
}

/** 未收录年份的公历固定节日 fallback(只放公历,不含农历节日)。 */
const FALLBACK_FIXED: Array<{ month: number; day: number; nameKey: string }> = [
  { month: 1, day: 1, nameKey: 'newYear' },
  { month: 5, day: 1, nameKey: 'labourDay' },
  { month: 10, day: 1, nameKey: 'nationalDay' },
  { month: 10, day: 2, nameKey: 'nationalDay' },
  { month: 10, day: 3, nameKey: 'nationalDay' },
]

const DATE_KEY_RE = /^\d{4}-\d{2}-\d{2}$/

let mapCache: Map<string, ChinaHolidayInfo> | null = null

function dateRangeKeys(start: string, end: string): string[] {
  const keys: string[] = []
  const [y, m, d] = start.split('-').map(Number)
  const [ey, em, ed] = end.split('-').map(Number)
  const endTime = new Date(ey, em - 1, ed).getTime()
  for (
    let cur = new Date(y, m - 1, d);
    cur.getTime() <= endTime;
    cur = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate() + 1)
  ) {
    keys.push(
      `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}-${String(
        cur.getDate(),
      ).padStart(2, '0')}`,
    )
  }
  return keys
}

function buildMap(): Map<string, ChinaHolidayInfo> {
  const map = new Map<string, ChinaHolidayInfo>()
  for (const year of Object.keys(HOLIDAY_RANGES)) {
    for (const range of HOLIDAY_RANGES[Number(year)]) {
      for (const key of dateRangeKeys(range.start, range.end)) {
        map.set(key, { nameKey: range.nameKey, kind: 'holiday' })
      }
    }
  }
  for (const year of Object.keys(MAKEUP_WORKDAYS)) {
    for (const key of MAKEUP_WORKDAYS[Number(year)]) {
      map.set(key, { nameKey: 'makeupWork', kind: 'workday' })
    }
  }
  return map
}

/**
 * 查某天是否法定节假日 / 调休补班日;都不是返回 null。
 * 未收录年份用公历固定节日 fallback。
 */
export function getChinaHoliday(dateKey: string): ChinaHolidayInfo | null {
  if (!DATE_KEY_RE.test(dateKey)) return null
  if (!mapCache) mapCache = buildMap()
  const hit = mapCache.get(dateKey)
  if (hit) return hit
  // fallback:公历固定节日(仅未收录年份生效,不覆盖权威表)
  const year = Number(dateKey.slice(0, 4))
  if (year in HOLIDAY_RANGES || year in MAKEUP_WORKDAYS) return null
  const month = Number(dateKey.slice(5, 7))
  const day = Number(dateKey.slice(8, 10))
  const fixed = FALLBACK_FIXED.find((f) => f.month === month && f.day === day)
  return fixed ? { nameKey: fixed.nameKey, kind: 'holiday' } : null
}

/** 已内置权威安排的年份列表(测试 / 文档用)。 */
export const CHINA_HOLIDAY_YEARS = Object.keys(HOLIDAY_RANGES)
  .map(Number)
  .sort((a, b) => a - b)
