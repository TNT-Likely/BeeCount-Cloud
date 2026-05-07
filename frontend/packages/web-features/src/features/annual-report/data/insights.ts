/**
 * 智能洞察句生成。基于 metric 阈值 + 模板,产出像 "你 X% 的钱花在 Y" 这种
 * 自然语言短句。**不调 LLM**,纯规则,够用。
 *
 * 每个 insight 函数返回 { textKey, args },UI 用 i18n.t(textKey, args) 渲染。
 * args 是字典,方便 i18n placeholder 注入。
 */
import type { AnnualReportData } from './types'
import { TKEY } from '../i18n'

export type Insight = {
  textKey: string
  /** placeholder 参数,UI 渲染时注入 */
  args?: Record<string, string | number>
}

const monthName = (m: number): string => `${m}` // i18n 层做月份本地化

const fmtPct = (n: number): string => Math.abs(n).toFixed(0)

// ============================================================================
// 各页洞察句
// ============================================================================

/** Page 2 总览:笔数 + 流转金额 */
export function overviewInsight(d: AnnualReportData): Insight {
  if (d.recordingDays >= 360) {
    return {
      textKey: TKEY.insightOverviewFullYear,
      args: {
        records: d.totalRecords,
        days: d.recordingDays,
        flow: Math.round(d.totalIncome + d.totalExpense),
      },
    }
  }
  return {
    textKey: TKEY.insightOverviewNormal,
    args: {
      records: d.totalRecords,
      days: d.recordingDays,
      flow: Math.round(d.totalIncome + d.totalExpense),
    },
  }
}

/** Page 3 跟去年比 */
export function yoyInsight(d: AnnualReportData): Insight {
  // 没有去年数据
  if (d.prevYear.totalExpense === 0 && d.prevYear.totalIncome === 0) {
    return { textKey: TKEY.insightYoyFirstYear }
  }
  // 节俭进步(-10% 以下)
  if (d.yoyExpenseChange <= -10) {
    return {
      textKey: TKEY.insightYoyFrugal,
      args: { pct: fmtPct(d.yoyExpenseChange) },
    }
  }
  // 支出涨幅大(+20%+)
  if (d.yoyExpenseChange >= 20) {
    return {
      textKey: TKEY.insightYoyMoreSpent,
      args: { pct: fmtPct(d.yoyExpenseChange) },
    }
  }
  // 收入涨幅大(+20%+)
  if (d.yoyIncomeChange >= 20) {
    return {
      textKey: TKEY.insightYoyMoreEarned,
      args: { pct: fmtPct(d.yoyIncomeChange) },
    }
  }
  return {
    textKey: TKEY.insightYoyStable,
    args: {
      incomePct: fmtPct(d.yoyIncomeChange),
      expensePct: fmtPct(d.yoyExpenseChange),
    },
  }
}

/** Page 4 月份起伏 */
export function monthlyInsight(d: AnnualReportData): Insight {
  const peakBucket = d.monthlyData.find((m) => m.month === d.peakMonth)
  const troughBucket = d.monthlyData.find((m) => m.month === d.troughMonth)
  return {
    textKey: TKEY.insightMonthly,
    args: {
      peakMonth: monthName(d.peakMonth),
      peakAmount: Math.round(peakBucket?.expense ?? 0),
      troughMonth: monthName(d.troughMonth),
      troughAmount: Math.round(troughBucket?.expense ?? 0),
    },
  }
}

/** Page 5 你最爱什么(分类) */
export function categoryInsight(d: AnnualReportData): Insight {
  const top = d.topExpenseCategories[0]
  if (!top) return { textKey: TKEY.insightCategoryEmpty }
  const pct = top.percent
  if (pct >= 30) {
    return {
      textKey: TKEY.insightCategoryHeavy,
      args: { name: top.name, pct: pct.toFixed(0) },
    }
  }
  if (pct >= 20) {
    return {
      textKey: TKEY.insightCategoryFavorite,
      args: { name: top.name, pct: pct.toFixed(0) },
    }
  }
  return {
    textKey: TKEY.insightCategoryBalanced,
    args: { name: top.name, pct: pct.toFixed(0) },
  }
}

/** Page 6 时段分析 */
export function hoursInsight(d: AnnualReportData): Insight {
  const totalCount = d.hourBuckets.reduce((s, b) => s + b.count, 0)
  if (totalCount === 0) return { textKey: TKEY.insightHoursEmpty }
  const peak = d.hourBuckets.reduce((max, b) => (b.count > max.count ? b : max))
  const pct = ((peak.count / totalCount) * 100).toFixed(0)
  const keyByBucket: Record<typeof peak.bucket, string> = {
    night: TKEY.insightHoursNight,
    morning: TKEY.insightHoursMorning,
    afternoon: TKEY.insightHoursAfternoon,
    evening: TKEY.insightHoursEvening,
  }
  return {
    textKey: keyByBucket[peak.bucket],
    args: { pct },
  }
}

/** Page 7 工作日 vs 周末 */
export function weekdayInsight(d: AnnualReportData): Insight {
  if (d.weekendBoost === 0) {
    return { textKey: TKEY.insightWeekdayEmpty }
  }
  if (d.weekendBoost >= 1.5) {
    return {
      textKey: TKEY.insightWeekdayBoost,
      args: { times: d.weekendBoost.toFixed(1) },
    }
  }
  if (d.weekendBoost <= 0.7) {
    return {
      textKey: TKEY.insightWeekdayWorkHard,
      args: { times: (1 / d.weekendBoost).toFixed(1) },
    }
  }
  return { textKey: TKEY.insightWeekdayBalanced }
}

/** Page 8 极端时刻 */
export function extremesInsight(d: AnnualReportData): Insight {
  if (!d.largestExpense) return { textKey: TKEY.insightExtremesEmpty }
  const date = d.largestExpense.happenedAt.slice(0, 10)
  return {
    textKey: TKEY.insightExtremes,
    args: {
      date,
      amount: Math.round(d.largestExpense.amount),
      note:
        d.largestExpense.note ||
        d.largestExpense.categoryName ||
        d.largestExpense.accountName ||
        '',
    },
  }
}

/** Page 9 习惯画像 */
export function habitsInsight(d: AnnualReportData): Insight {
  if (d.maxConsecutiveDays >= 100) {
    return {
      textKey: TKEY.insightHabitsLegend,
      args: { days: d.maxConsecutiveDays },
    }
  }
  if (d.maxConsecutiveDays >= 30) {
    return {
      textKey: TKEY.insightHabitsDisciplined,
      args: { days: d.maxConsecutiveDays },
    }
  }
  return {
    textKey: TKEY.insightHabitsCasual,
    args: {
      days: d.maxConsecutiveDays,
      avg: d.avgDailyExpense.toFixed(0),
    },
  }
}

/** Page 10 商家 / 标签 */
export function tagsInsight(d: AnnualReportData): Insight {
  if (d.topTags.length === 0) return { textKey: TKEY.insightTagsEmpty }
  const top = d.topTags[0]
  return {
    textKey: TKEY.insightTags,
    args: { name: top.name, count: top.count },
  }
}

/** Page 11 成就墙 */
export function achievementsInsight(d: AnnualReportData): Insight {
  const n = d.achievements.length
  if (n === 0) return { textKey: TKEY.insightAchievementsNone }
  if (n >= 5) {
    return {
      textKey: TKEY.insightAchievementsMany,
      args: { count: n },
    }
  }
  return {
    textKey: TKEY.insightAchievementsFew,
    args: { count: n },
  }
}

/** Page 12 结尾(治愈句) */
export function outroInsight(d: AnnualReportData): Insight {
  return {
    textKey: TKEY.insightOutro,
    args: { records: d.totalRecords },
  }
}
