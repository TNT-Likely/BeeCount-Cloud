/**
 * 成就解锁规则。基于聚合后的 metric,判断哪些成就用户达成了。
 *
 * 每个成就只输出 i18n key,UI 渲染时翻译。规则尽量简单 + 直观,避免复杂阈值
 * 让用户疑惑「为啥我没解锁」。
 */
import type { Achievement, AnnualReportData } from './types'
import { TKEY } from '../i18n'

type DataWithoutAchievements = Omit<AnnualReportData, 'achievements'>

export function computeAchievements(d: DataWithoutAchievements): Achievement[] {
  const achievements: Achievement[] = []

  // 满勤(>= 360 天有交易)
  if (d.recordingDays >= 360) {
    achievements.push({
      id: 'full-attendance',
      titleKey: TKEY.ach.fullAttendance.title,
      descKey: TKEY.ach.fullAttendance.desc,
      rare: true,
    })
  } else if (d.recordingDays >= 300) {
    // 高频记账(>= 300 天)
    achievements.push({
      id: 'frequent-recorder',
      titleKey: TKEY.ach.frequentRecorder.title,
      descKey: TKEY.ach.frequentRecorder.desc,
    })
  } else if (d.recordingDays >= 180) {
    // 半年坚持
    achievements.push({
      id: 'half-year-keeper',
      titleKey: TKEY.ach.halfYear.title,
      descKey: TKEY.ach.halfYear.desc,
    })
  }

  // 长连续打卡(>= 100 天)
  if (d.maxConsecutiveDays >= 100) {
    achievements.push({
      id: 'streak-100',
      titleKey: TKEY.ach.streak100.title,
      descKey: TKEY.ach.streak100.desc,
      rare: true,
    })
  } else if (d.maxConsecutiveDays >= 30) {
    achievements.push({
      id: 'streak-30',
      titleKey: TKEY.ach.streak30.title,
      descKey: TKEY.ach.streak30.desc,
    })
  }

  // 笔数里程碑
  if (d.totalRecords >= 1000) {
    achievements.push({
      id: 'records-1k',
      titleKey: TKEY.ach.records1k.title,
      descKey: TKEY.ach.records1k.desc,
      rare: true,
    })
  } else if (d.totalRecords >= 500) {
    achievements.push({
      id: 'records-500',
      titleKey: TKEY.ach.records500.title,
      descKey: TKEY.ach.records500.desc,
    })
  } else if (d.totalRecords >= 100) {
    achievements.push({
      id: 'records-100',
      titleKey: TKEY.ach.records100.title,
      descKey: TKEY.ach.records100.desc,
    })
  }

  // 储蓄率(>= 30%)
  if (d.savingsRate >= 50) {
    achievements.push({
      id: 'saver-pro',
      titleKey: TKEY.ach.saverPro.title,
      descKey: TKEY.ach.saverPro.desc,
      rare: true,
    })
  } else if (d.savingsRate >= 30) {
    achievements.push({
      id: 'saver',
      titleKey: TKEY.ach.saver.title,
      descKey: TKEY.ach.saver.desc,
    })
  }

  // 比去年节俭(支出同比 -10% 以下)
  if (d.yoyExpenseChange <= -10 && d.prevYear.totalExpense > 0) {
    achievements.push({
      id: 'frugal-progress',
      titleKey: TKEY.ach.frugalProgress.title,
      descKey: TKEY.ach.frugalProgress.desc,
    })
  }

  // 比去年记得更勤(笔数同比 +20% 以上)
  if (d.yoyRecordChange >= 20 && d.prevYear.totalRecords > 0) {
    achievements.push({
      id: 'more-attentive',
      titleKey: TKEY.ach.moreAttentive.title,
      descKey: TKEY.ach.moreAttentive.desc,
    })
  }

  // 收入增长(收入同比 +20% 以上)
  if (d.yoyIncomeChange >= 20 && d.prevYear.totalIncome > 0) {
    achievements.push({
      id: 'income-growth',
      titleKey: TKEY.ach.incomeGrowth.title,
      descKey: TKEY.ach.incomeGrowth.desc,
    })
  }

  return achievements
}
