import { formatBalanceCompact } from '../format'

/**
 * 通用金额展示组件。全站所有"金额"类文案都走这里，方便统一改：
 *
 * - 默认使用紧凑格式（`formatBalanceCompact`），对齐 mobile 的"X.X万 / X.Xk / X.XM"规则。
 * - `compact={false}` 时回退到千分号两位小数完整格式（比如详情页、表格合计行）。
 * - `sign`：`'none'`（默认）直接展示；`'positive'` 强制 + / -；`'negative'` 只在负值加 -。
 * - `tone`：`'default' | 'positive' | 'negative' | 'muted'`，语义色。
 * - `showCurrency`：是否在数字前展示币种符号（默认不展示，避免和 pill / 分组标题重复）。
 * - `size`：预设字号。业务不直接指定 tailwind text-\* 以免各处分散。
 *
 * 使用示例：
 *   <Amount value={1234567.89} />                     → ¥123.5万
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
  sign = 'auto'
}: AmountProps) {
  const text = renderAmount({ value, currency, compact, showCurrency, sign })
  const classes = [
    'font-mono tabular-nums',
    SIZE_CLASS[size],
    TONE_CLASS[tone],
    bold ? 'font-bold' : '',
    className || ''
  ]
    .filter(Boolean)
    .join(' ')
  return <span className={classes}>{text}</span>
}

function renderAmount({
  value,
  currency,
  compact,
  showCurrency,
  sign
}: {
  value: number | null | undefined
  currency?: string | null
  compact: boolean
  showCurrency: boolean
  sign: 'auto' | 'always' | 'never'
}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const isNeg = value < 0
  const absVal = Math.abs(value)
  const cur = showCurrency ? currency || 'CNY' : null

  let body: string
  if (compact) {
    body = formatBalanceCompact(absVal, cur, {
      chinese: (cur || currency || 'CNY').toUpperCase() === 'CNY'
    })
  } else {
    const formatted = absVal.toLocaleString('zh-CN', {
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
