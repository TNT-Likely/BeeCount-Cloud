import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { monthlyInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 月度趋势:12 个月柱状图,高峰月 / 低谷月高亮 + 月份名飘字。
 */
export function PageMonthlyTrend({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = monthlyInsight(data)

  const max = Math.max(...data.monthlyData.map((m) => m.expense), 1)

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={210} />
      <div className="relative z-10 mx-auto flex h-full max-w-5xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-3 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page4Title)}
        </h2>
        <div className="mb-10 flex gap-6 text-sm text-white/50">
          <span>
            {t(TKEY.page4Peak)}{' '}
            <span className="font-semibold text-amber-300">
              {t(TKEY.page4MonthFmt, { month: data.peakMonth })}
            </span>
          </span>
          <span>
            {t(TKEY.page4Trough)}{' '}
            <span className="font-semibold text-sky-300">
              {t(TKEY.page4MonthFmt, { month: data.troughMonth })}
            </span>
          </span>
        </div>

        <div ref={ref} className="flex h-64 w-full items-end justify-between gap-2 sm:gap-3">
          {data.monthlyData.map((m, i) => {
            const heightPct = (m.expense / max) * 100
            const isPeak = m.month === data.peakMonth
            const isTrough = m.month === data.troughMonth && m.expense > 0
            const color = isPeak ? '#F4A82B' : isTrough ? '#0EA5E9' : 'rgba(244, 168, 43, 0.4)'
            return (
              <div key={m.month} className="flex flex-1 flex-col items-center gap-2">
                <div className="relative flex h-full w-full items-end">
                  <motion.div
                    className="w-full rounded-t-md"
                    style={{ background: color }}
                    initial={{ height: 0 }}
                    animate={inView ? { height: `${heightPct}%` } : {}}
                    transition={{ duration: 1.0, delay: i * 0.06 + 0.2, ease: [0.16, 1, 0.3, 1] }}
                  />
                  {isPeak && m.expense > 0 && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={inView ? { opacity: 1, y: 0 } : {}}
                      transition={{ delay: 1.4 }}
                      className="absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs font-semibold text-amber-300"
                    >
                      ↑ {Math.round(m.expense).toLocaleString()}
                    </motion.div>
                  )}
                </div>
                <div
                  className={`text-xs ${
                    isPeak || isTrough ? 'font-semibold text-white' : 'text-white/40'
                  }`}
                >
                  {m.month}
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.0} />
        </div>
      </div>
    </div>
  )
}
