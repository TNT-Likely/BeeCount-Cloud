import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { BigNumber } from '../widgets/BigNumber'
import { InsightLine } from '../widgets/InsightLine'
import { weekdayInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

const currencySymbol = (code: string) =>
  ({ CNY: '¥', USD: '$', EUR: '€', GBP: '£', JPY: '¥', HKD: 'HK$' } as Record<string, string>)[code] || code + ' '

/**
 * 工作日 vs 周末:双卡对比 + 周末倍数高亮。
 * 用 ring + 数字翻牌组合呈现。
 */
export function PageWeekday({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = weekdayInsight(data)
  const sym = currencySymbol(data.ledgerCurrency)

  const max = Math.max(data.weekdayAvgExpense, data.weekendAvgExpense, 1)
  const weekdayPct = (data.weekdayAvgExpense / max) * 100
  const weekendPct = (data.weekendAvgExpense / max) * 100

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={170} />
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-3 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page7Title)}
        </h2>
        <div className="mb-10 text-sm text-white/50">{t(TKEY.page7AvgPerDay)}</div>

        <div ref={ref} className="grid w-full grid-cols-2 gap-6 sm:gap-10">
          <SideCard
            label={t(TKEY.page7Weekday)}
            emoji="💼"
            color="#5EEAD4"
            value={data.weekdayAvgExpense}
            sym={sym}
            pct={weekdayPct}
            inView={inView}
            delay={0.3}
          />
          <SideCard
            label={t(TKEY.page7Weekend)}
            emoji="🍷"
            color="#FBBF24"
            value={data.weekendAvgExpense}
            sym={sym}
            pct={weekendPct}
            inView={inView}
            delay={0.5}
          />
        </div>

        {data.weekendBoost > 0 && (
          <motion.div
            initial={{ opacity: 0, scale: 0.85 }}
            animate={inView ? { opacity: 1, scale: 1 } : {}}
            transition={{ duration: 0.5, delay: 1.2, ease: [0.16, 1, 0.3, 1] }}
            className="mt-10 flex items-center gap-3 rounded-2xl border border-white/15 bg-white/5 px-6 py-3 backdrop-blur-sm"
          >
            <span className="text-2xl">
              {data.weekendBoost >= 1 ? '↑' : '↓'}
            </span>
            <span className="text-base text-white/70 sm:text-lg">
              {data.weekendBoost >= 1 ? (
                <>
                  <span className="font-bold text-amber-300">×{data.weekendBoost.toFixed(1)}</span>
                  <span className="ml-2 text-white/60">weekend / weekday</span>
                </>
              ) : (
                <>
                  <span className="font-bold text-teal-300">×{(1 / data.weekendBoost).toFixed(1)}</span>
                  <span className="ml-2 text-white/60">weekday / weekend</span>
                </>
              )}
            </span>
          </motion.div>
        )}

        <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.4} />
        </div>
      </div>
    </div>
  )
}

function SideCard({
  label,
  emoji,
  color,
  value,
  sym,
  pct,
  inView,
  delay,
}: {
  label: string
  emoji: string
  color: string
  value: number
  sym: string
  pct: number
  inView: boolean
  delay: number
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.5, delay, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col gap-4 rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm sm:p-8"
    >
      <div className="flex items-center gap-3">
        <span className="text-3xl">{emoji}</span>
        <span className="text-sm uppercase tracking-widest text-white/60 sm:text-base">{label}</span>
      </div>
      <BigNumber value={value} format="currency" prefix={sym} size={40} className="text-white" />
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
        <motion.div
          className="h-full rounded-full"
          style={{ background: color }}
          initial={{ width: 0 }}
          animate={inView ? { width: `${pct}%` } : {}}
          transition={{ duration: 1.2, delay: delay + 0.4, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
    </motion.div>
  )
}
