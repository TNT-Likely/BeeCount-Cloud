/**
 * 年度报告 — 公共导出。consumer 只 import 这个 barrel。
 */
export { AnnualReportPage } from './AnnualReportPage'
export type { AnnualReportPageProps } from './AnnualReportPage'
export { PosterDialog } from './widgets/PosterDialog'
export {
  aggregate,
  fetchAnnualReportData,
  MIN_RECORDS_FOR_REPORT,
  type AnnualReportData,
  type TransactionLite,
  type CategoryStat,
  type TagStat,
  type MonthBucket,
  type HourBucket,
  type DayStat,
  type Achievement,
  type Insight,
} from './data'
export { TKEY as ANNUAL_REPORT_TKEY } from './i18n'
