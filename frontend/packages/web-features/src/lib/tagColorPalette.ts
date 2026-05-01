/**
 * 标签颜色预设(20 色)。
 *
 * 必须跟 app 端 (`lib/services/data/tag_seed_service.dart` 里的 `_defaultColors`)
 * 一一对齐。同色同名同顺序 —— 用户在 app 选 "#FF5722 深橙" 跟在 web 选同一格
 * 应该看到一模一样的色值。
 *
 * 改这个列表前先看 app 端那个 const 是不是也改了,两边必须同步,否则跨端
 * 颜色显示不一致。
 */
export const TAG_COLOR_PALETTE: readonly string[] = [
  '#FF5722', // 深橙
  '#E91E63', // 粉红
  '#9C27B0', // 紫色
  '#673AB7', // 深紫
  '#3F51B5', // 靛蓝
  '#2196F3', // 蓝色
  '#03A9F4', // 浅蓝
  '#00BCD4', // 青色
  '#009688', // 蓝绿
  '#4CAF50', // 绿色
  '#8BC34A', // 浅绿
  '#CDDC39', // 酸橙
  '#FFC107', // 琥珀
  '#FF9800', // 橙色
  '#795548', // 棕色
  '#607D8B', // 蓝灰
  '#F44336', // 红色
  '#00E676', // 亮绿
  '#FF4081', // 粉红强调
  '#536DFE', // 靛蓝强调
] as const

/** 颜色十六进制是否在调色板里(忽略大小写)。 */
export function isPaletteColor(hex: string | null | undefined): boolean {
  if (!hex) return false
  const upper = hex.toUpperCase()
  return TAG_COLOR_PALETTE.some((c) => c.toUpperCase() === upper)
}

/** 给新建标签随机选一个调色板色,跟 app 的 `getRandomColor` 行为一致。 */
export function pickRandomTagColor(): string {
  const index = Date.now() % TAG_COLOR_PALETTE.length
  return TAG_COLOR_PALETTE[index]
}

/**
 * 根据背景色亮度判断对比文字应该用黑还是白。
 * Luminance 阈值 0.6 跟 app 端 `_isLightColor` 对齐。
 */
export function tagTextColorOn(hex: string): '#000000' | '#FFFFFF' {
  const cleaned = hex.replace('#', '')
  if (cleaned.length !== 6) return '#FFFFFF'
  const r = parseInt(cleaned.slice(0, 2), 16) / 255
  const g = parseInt(cleaned.slice(2, 4), 16) / 255
  const b = parseInt(cleaned.slice(4, 6), 16) / 255
  // sRGB → relative luminance,简化版(不做 gamma 校正,误差可忽略)
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
  return luminance > 0.6 ? '#000000' : '#FFFFFF'
}
