import { useEffect, useRef, useState, type CSSProperties } from 'react'

import { useReducedMotion } from 'framer-motion'

import { useLocale, useT } from '@beecount/ui'

import { formatBalanceCompact } from '../format'

/**
 * 通用金额展示组件。全站所有"金额"类文案都走这里，方便统一改：
 *
 * - 默认使用紧凑格式（`formatBalanceCompact`），对齐 mobile 的"X.X万 / X.Xk / X.XM"规则。
 * - 紧凑单位跟随 **UI 语言**而非币种:中文(zh-CN / zh-TW)用「万 / 萬」,英文用
 *   "k / M" —— 这样英文界面下哪怕是 CNY 账本也不会再蹦出「247.25万」这种不符合
 *   英文区阅读习惯的写法(见 #英文金额统计 issue)。
 * - `compact={false}` 时回退到千分号两位小数完整格式（比如详情页、表格合计行）。
 * - `sign`：`'none'`（默认）直接展示；`'positive'` 强制 + / -；`'negative'` 只在负值加 -。
 * - `tone`：`'default' | 'positive' | 'negative' | 'muted'`，语义色。
 * - `showCurrency`：是否在数字前展示币种符号（默认不展示，避免和 pill / 分组标题重复）。
 * - `size`：预设字号。业务不直接指定 tailwind text-\* 以免各处分散。
 *
 * 使用示例(中文 locale)：
 *   <Amount value={1234567.89} />                     → ¥123.5万   （英文 locale → ¥1.2M）
 *   <Amount value={-980} tone="negative" />           → -980.00
 *   <Amount value={0} compact={false} showCurrency /> → ¥0.00
 */
export type AmountTone = 'default' | 'positive' | 'negative' | 'muted'
export type AmountSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl'

const SIZE_CLASS: Record<AmountSize, string> = {
  xs: 'text-[11px]',
  sm: 'text-xs',
  md: 'text-sm',
  lg: 'text-base',
  xl: 'text-lg',
  '2xl': 'text-2xl',
  '3xl': 'text-3xl',
  '4xl': 'text-4xl sm:text-5xl'
}

// positive = 收入，negative = 支出。两者的底层颜色由 tailwind theme 的
// `income` / `expense` token 决定，token 读 CSS var，CSS var 由 <html
// data-income-color="red|green"> 切换。换句话说：一旦 mobile 切了颜色
// 方案，这里不用动，全站 Amount 自动跟随。
const TONE_CLASS: Record<AmountTone, string> = {
  default: 'text-foreground',
  positive: 'text-income',
  negative: 'text-expense',
  muted: 'text-muted-foreground'
}

type AmountProps = {
  value: number | null | undefined
  currency?: string | null
  compact?: boolean
  showCurrency?: boolean
  tone?: AmountTone
  size?: AmountSize
  bold?: boolean
  className?: string
  /**
   * `'auto'`：正数不加、负数显示 -（默认）；
   * `'always'`：正数显示 + / 负数显示 -；
   * `'never'`：永远不加符号。
   */
  sign?: 'auto' | 'always' | 'never'
  /**
   * 数字是否做"上下翻页"(odometer 滚轮)动画 —— 每个数字位垂直滚动到目标值,
   * 非数字字符(¥ / 万 / k·M / 小数点 / 正负号)保持不动。先用 renderAmount 算出
   * 终值字符串再逐位滚动,因此业务格式完整保留,也不会跨"万"档位时抖动。
   * 默认 false —— 仅在仪表盘大数字等高价值位置显式开启,列表/表格保持安静。
   * 自动尊重 prefers-reduced-motion(命中时直接展示终值,不滚动)。
   */
  animate?: boolean
  /** 翻滚时长(秒),默认 0.6。 */
  animateDuration?: number
  /** 首次翻滚前的延迟(秒),默认 0 —— 用于"卡片先入场、数字再滚动"的编排;后续数值变化不受影响,立即滚。 */
  animateDelay?: number
}

export function Amount({
  value,
  currency,
  compact = true,
  showCurrency = false,
  tone = 'default',
  size = 'md',
  bold = false,
  className,
  sign = 'auto',
  animate = false,
  animateDuration = 0.6,
  animateDelay = 0
}: AmountProps) {
  // chinese 决定算法分支(中文按「万」折算 / 英文按 k·M);万字字形(简体「万」、
  // 繁体「萬」)是纯文案,统一从 i18n 取,不在 JS 里硬编码。英文 locale 下这个 key
  // 返回 'k',但英文分支用不到 wanUnit,无副作用。
  const { locale } = useLocale()
  const t = useT()
  const chinese = locale.startsWith('zh')
  const wanUnit = t('common.unit.10k')

  const text = renderAmount({ value, currency, compact, showCurrency, sign, chinese, wanUnit })
  const classes = [
    'font-mono tabular-nums',
    SIZE_CLASS[size],
    TONE_CLASS[tone],
    bold ? 'font-bold' : '',
    className || ''
  ]
    .filter(Boolean)
    .join(' ')

  // "上下翻页":只在显式开启 + 允许动效 + 是有限数值时启用,否则渲染静态文本。
  const reduceMotion = useReducedMotion()
  const numeric = typeof value === 'number' && Number.isFinite(value)
  const shouldRoll = animate && !reduceMotion && numeric

  if (shouldRoll) {
    return (
      <span className={classes}>
        <RollingNumber text={text} duration={animateDuration} delay={animateDelay} />
      </span>
    )
  }
  return <span className={classes}>{text}</span>
}

// 屏幕阅读器读完整终值;逐位滚动的可见部分全部 aria-hidden。
const SR_ONLY: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0
}

/**
 * 把已格式化好的金额字符串(如 "¥1.2万" / "-$50k" / "¥980.00")逐字符渲染:
 * 数字位用 odometer 滚轮上下翻页,其余字符(符号 / 小数点 / 万·k·M)保持静止。
 * 滚的是"终值字符串的每一位",所以不会出现 count-up 跨档位时
 * "¥9999.00 → ¥1万" 那种宽度/格式突变的抖动。
 */
function RollingNumber({
  text,
  duration,
  delay
}: {
  text: string
  duration: number
  delay: number
}) {
  return (
    <span style={{ lineHeight: 1, whiteSpace: 'nowrap' }}>
      <span style={SR_ONLY}>{text}</span>
      {text.split('').map((ch, i) =>
        ch >= '0' && ch <= '9' ? (
          <RollingDigit key={i} digit={Number(ch)} duration={duration} delay={delay} />
        ) : (
          <span
            key={i}
            aria-hidden
            style={{ display: 'inline-block', verticalAlign: 'bottom', lineHeight: 1 }}
          >
            {ch}
          </span>
        )
      )}
    </span>
  )
}

/** 单个数字位:0-9 竖排成一列,translateY 把目标数字滚动到可视窗口。 */
function RollingDigit({
  digit,
  duration,
  delay
}: {
  digit: number
  duration: number
  delay: number
}) {
  // 初值 0;首次挂载等 delay 秒后再滚(让卡片入场先走完),之后的数值变化立即滚。
  const [shown, setShown] = useState(0)
  const mounted = useRef(false)
  useEffect(() => {
    if (mounted.current) {
      setShown(digit)
      return
    }
    mounted.current = true
    const id = setTimeout(() => setShown(digit), delay * 1000)
    return () => clearTimeout(id)
  }, [digit, delay])
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        height: '1em',
        overflow: 'hidden',
        verticalAlign: 'bottom',
        lineHeight: 1
      }}
    >
      <span
        style={{
          display: 'block',
          transform: `translateY(-${shown}em)`,
          transition: `transform ${duration}s cubic-bezier(0.16, 1, 0.3, 1)`,
          willChange: 'transform'
        }}
      >
        {Array.from({ length: 10 }, (_, i) => (
          <span key={i} style={{ display: 'block', height: '1em', lineHeight: 1 }}>
            {i}
          </span>
        ))}
      </span>
    </span>
  )
}

function renderAmount({
  value,
  currency,
  compact,
  showCurrency,
  sign,
  chinese,
  wanUnit
}: {
  value: number | null | undefined
  currency?: string | null
  compact: boolean
  showCurrency: boolean
  sign: 'auto' | 'always' | 'never'
  chinese: boolean
  wanUnit: string
}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const isNeg = value < 0
  const absVal = Math.abs(value)
  const cur = showCurrency ? currency || 'CNY' : null

  let body: string
  if (compact) {
    body = formatBalanceCompact(absVal, cur, { chinese, wanUnit })
  } else {
    const formatted = absVal.toLocaleString(chinese ? 'zh-CN' : 'en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })
    body = cur ? `${currencySymbol(cur)}${formatted}` : formatted
  }

  if (sign === 'always') return (isNeg ? '-' : '+') + body
  if (sign === 'never') return body
  return isNeg ? `-${body}` : body
}

function currencySymbol(code: string): string {
  switch (code.toUpperCase()) {
    case 'CNY':
    case 'JPY':
      return '¥'
    case 'USD':
      return '$'
    case 'EUR':
      return '€'
    case 'HKD':
      return 'HK$'
    case 'GBP':
      return '£'
    default:
      return ''
  }
}
