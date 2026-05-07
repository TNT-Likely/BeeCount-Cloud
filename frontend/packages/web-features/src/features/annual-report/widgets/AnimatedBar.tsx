import { motion, useInView } from 'framer-motion'
import { useRef } from 'react'

/**
 * 横向条形图竞速:多行,每行从 0 长大到目标百分比,stagger 入场。
 * 用于 Top 5 分类排行 / 收入分类排行等。
 */
export type AnimatedBarItem = {
  label: string
  value: number
  /** 占总量百分比 0-100 */
  percent: number
  /** 该项颜色,通常 brandColor 系列 */
  color: string
  /** emoji 或 icon char(可选) */
  emoji?: string
}

export type AnimatedBarsProps = {
  items: AnimatedBarItem[]
  formatValue?: (v: number) => string
  className?: string
}

export function AnimatedBars({ items, formatValue, className = '' }: AnimatedBarsProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const inView = useInView(containerRef, { once: true, margin: '-15%' })

  return (
    <div ref={containerRef} className={`flex flex-col gap-3 ${className}`}>
      {items.map((item, i) => (
        <div key={item.label} className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between text-white">
            <div className="flex items-center gap-2 text-base font-medium">
              {item.emoji ? <span className="text-xl">{item.emoji}</span> : null}
              <span className="text-white/90">{item.label}</span>
            </div>
            <div className="text-sm tabular-nums text-white/70">
              {formatValue ? formatValue(item.value) : item.value.toLocaleString()}
              <span className="ml-2 text-xs text-white/50">{item.percent.toFixed(0)}%</span>
            </div>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-white/10">
            <motion.div
              className="h-full rounded-full"
              style={{ background: item.color }}
              initial={{ width: 0 }}
              animate={inView ? { width: `${item.percent}%` } : {}}
              transition={{
                duration: 1.0,
                delay: i * 0.12 + 0.2,
                ease: [0.16, 1, 0.3, 1], // easeOutExpo
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
