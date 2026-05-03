import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { extremesInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

const currencySymbol = (code: string) =>
  ({ CNY: '¥', USD: '$', EUR: '€', GBP: '£', JPY: '¥', HKD: 'HK$' } as Record<string, string>)[code] || code + ' '

const formatDate = (iso: string) => {
  // ISO 'YYYY-MM-DD...' → 'M月D日'
  const d = iso.slice(0, 10)
  const [, m, day] = d.split('-')
  return `${parseInt(m, 10)}.${parseInt(day, 10)}`
}

/**
 * 极端时刻:故事卡片堆叠 — 最大支出 / 第一笔 / 最贵的一天。
 * 每张卡左侧时间戳竖排,右侧内容,垂直 stagger 入场,带 timeline 竖线。
 */
export function PageExtremes({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = extremesInsight(data)
  const sym = currencySymbol(data.ledgerCurrency)

  const cards: Array<{
    key: string
    labelKey: string
    date: string | null
    primary: string
    secondary: string
    accent: string
  }> = []

  if (data.largestExpense) {
    cards.push({
      key: 'largest',
      labelKey: TKEY.page8Largest,
      date: formatDate(data.largestExpense.happenedAt),
      primary: `${sym}${Math.round(data.largestExpense.amount).toLocaleString()}`,
      secondary:
        data.largestExpense.note ||
        data.largestExpense.categoryName ||
        data.largestExpense.accountName ||
        '—',
      accent: '#F87171',
    })
  }

  if (data.firstRecord) {
    cards.push({
      key: 'first',
      labelKey: TKEY.page8First,
      date: formatDate(data.firstRecord.happenedAt),
      primary:
        data.firstRecord.note ||
        data.firstRecord.categoryName ||
        '—',
      secondary: `${sym}${Math.round(data.firstRecord.amount).toLocaleString()}`,
      accent: '#FBBF24',
    })
  }

  if (data.mostExpensiveDay) {
    cards.push({
      key: 'mostExpensiveDay',
      labelKey: TKEY.page8MostExpensiveDay,
      date: formatDate(data.mostExpensiveDay.date),
      primary: `${sym}${Math.round(data.mostExpensiveDay.total).toLocaleString()}`,
      secondary: `${data.mostExpensiveDay.count} 笔`,
      accent: '#A78BFA',
    })
  }

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={350} />
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page8Title)}
        </h2>

        <div ref={ref} className="relative w-full">
          {/* 竖线 timeline */}
          <div className="absolute bottom-2 left-[68px] top-2 w-px bg-gradient-to-b from-white/40 via-white/20 to-transparent" />
          <div className="flex flex-col gap-6">
            {cards.map((c, i) => (
              <motion.div
                key={c.key}
                initial={{ opacity: 0, x: -20 }}
                animate={inView ? { opacity: 1, x: 0 } : {}}
                transition={{ duration: 0.6, delay: i * 0.2 + 0.2, ease: [0.16, 1, 0.3, 1] }}
                className="flex items-center gap-6"
              >
                <div className="w-14 shrink-0 text-right text-xs font-mono tracking-tight text-white/50">
                  {c.date}
                </div>
                <div
                  className="h-3 w-3 shrink-0 rounded-full ring-4 ring-[#1A1612]"
                  style={{ background: c.accent }}
                />
                <div className="flex-1 rounded-2xl border border-white/10 bg-white/5 px-5 py-4 backdrop-blur-sm">
                  <div className="text-xs uppercase tracking-widest text-white/50">
                    {t(c.labelKey)}
                  </div>
                  <div className="mt-1 text-xl font-semibold text-white sm:text-2xl">
                    {c.primary}
                  </div>
                  <div className="mt-1 text-sm text-white/60">{c.secondary}</div>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        {data.largestExpense && (
          <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
            <InsightLine text={t(insight.textKey, insight.args)} delay={1.4} />
          </div>
        )}
      </div>
    </div>
  )
}
