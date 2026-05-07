/**
 * 年度报告 i18n key 工具。所有文案 key 集中在此声明,便于统一替换 / 翻译。
 *
 * 实际文案存于 apps/web/src/i18n/{zh-CN,zh-TW,en}.ts,本文件只声明 key
 * 类型,UI 用 useT() 取。如果 key 在 i18n 缺失,useT() 会回 fallback 字符串。
 */
export const TKEY = {
  // 主页面
  title: 'annualReport.title',
  shareButton: 'annualReport.shareButton',
  restartButton: 'annualReport.restartButton',
  closeButton: 'annualReport.closeButton',

  // page 1 — welcome
  page1Title: 'annualReport.page1.title',
  page1Subtitle: 'annualReport.page1.subtitle',
  page1Hint: 'annualReport.page1.hint',

  // page 2 — overview
  page2Title: 'annualReport.page2.title',
  page2RecordsLabel: 'annualReport.page2.recordsLabel',
  page2DaysLabel: 'annualReport.page2.daysLabel',
  page2IncomeLabel: 'annualReport.page2.incomeLabel',
  page2ExpenseLabel: 'annualReport.page2.expenseLabel',

  // page 3 — yoy
  page3Title: 'annualReport.page3.title',
  page3IncomeLabel: 'annualReport.page3.incomeLabel',
  page3ExpenseLabel: 'annualReport.page3.expenseLabel',
  page3SavingsLabel: 'annualReport.page3.savingsLabel',
  page3VsPrev: 'annualReport.page3.vsPrev',
  page3FirstYear: 'annualReport.page3.firstYear',

  // page 4 — monthly
  page4Title: 'annualReport.page4.title',
  page4Peak: 'annualReport.page4.peak',
  page4Trough: 'annualReport.page4.trough',
  page4MonthFmt: 'annualReport.page4.monthFmt',

  // page 5 — categories
  page5Title: 'annualReport.page5.title',
  page5Empty: 'annualReport.page5.empty',

  // page 6 — hours
  page6Title: 'annualReport.page6.title',
  page6BucketNight: 'annualReport.page6.bucketNight',
  page6BucketMorning: 'annualReport.page6.bucketMorning',
  page6BucketAfternoon: 'annualReport.page6.bucketAfternoon',
  page6BucketEvening: 'annualReport.page6.bucketEvening',

  // page 7 — weekday
  page7Title: 'annualReport.page7.title',
  page7Weekday: 'annualReport.page7.weekday',
  page7Weekend: 'annualReport.page7.weekend',
  page7AvgPerDay: 'annualReport.page7.avgPerDay',

  // page 8 — extremes
  page8Title: 'annualReport.page8.title',
  page8Largest: 'annualReport.page8.largest',
  page8First: 'annualReport.page8.first',
  page8MostExpensiveDay: 'annualReport.page8.mostExpensiveDay',

  // page 9 — habits
  page9Title: 'annualReport.page9.title',
  page9StreakLabel: 'annualReport.page9.streakLabel',
  page9AvgDailyLabel: 'annualReport.page9.avgDailyLabel',
  page9PerWeekLabel: 'annualReport.page9.perWeekLabel',
  page9DaysSuffix: 'annualReport.page9.daysSuffix',

  // page 10 — tags
  page10Title: 'annualReport.page10.title',
  page10Empty: 'annualReport.page10.empty',
  page10CountSuffix: 'annualReport.page10.countSuffix',

  // page 11 — achievements
  page11Title: 'annualReport.page11.title',
  page11Empty: 'annualReport.page11.empty',
  // 各成就 (跟 achievements.ts 里的 titleKey/descKey 对应)
  ach: {
    fullAttendance: { title: 'annualReport.ach.fullAttendance.title', desc: 'annualReport.ach.fullAttendance.desc' },
    frequentRecorder: { title: 'annualReport.ach.frequentRecorder.title', desc: 'annualReport.ach.frequentRecorder.desc' },
    halfYear: { title: 'annualReport.ach.halfYear.title', desc: 'annualReport.ach.halfYear.desc' },
    streak100: { title: 'annualReport.ach.streak100.title', desc: 'annualReport.ach.streak100.desc' },
    streak30: { title: 'annualReport.ach.streak30.title', desc: 'annualReport.ach.streak30.desc' },
    records1k: { title: 'annualReport.ach.records1k.title', desc: 'annualReport.ach.records1k.desc' },
    records500: { title: 'annualReport.ach.records500.title', desc: 'annualReport.ach.records500.desc' },
    records100: { title: 'annualReport.ach.records100.title', desc: 'annualReport.ach.records100.desc' },
    saverPro: { title: 'annualReport.ach.saverPro.title', desc: 'annualReport.ach.saverPro.desc' },
    saver: { title: 'annualReport.ach.saver.title', desc: 'annualReport.ach.saver.desc' },
    frugalProgress: { title: 'annualReport.ach.frugalProgress.title', desc: 'annualReport.ach.frugalProgress.desc' },
    moreAttentive: { title: 'annualReport.ach.moreAttentive.title', desc: 'annualReport.ach.moreAttentive.desc' },
    incomeGrowth: { title: 'annualReport.ach.incomeGrowth.title', desc: 'annualReport.ach.incomeGrowth.desc' },
  },

  // page 12 — outro
  page12Title: 'annualReport.page12.title',
  page12Body: 'annualReport.page12.body',

  // 海报弹窗
  posterDownload: 'annualReport.poster.download',
  posterTitle: 'annualReport.poster.title',

  // insights (一组,不展开 — 文件已多)
  insightOverviewFullYear: 'annualReport.insight.overviewFullYear',
  insightOverviewNormal: 'annualReport.insight.overviewNormal',
  insightYoyFrugal: 'annualReport.insight.yoyFrugal',
  insightYoyMoreSpent: 'annualReport.insight.yoyMoreSpent',
  insightYoyMoreEarned: 'annualReport.insight.yoyMoreEarned',
  insightYoyStable: 'annualReport.insight.yoyStable',
  insightYoyFirstYear: 'annualReport.insight.yoyFirstYear',
  insightMonthly: 'annualReport.insight.monthly',
  insightCategoryHeavy: 'annualReport.insight.categoryHeavy',
  insightCategoryFavorite: 'annualReport.insight.categoryFavorite',
  insightCategoryBalanced: 'annualReport.insight.categoryBalanced',
  insightCategoryEmpty: 'annualReport.insight.categoryEmpty',
  insightHoursNight: 'annualReport.insight.hoursNight',
  insightHoursMorning: 'annualReport.insight.hoursMorning',
  insightHoursAfternoon: 'annualReport.insight.hoursAfternoon',
  insightHoursEvening: 'annualReport.insight.hoursEvening',
  insightHoursEmpty: 'annualReport.insight.hoursEmpty',
  insightWeekdayBoost: 'annualReport.insight.weekdayBoost',
  insightWeekdayWorkHard: 'annualReport.insight.weekdayWorkHard',
  insightWeekdayBalanced: 'annualReport.insight.weekdayBalanced',
  insightWeekdayEmpty: 'annualReport.insight.weekdayEmpty',
  insightExtremes: 'annualReport.insight.extremes',
  insightExtremesEmpty: 'annualReport.insight.extremesEmpty',
  insightHabitsLegend: 'annualReport.insight.habitsLegend',
  insightHabitsDisciplined: 'annualReport.insight.habitsDisciplined',
  insightHabitsCasual: 'annualReport.insight.habitsCasual',
  insightTags: 'annualReport.insight.tags',
  insightTagsEmpty: 'annualReport.insight.tagsEmpty',
  insightAchievementsMany: 'annualReport.insight.achievementsMany',
  insightAchievementsFew: 'annualReport.insight.achievementsFew',
  insightAchievementsNone: 'annualReport.insight.achievementsNone',
  insightOutro: 'annualReport.insight.outro',

  // entry banner
  entryBannerTitle: 'annualReport.entry.title',
  entryBannerSubtitle: 'annualReport.entry.subtitle',
  entryBannerCta: 'annualReport.entry.cta',
  entryBannerLoading: 'annualReport.entry.loading',
  entryBannerError: 'annualReport.entry.error',

  // 边界
  insufficientDataTitle: 'annualReport.insufficientData.title',
  insufficientDataBody: 'annualReport.insufficientData.body',
  insufficientDataCta: 'annualReport.insufficientData.cta',
} as const

/** 把 ach.frugalProgress 之类的 nested key 摊平到顶层 i18n 平铺结构,转一下 */
export function flatTKey(): Record<string, string> {
  const out: Record<string, string> = {}
  function walk(obj: any, prefix = '') {
    for (const [k, v] of Object.entries(obj)) {
      if (typeof v === 'string') {
        out[prefix + k] = v
      } else if (typeof v === 'object' && v !== null) {
        walk(v, prefix + k + '.')
      }
    }
  }
  walk(TKEY)
  return out
}
