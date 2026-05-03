import { motion } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import type { AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 第一屏:欢迎页。年份大字 + 「你的钱画像」slogan + 滚动提示。
 */
export function PageWelcome({ data }: { data: AnnualReportData }) {
  const t = useT()
  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={38} />
      <div className="relative z-10 flex h-full flex-col items-center justify-center px-8 text-center">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: 'easeOut' }}
          className="mb-6 text-sm uppercase tracking-[0.4em] text-white/50"
        >
          BeeCount Year in Review
        </motion.div>
        <motion.div
          initial={{ opacity: 0, scale: 0.85 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.9, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
          className="mb-2 font-serif text-[12rem] font-bold leading-none tracking-tight tabular-nums sm:text-[16rem]"
          style={{
            background: 'linear-gradient(135deg, #F4A82B 0%, #FDE68A 50%, #F4A82B 100%)',
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          {data.year}
        </motion.div>
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.6 }}
          className="mb-3 font-serif text-3xl font-bold sm:text-5xl"
        >
          {t(TKEY.page1Title)}
        </motion.h1>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.7, delay: 0.9 }}
          className="max-w-md text-base text-white/70 sm:text-lg"
        >
          {t(TKEY.page1Subtitle, { ledger: data.ledgerName })}
        </motion.p>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1, y: [0, -8, 0] }}
          transition={{ delay: 1.4, opacity: { duration: 0.6 }, y: { duration: 1.6, repeat: Infinity, ease: 'easeInOut' } }}
          className="absolute bottom-12 text-xs text-white/40 sm:bottom-16"
        >
          {t(TKEY.page1Hint)}
        </motion.div>
      </div>
    </div>
  )
}
