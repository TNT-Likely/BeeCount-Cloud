/** Local storage key for user's custom primary color (hex). */
export const PRIMARY_COLOR_STORAGE_KEY = 'beecount.primary-color'

/** Default matches mobile `BeeTheme.honeyGold` hex. */
export const DEFAULT_PRIMARY_COLOR = '#F59E0B'

/**
 * 预设色板（对齐 mobile `personalize_page.dart` 的常用色，加上几种互补）。
 * color picker 里先摆这些，用户也可以用 `<input type="color">` 自定义。
 */
export const PRIMARY_COLOR_PRESETS: string[] = [
  '#F59E0B', // 蜂蜜金（默认）
  '#EF4444', // 玫瑰红
  '#EC4899', // 粉
  '#8B5CF6', // 紫
  '#3B82F6', // 蓝
  '#06B6D4', // 青
  '#10B981', // 翠绿
  '#22C55E', // 叶绿
  '#84CC16', // 柠檬
  '#F97316'  // 橙
]

/** 把 #RRGGBB 转 HSL（tailwind CSS variable 需要 `H S% L%` 格式，不带 hsl() 包装）。 */
export function hexToHsl(hex: string): { h: number; s: number; l: number } {
  const normalized = hex.trim().replace(/^#/, '')
  const full =
    normalized.length === 3
      ? normalized
          .split('')
          .map((c) => c + c)
          .join('')
      : normalized
  const r = parseInt(full.slice(0, 2), 16) / 255
  const g = parseInt(full.slice(2, 4), 16) / 255
  const b = parseInt(full.slice(4, 6), 16) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  let h = 0
  let s = 0
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    switch (max) {
      case r:
        h = (g - b) / d + (g < b ? 6 : 0)
        break
      case g:
        h = (b - r) / d + 2
        break
      case b:
        h = (r - g) / d + 4
        break
    }
    h /= 6
  }
  return {
    h: Math.round(h * 360),
    s: Math.round(s * 100),
    l: Math.round(l * 100)
  }
}

/**
 * 把 hex 应用到 `--primary` / `--ring` CSS 变量上。`--primary-foreground`
 * 继续由 light/dark mode 各自的 CSS 决定（亮/暗底的文本对比色不随 primary
 * 变）。这里 inline style 写在 <html>，优先级高于 styles.css 里的 `:root` /
 * `.dark` 定义，亮暗两个主题都会被统一覆盖。
 */
export function applyPrimaryColor(hex: string): void {
  if (typeof document === 'undefined') return
  const { h, s, l } = hexToHsl(hex)
  const value = `${h} ${s}% ${l}%`
  const root = document.documentElement
  root.style.setProperty('--primary', value)
  root.style.setProperty('--ring', value)
}

/** 初次加载时从 localStorage 读；没有则用默认色。不写 style.setProperty
 *  —— 调用方负责触发 applyPrimaryColor（通常放 provider 里）。 */
export function initialPrimaryColor(): string {
  if (typeof window === 'undefined') return DEFAULT_PRIMARY_COLOR
  const raw = window.localStorage.getItem(PRIMARY_COLOR_STORAGE_KEY)
  if (typeof raw === 'string' && /^#[0-9a-fA-F]{6}$/.test(raw)) return raw
  return DEFAULT_PRIMARY_COLOR
}

export function persistPrimaryColor(hex: string): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(PRIMARY_COLOR_STORAGE_KEY, hex)
}
