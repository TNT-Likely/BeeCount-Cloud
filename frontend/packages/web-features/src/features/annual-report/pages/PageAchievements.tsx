import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { achievementsInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 成就墙:网格展示已解锁的成就。rare 成就有金色光晕脉动。
 */
export function PageAchievements({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = achievementsInsight(data)

  const empty = data.achievements.length === 0

  // achievement id → emoji
  const emojiMap: Record<string, string> = {
    'full-attendance': '👑',
    'frequent-recorder': '🏆',
    'half-year-keeper': '🎖️',
    'streak-100': '🔥',
    'streak-30': '⚡',
    'records-1k': '💎',
    'records-500': '⭐',
    'records-100': '🌟',
    'saver-pro': '💰',
    saver: '🏦',
    'frugal-progress': '🌱',
    'more-attentive': '✍️',
    'income-growth': '📈',
  }

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={48} />
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-10 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page11Title)}
        </h2>
        {empty ? (
          <p className="text-lg text-white/50">{t(TKEY.page11Empty)}</p>
        ) : (
          <div ref={ref} className="grid w-full grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4">
            {data.achievements.map((ach, i) => {
              const emoji = emojiMap[ach.id] || '🎯'
              return (
                <motion.div
                  key={ach.id}
                  initial={{ opacity: 0, y: 16, scale: 0.95 }}
                  animate={inView ? { opacity: 1, y: 0, scale: 1 } : {}}
                  transition={{
                    duration: 0.5,
                    delay: i * 0.08 + 0.2,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className={`relative flex flex-col gap-2 overflow-hidden rounded-2xl border p-4 backdrop-blur-sm sm:p-5 ${
                    ach.rare
                      ? 'border-amber-300/40 bg-gradient-to-br from-amber-500/15 to-orange-500/10'
                      : 'border-white/10 bg-white/5'
                  }`}
                >
                  {ach.rare && (
                    <motion.div
                      className="pointer-events-none absolute -inset-1 rounded-2xl"
                      style={{
                        background:
                          'radial-gradient(circle at 30% 20%, rgba(251,191,36,0.18), transparent 60%)',
                      }}
                      animate={{ opacity: [0.6, 1, 0.6] }}
                      transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}
                    />
                  )}
                  <div className="relative flex items-center gap-2">
                    <span className="text-3xl sm:text-4xl">{emoji}</span>
                    {ach.rare && (
                      <span className="rounded-full bg-amber-400/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-amber-300">
                        Rare
                      </span>
                    )}
                  </div>
                  <div className="relative text-base font-semibold text-white sm:text-lg">
                    {t(ach.titleKey)}
                  </div>
                  <div className="relative text-xs leading-relaxed text-white/60 sm:text-sm">
                    {t(ach.descKey)}
                  </div>
                </motion.div>
              )
            })}
          </div>
        )}
        <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.4} />
        </div>
      </div>
    </div>
  )
}
