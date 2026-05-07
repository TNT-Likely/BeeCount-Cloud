import { useEffect, useRef, useState } from 'react'
import { motion, useInView } from 'framer-motion'

/**
 * 翻牌动效大数字:从 0 滚动到目标值,有数量级 / 弹性收尾。
 *
 * - 进入视口才触发(避免后台屏先跑完)
 * - 整数:不带小数;含 ¥ / % / 等前后缀通过 prefix / suffix 传入
 * - 时长 0.8s,easeOutCubic 派生
 */
export type BigNumberProps = {
  value: number
  /** 显示格式:'integer'(默认,整数)| 'currency'(千分位) | 'percent'(百分比) */
  format?: 'integer' | 'currency' | 'percent'
  prefix?: string
  suffix?: string
  duration?: number
  className?: string
  /** 字号,默认 96 (h1 级别) */
  size?: number
}

export function BigNumber({
  value,
  format = 'integer',
  prefix = '',
  suffix = '',
  duration = 0.9,
  className = '',
  size = 96,
}: BigNumberProps) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: '-10%' })
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    if (!inView) return
    let start: number | null = null
    let frame = 0
    const animate = (ts: number) => {
      if (start === null) start = ts
      const elapsed = (ts - start) / 1000
      const t = Math.min(elapsed / duration, 1)
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplay(value * eased)
      if (t < 1) {
        frame = requestAnimationFrame(animate)
      } else {
        setDisplay(value)
      }
    }
    frame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frame)
  }, [inView, value, duration])

  const formatted = formatValue(display, format)

  return (
    <motion.span
      ref={ref}
      className={`inline-block font-bold tabular-nums leading-none ${className}`}
      style={{ fontSize: size }}
      initial={{ opacity: 0, y: 12 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.4, ease: 'easeOut' }}
    >
      {prefix}
      {formatted}
      {suffix}
    </motion.span>
  )
}

function formatValue(v: number, format: 'integer' | 'currency' | 'percent'): string {
  switch (format) {
    case 'currency':
      return Math.round(v).toLocaleString()
    case 'percent':
      return `${v.toFixed(1)}`
    default:
      return Math.round(v).toLocaleString()
  }
}
