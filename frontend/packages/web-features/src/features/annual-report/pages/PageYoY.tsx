import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { yoyInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 跟去年比:3 行双柱图(今年 vs 去年)+ 增减箭头脉动。
 */
export function PageYoY({ data }: { data: AnnualReportData }) {
  const t = useT()
  const insight = yoyInsight(data)

  const items = [
    {
      label: t(TKEY.page3IncomeLabel),
      this: data.totalIncome,
      prev: data.prevYear.totalIncome,
      yoy: data.yoyIncomeChange,
      color: '#10B981',
    },
    {
      label: t(TKEY.page3ExpenseLabel),
      this: data.totalExpense,
      prev: data.prevYear.totalExpense,
      yoy: data.yoyExpenseChange,
      color: '#F43F5E',
    },
    {
      label: t(TKEY.page3SavingsLabel),
      this: data.netSavings,
      prev: data.prevYear.totalIncome - data.prevYear.totalExpense,
      yoy:
        data.prevYear.totalIncome - data.prevYear.totalExpense !== 0
          ? ((data.netSavings -
              (data.prevYear.totalIncome - data.prevYear.totalExpense)) /
              Math.max(
                Math.abs(data.prevYear.totalIncome - data.prevYear.totalExpense),
                1,
              )) *
            100
          : 0,
      color: '#F4A82B',
    },
  ]

  const max = Math.max(...items.flatMap((i) => [i.this, i.prev, 1]))
  const hasPrev = data.prevYear.totalIncome > 0 || data.prevYear.totalExpense > 0

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={350} />
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page3Title)}
        </h2>
        <div className="flex w-full flex-col gap-8">
          {items.map((item, i) => (
            <YoYRow key={item.label} item={item} max={max} index={i} hasPrev={hasPrev} t={t} />
          ))}
        </div>
        <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={0.7} />
        </div>
      </div>
    </div>
  )
}

function YoYRow({
  item,
  max,
  index,
  hasPrev,
  t,
}: {
  item: { label: string; this: number; prev: number; yoy: number; color: string }
  max: number
  index: number
  hasPrev: boolean
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const thisPct = Math.min(100, (item.this / max) * 100)
  const prevPct = hasPrev ? Math.min(100, (item.prev / max) * 100) : 0
  const up = item.yoy > 0
  const showYoy = hasPrev && item.prev !== 0

  return (
    <div ref={ref} className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between text-white">
        <span className="text-sm uppercase tracking-widest text-white/60">{item.label}</span>
        {showYoy && (
          <motion.span
            initial={{ opacity: 0, x: 10 }}
            animate={inView ? { opacity: 1, x: 0 } : {}}
            transition={{ delay: index * 0.15 + 0.5 }}
            className={`text-base font-semibold tabular-nums ${up ? 'text-emerald-300' : 'text-rose-300'}`}
          >
            {up ? '↑' : '↓'} {Math.abs(item.yoy).toFixed(0)}%
          </motion.span>
        )}
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="h-3 overflow-hidden rounded-full bg-white/10">
          <motion.div
            className="h-full rounded-full"
            style={{ background: item.color }}
            initial={{ width: 0 }}
            animate={inView ? { width: `${thisPct}%` } : {}}
            transition={{ duration: 1.0, delay: index * 0.15, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
        {hasPrev && (
          <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
            <motion.div
              className="h-full rounded-full bg-white/30"
              initial={{ width: 0 }}
              animate={inView ? { width: `${prevPct}%` } : {}}
              transition={{ duration: 1.0, delay: index * 0.15 + 0.2, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        )}
        <div className="flex justify-between text-xs text-white/40 tabular-nums">
          <span>
            {t(TKEY.page3VsPrev)} {hasPrev ? Math.round(item.prev).toLocaleString() : '—'}
          </span>
          <span>{Math.round(item.this).toLocaleString()}</span>
        </div>
      </div>
    </div>
  )
}
