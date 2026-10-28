/**
 * 银行/支付工具图标徽章(0026)。
 *
 * 品牌色圆角方块 + 单个汉字/字母,识别国内银行的常见做法。未注册 key
 * 返回 null —— 调用方回退到按 account_type 的默认图标。
 */
import { bankIconOf } from '../lib/bankIcons'

export function BankIconBadge({
  icon,
  size = 28,
  radius = 10,
  className
}: {
  /** 账户 icon 字段(key);未注册/空 → 不渲染。 */
  icon: string | null | undefined
  /** 徽章边长 px。 */
  size?: number
  /** 圆角 px。 */
  radius?: number
  className?: string
}) {
  const def = bankIconOf(icon)
  if (!def) return null
  return (
    <span
      className={`inline-flex shrink-0 select-none items-center justify-center font-bold leading-none ${className || ''}`}
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        background: def.color,
        color: def.fg || '#ffffff',
        fontSize: Math.round(size * 0.52),
        boxShadow: `0 1px 3px ${def.color}55`
      }}
      title={def.label}
      aria-label={def.label}
    >
      {def.glyph}
    </span>
  )
}

/**
 * 银行卡磁贴里用的「白底 + 品牌色字」小徽章 —— 磁贴背景是分组渐变色的,
 * 白色小方块承托品牌色字符,跟原 TypeIcon 的白底盒子视觉一致。
 */
export function BankGlyphOnWhite({
  icon,
  size = 16
}: {
  icon: string | null | undefined
  size?: number
}) {
  const def = bankIconOf(icon)
  if (!def) return null
  return (
    <span
      className="inline-flex items-center justify-center font-bold leading-none"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.72),
        color: def.color
      }}
      title={def.label}
    >
      {def.glyph}
    </span>
  )
}
