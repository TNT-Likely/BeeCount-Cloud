/**
 * 年度报告数据类型。
 *
 * 覆盖 12 屏 carousel 需要的所有 metric。客户端按 `aggregate(thisYear, prevYear)`
 * 计算,跟 mobile 端 `AnnualReportData` 类对齐(命名 / 字段对应,只是 camel
 * 命名 + TS 类型)。
 */

/**
 * 简化的交易记录(年度报告只用很少字段,从 WorkspaceTransaction / ReadTransaction
 * 投影下来)。比直接用全量类型省内存,聚合后扔掉原始数组。
 */
export type TransactionLite = {
  id: string
  txType: 'expense' | 'income' | 'transfer'
  amount: number
  happenedAt: string // ISO 8601
  note: string | null
  categoryName: string | null
  categoryKind: string | null
  accountName: string | null
  tagsList: string[]
}

/** 分类聚合统计 */
export type CategoryStat = {
  name: string
  /** 累计金额(总和,绝对值) */
  total: number
  /** 占同类型(收入或支出)的百分比(0-100) */
  percent: number
  /** 该分类下的交易笔数 */
  count: number
}

/** 标签聚合统计 */
export type TagStat = {
  name: string
  count: number
  /** 该标签下交易的总金额(绝对值) */
  total: number
}

/** 单月数据点 */
export type MonthBucket = {
  month: number // 1-12
  income: number
  expense: number
  /** income - expense */
  netFlow: number
  /** 该月交易笔数 */
  count: number
}

/** 时段(0-5 / 6-11 / 12-17 / 18-23) */
export type HourBucket = {
  /** 'night' | 'morning' | 'afternoon' | 'evening' */
  bucket: 'night' | 'morning' | 'afternoon' | 'evening'
  count: number
  total: number
}

/** 单日聚合(用于找最贵 / 最省的一天) */
export type DayStat = {
  date: string // 'YYYY-MM-DD'
  total: number
  count: number
}

/** 成就 */
export type Achievement = {
  id: string
  /** i18n key,UI 渲染时翻译 */
  titleKey: string
  /** i18n key */
  descKey: string
  /** 解锁时是否爆炸特效(高级成就) */
  rare?: boolean
}

/**
 * 年度报告完整数据(单账本,单年度)。
 *
 * 客户端按 `fetchAnnualReportData(token, ledgerId, year)` 调用一次后获得。
 * 各 page 组件从这个对象里取自己需要的字段。
 */
export type AnnualReportData = {
  // ===== meta =====
  year: number
  ledgerId: string
  ledgerName: string
  ledgerCurrency: string
  /** 数据是否够生成报告(笔数 >= 阈值)— 不够时 UI 显示「数据太少」引导记账 */
  hasSufficientData: boolean

  // ===== 整体规模 =====
  totalRecords: number
  totalIncome: number
  totalExpense: number
  netSavings: number // income - expense
  /** 储蓄率(0-100, 占收入百分比),收入为 0 时 = 0 */
  savingsRate: number
  /** 该年总天数(闰年 366,非闰年 365)*/
  totalDays: number
  /** 实际有交易的天数 */
  recordingDays: number

  // ===== 跟去年比 =====
  prevYear: { totalIncome: number; totalExpense: number; totalRecords: number }
  /** 收入同比变化 % (正数 = 涨,负数 = 跌) */
  yoyIncomeChange: number
  yoyExpenseChange: number
  yoyRecordChange: number

  // ===== 月份分布 =====
  monthlyData: MonthBucket[] // 长度 12,即使某月无数据也补 0
  peakMonth: number // 1-12,支出最高月
  troughMonth: number // 1-12,支出最低月(>0 笔的月份)

  // ===== 分类 =====
  topExpenseCategories: CategoryStat[] // 前 5
  topIncomeCategories: CategoryStat[] // 前 5

  // ===== 时段分布 =====
  hourBuckets: HourBucket[] // 4 个 bucket,固定顺序

  // ===== 工作日 vs 周末 =====
  /** 工作日日均支出 */
  weekdayAvgExpense: number
  weekendAvgExpense: number
  /** 周末是工作日的几倍 (0 时 = 0) */
  weekendBoost: number

  // ===== 极端时刻 =====
  largestExpense: TransactionLite | null
  largestIncome: TransactionLite | null
  firstRecord: TransactionLite | null
  lastRecord: TransactionLite | null
  mostExpensiveDay: DayStat | null
  mostFrugalDay: DayStat | null

  // ===== 习惯画像 =====
  /** 最长连续记账天数 */
  maxConsecutiveDays: number
  /** 日均支出(只算有交易的天) */
  avgDailyExpense: number
  /** 周均交易笔数 */
  recordsPerWeekAvg: number

  // ===== 商家 / 标签 =====
  topTags: TagStat[] // 前 6

  // ===== 成就 =====
  achievements: Achievement[]
}
