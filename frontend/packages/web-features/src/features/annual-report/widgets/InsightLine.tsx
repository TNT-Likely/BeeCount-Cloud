import { motion } from 'framer-motion'

/**
 * 洞察句:大字斜体衬线感,每个字 stagger 入场(类似打字机但更软)。
 *
 * 用于每屏的「智能解读」,字号 24-32px。
 */
export type InsightLineProps = {
  text: string
  className?: string
  delay?: number
}

export function InsightLine({ text, className = '', delay = 0 }: InsightLineProps) {
  // 简单按字符切(中文每字独立,英文按词反而割裂),保持 stagger 一致体感
  const chars = Array.from(text)
  return (
    <motion.div
      className={`inline-block ${className}`}
      initial="hidden"
      animate="show"
      variants={{
        hidden: {},
        show: {
          transition: {
            delayChildren: delay,
            staggerChildren: 0.03,
          },
        },
      }}
    >
      {chars.map((c, i) => (
        <motion.span
          key={i}
          variants={{
            hidden: { opacity: 0, y: 8, filter: 'blur(4px)' },
            show: { opacity: 1, y: 0, filter: 'blur(0px)' },
          }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
          className="inline-block"
        >
          {c === ' ' ? '\u00A0' : c}
        </motion.span>
      ))}
    </motion.div>
  )
}
