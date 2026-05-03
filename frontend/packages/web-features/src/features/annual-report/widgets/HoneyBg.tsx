import { motion } from 'framer-motion'

/**
 * 蜂蜜风格沉浸式背景:深底 + 渐变 + 浮动金色粒子。
 * 每屏可传不同 hue(蜂蜜橙 / 玫红 / 翠绿 / 蓝紫),色调换但保持质感。
 *
 * 粒子用 framer-motion 控制,GPU 加速,不影响主线程。
 */
export type HoneyBgProps = {
  /** HSL 主色相,默认蜂蜜橙(38) */
  hue?: number
  /** 粒子数量,默认 18 */
  particleCount?: number
  /** 是否显示径向光斑,默认 true */
  glow?: boolean
}

export function HoneyBg({ hue = 38, particleCount = 18, glow = true }: HoneyBgProps) {
  // 用 hue 派生主色和次色,保证视觉协调
  const primary = `hsl(${hue}, 75%, 55%)`
  const secondary = `hsl(${(hue + 35) % 360}, 65%, 50%)`

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* 基底:深色蜂蜡 */}
      <div className="absolute inset-0 bg-[#1A1612]" />

      {/* 径向光斑 */}
      {glow && (
        <>
          <div
            className="absolute -top-1/4 -left-1/4 h-[60vh] w-[60vw] rounded-full opacity-30 blur-3xl"
            style={{ background: `radial-gradient(circle, ${primary}, transparent 70%)` }}
          />
          <div
            className="absolute -bottom-1/4 -right-1/4 h-[55vh] w-[55vw] rounded-full opacity-20 blur-3xl"
            style={{ background: `radial-gradient(circle, ${secondary}, transparent 70%)` }}
          />
        </>
      )}

      {/* 浮动粒子 */}
      {Array.from({ length: particleCount }).map((_, i) => {
        const size = 4 + Math.random() * 8
        const startX = Math.random() * 100
        const startY = 100 + Math.random() * 20
        const drift = (Math.random() - 0.5) * 30
        const duration = 12 + Math.random() * 10
        const delay = Math.random() * duration
        return (
          <motion.div
            key={i}
            className="absolute rounded-full"
            style={{
              left: `${startX}%`,
              top: `${startY}%`,
              width: size,
              height: size,
              background: primary,
              opacity: 0.5,
              filter: 'blur(1px)',
            }}
            animate={{
              y: ['0vh', '-130vh'],
              x: [0, drift, -drift, 0],
              opacity: [0, 0.6, 0.6, 0],
            }}
            transition={{
              duration,
              delay,
              repeat: Infinity,
              ease: 'linear',
            }}
          />
        )
      })}
    </div>
  )
}
