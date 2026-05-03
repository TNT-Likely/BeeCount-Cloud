import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { BigNumber } from '../widgets/BigNumber'
import { InsightLine } from '../widgets/InsightLine'
import { habitsInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

const currencySymbol = (code: string) =>
  ({ CNY: '¥', USD: '$', EUR: '€', GBP: '£', JPY: '¥', HKD: 'HK$' } as Record<string, string>)[code] || code + ' '

/**
 * 习惯画像:最长连续记账 / 日均支出 / 周均笔数 — 三张大数字卡。
 * 顶部一条火苗般的连续条带,展示坚持感。
 */
export function PageHabits({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = habitsInsight(data)
  const sym = currencySymbol(data.ledgerCurrency)

  // 把 streak 限定在 0-365 比例条
  const streakPct = Math.min((data.maxConsecutiveDays / 365) * 100, 100)

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={120} />
      <div className="relative z-10 mx-auto flex h-full max-w-5xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page9Title)}
        </h2>

        {/* 顶部火苗条 — 表示连续坚持 */}
        <div ref={ref} className="mb-10 w-full">
          <div className="mb-3 flex items-baseline justify-between">
            <span className="text-xs uppercase tracking-widest text-white/50">
              {t(TKEY.page9StreakLabel)}
            </span>
            <span className="flex items-baseline gap-2">
              <span className="text-4xl">🔥</span>
              <BigNumber
                value={data.maxConsecutiveDays}
                size={48}
                className="text-amber-300"
                suffix={t(TKEY.page9DaysSuffix)}
              />
            </span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-white/10">
            <motion.div
              className="h-full rounded-full bg-gradient-to-r from-amber-400 via-orange-400 to-red-400"
              initial={{ width: 0 }}
              animate={inView ? { width: `${streakPct}%` } : {}}
              transition={{ duration: 1.4, delay: 0.4, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        </div>

        <div className="grid w-full grid-cols-2 gap-6 sm:gap-10">
          <Stat
            label={t(TKEY.page9AvgDailyLabel)}
            value={
              <BigNumber
                value={data.avgDailyExpense}
                format="currency"
                prefix={sym}
                size={56}
                className="text-emerald-300"
              />
            }
            delay={0.8}
            inView={inView}
          />
          <Stat
            label={t(TKEY.page9PerWeekLabel)}
            value={
              <BigNumber
                value={Math.round(data.recordsPerWeekAvg)}
                size={56}
                className="text-cyan-300"
              />
            }
            delay={1.0}
            inView={inView}
          />
        </div>

        <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.6} />
        </div>
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  delay,
  inView,
}: {
  label: string
  value: React.ReactNode
  delay: number
  inView: boolean
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.5, delay, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm sm:p-8"
    >
      <div className="text-xs uppercase tracking-widest text-white/50">{label}</div>
      <div>{value}</div>
    </motion.div>
  )
}
