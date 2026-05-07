import { motion } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { outroInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

export type PageOutroProps = {
  data: AnnualReportData
  onShare?: () => void
  onRestart: () => void
  onClose: () => void
}

/**
 * 终幕:致谢 + 三个动作按钮。背景一颗冉冉升起的「感谢之光」。
 */
export function PageOutro({ data, onShare, onRestart, onClose }: PageOutroProps) {
  const t = useT()
  const insight = outroInsight(data)

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={28} />
      {/* 中央光晕 */}
      <motion.div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[60vmin] w-[60vmin] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{
          background:
            'radial-gradient(circle, rgba(251,191,36,0.30) 0%, rgba(251,146,60,0.10) 40%, transparent 70%)',
          filter: 'blur(20px)',
        }}
        animate={{ opacity: [0.6, 1, 0.6], scale: [0.95, 1.05, 0.95] }}
        transition={{ duration: 4.5, repeat: Infinity, ease: 'easeInOut' }}
      />

      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-8 text-center sm:px-12">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          className="mb-8 text-6xl sm:text-7xl"
        >
          🐝
        </motion.div>
        <motion.h2
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className="mb-6 font-serif text-4xl font-bold text-white sm:text-6xl"
        >
          {t(TKEY.page12Title)}
        </motion.h2>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.9 }}
          className="mb-2 max-w-xl text-lg leading-relaxed text-white/80 sm:text-xl"
        >
          <InsightLine text={t(insight.textKey, insight.args)} delay={0.9} />
        </motion.div>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 1.6 }}
          className="mb-12 max-w-xl text-base text-white/60 sm:text-lg"
        >
          {t(TKEY.page12Body)}
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 2.0 }}
          className="flex flex-col gap-3 sm:flex-row sm:gap-4"
        >
          {onShare && (
            <button
              type="button"
              onClick={onShare}
              className="group inline-flex items-center justify-center gap-2 rounded-full bg-amber-400 px-6 py-3 text-sm font-semibold text-black transition hover:scale-105 hover:bg-amber-300 sm:px-8 sm:text-base"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="18" cy="5" r="3" />
                <circle cx="6" cy="12" r="3" />
                <circle cx="18" cy="19" r="3" />
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
              </svg>
              {t(TKEY.shareButton)}
            </button>
          )}
          <button
            type="button"
            onClick={onRestart}
            className="inline-flex items-center justify-center gap-2 rounded-full border border-white/20 bg-white/5 px-6 py-3 text-sm font-semibold text-white backdrop-blur-sm transition hover:bg-white/10 sm:px-8 sm:text-base"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            {t(TKEY.restartButton)}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center justify-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-white/60 transition hover:text-white sm:px-8 sm:text-base"
          >
            {t(TKEY.closeButton)}
          </button>
        </motion.div>
      </div>
    </div>
  )
}
