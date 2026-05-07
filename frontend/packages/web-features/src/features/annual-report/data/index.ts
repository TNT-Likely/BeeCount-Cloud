// 年度报告数据层 — 公共导出。UI 层只 import 这个 barrel。

export * from './types'
export { aggregate, MIN_RECORDS_FOR_REPORT } from './aggregate'
export { fetchAnnualReportData } from './fetch'
export {
  overviewInsight,
  yoyInsight,
  monthlyInsight,
  categoryInsight,
  hoursInsight,
  weekdayInsight,
  extremesInsight,
  habitsInsight,
  tagsInsight,
  achievementsInsight,
  outroInsight,
  type Insight,
} from './insights'
