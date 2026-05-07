import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { hoursInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 时段分布:4 个 bucket(深夜 / 早晨 / 下午 / 傍晚),emoji + 大数字 + 增长条。
 * 高峰段高亮 + 顶部漂浮一句洞察。
 */
export function PageHours({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = hoursInsight(data)

  const total = data.hourBuckets.reduce((s, b) => s + b.count, 0)
  const peak = data.hourBuckets.reduce((m, b) => (b.count > m.count ? b : m), data.hourBuckets[0])

  const meta: Record<string, { emoji: string; labelKey: string; color: string; range: string }> = {
    night: { emoji: '🌙', labelKey: TKEY.page6BucketNight, color: '#A78BFA', range: '0–6' },
    morning: { emoji: '☀️', labelKey: TKEY.page6BucketMorning, color: '#FBBF24', range: '6–12' },
    afternoon: { emoji: '🌤️', labelKey: TKEY.page6BucketAfternoon, color: '#FB923C', range: '12–18' },
    evening: { emoji: '🌃', labelKey: TKEY.page6BucketEvening, color: '#60A5FA', range: '18–24' },
  }

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={270} />
      <div className="relative z-10 mx-auto flex h-full max-w-5xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page6Title)}
        </h2>
        <div ref={ref} className="grid w-full grid-cols-4 gap-4 sm:gap-6">
          {data.hourBuckets.map((b, i) => {
            const m = meta[b.bucket]
            const pct = total > 0 ? (b.count / total) * 100 : 0
            const isPeak = peak && b.bucket === peak.bucket && b.count > 0
            return (
              <motion.div
                key={b.bucket}
                initial={{ opacity: 0, y: 16 }}
                animate={inView ? { opacity: 1, y: 0 } : {}}
                transition={{ duration: 0.5, delay: i * 0.1 + 0.2, ease: [0.16, 1, 0.3, 1] }}
                className={`relative flex flex-col items-center gap-3 rounded-2xl border p-5 backdrop-blur-sm transition ${
                  isPeak ? 'border-white/40 bg-white/10' : 'border-white/10 bg-white/5'
                }`}
              >
                <div className="text-4xl sm:text-5xl">{m.emoji}</div>
                <div className="text-xs uppercase tracking-widest text-white/50">{m.range}</div>
                <div className="text-sm font-medium text-white/80">{t(m.labelKey)}</div>
                <div
                  className="mt-1 text-3xl font-bold tabular-nums sm:text-4xl"
                  style={{ color: m.color }}
                >
                  {b.count}
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                  <motion.div
                    className="h-full rounded-full"
                    style={{ background: m.color }}
                    initial={{ width: 0 }}
                    animate={inView ? { width: `${pct}%` } : {}}
                    transition={{ duration: 1.0, delay: i * 0.1 + 0.5, ease: [0.16, 1, 0.3, 1] }}
                  />
                </div>
                <div className="text-xs text-white/50">{pct.toFixed(0)}%</div>
                {isPeak && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.6 }}
                    animate={inView ? { opacity: 1, scale: 1 } : {}}
                    transition={{ delay: 1.4 }}
                    className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-white/90 px-2 py-0.5 text-[10px] font-semibold text-black"
                  >
                    PEAK
                  </motion.div>
                )}
              </motion.div>
            )
          })}
        </div>

        <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.6} />
        </div>
      </div>
    </div>
  )
}
