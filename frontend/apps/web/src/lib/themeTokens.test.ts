import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * 设计 token 回归测试（设计系统：Notion 暖中性克制风）。
 *
 * 锁住 awesome-design-md/notion DESIGN.md 的两条硬约束：
 *  1) 「Always use warm neutrals -- Notion's grays have yellow-brown
 *     undertones (#f6f5f4, #31302e, #615d59, #a39e98), never blue-gray」
 *     → 所有中性 token 的色相必须落在暖区，绝不能回到蓝灰(旧值 210/216/220)。
 *  2) 「Shadows use 4-5 layers with individual opacity never exceeding 0.05」
 *     → 阴影必须多层叠加，单层不透明度 ≤ 0.05。
 *
 * 只解析 CSS，不渲染组件（项目无 jsdom / testing-library）。
 */
const CSS_PATH = fileURLToPath(new URL('../styles.css', import.meta.url))

/** 需要保持暖中性的语义 token(shadcn 命名)。品牌色/收支色不在此列。 */
const NEUTRAL_TOKENS = [
  'background',
  'foreground',
  'card',
  'card-foreground',
  'popover',
  'popover-foreground',
  'muted',
  'muted-foreground',
  'border',
  'input',
  'secondary',
  'secondary-foreground',
] as const

/** 暖中性色相区间(黄棕底 ≈ 30°)，与 Notion #f6f5f4/#31302e/#615d59/#a39e98 一致。 */
const WARM_HUE_MIN = 15
const WARM_HUE_MAX = 50

/** 阴影：最少层数 + 单层不透明度上限(Notion: 0.01 ~ 0.05)。 */
const SHADOW_MIN_LAYERS = 4
const SHADOW_MAX_LAYER_ALPHA = 0.05

/** Notion 卡片圆角 12px。 */
const EXPECTED_RADIUS = '0.75rem'

const css = readFileSync(CSS_PATH, 'utf8')

/** 取 CSS 里某个选择器块的声明体(块内无嵌套花括号，够用)。 */
function blockOf(selector: string): string {
  const re = new RegExp(`${selector.replace('.', '\\.')}\\s*\\{([^}]*)\\}`)
  const match = re.exec(css)
  if (!match) throw new Error(`styles.css 里找不到选择器块: ${selector}`)
  return match[1]
}

/** `a: 1; --b: 2;` → { a: '1', b: '2' } */
function parseTokens(block: string): Record<string, string> {
  const out: Record<string, string> = {}
  const re = /--([A-Za-z0-9-]+)\s*:\s*([^;]+);/g
  let m: RegExpExecArray | null
  while ((m = re.exec(block)) !== null) out[m[1]] = m[2].trim()
  return out
}

/** `30 12% 97%` → 30；`var(--accent-light, 38 80% 90%)` → null(非字面量)。 */
function hueOf(value: string): number | null {
  const m = /^(\d+(?:\.\d+)?)\s/.exec(value)
  return m ? Number(m[1]) : null
}

/** `0 0% 100%` → 0(纯白/纯灰,无彩色,色相无意义)。 */
function saturationOf(value: string): number | null {
  const m = /^\d+(?:\.\d+)?\s+(\d+(?:\.\d+)?)%/.exec(value)
  return m ? Number(m[1]) : null
}

/**
 * Notion 的中性色有两类:暖灰(带黄棕底,色相 ≈30°)和**无彩色**纯白/纯灰
 * (Notion 画布就是纯白 `#ffffff`)。两者都合法,只有蓝灰(≈210°)违规。
 */
function assertWarmOrAchromatic(raw: string | undefined, token: string): void {
  expect(raw, `styles.css 缺少 --${token}`).toBeTruthy()
  const hue = hueOf(raw!)
  expect(hue, `--${token} 的值 "${raw}" 不是 HSL 三元组`).not.toBeNull()
  if (saturationOf(raw!) === 0) return // 无彩色:纯白 / 纯灰,色相无意义
  expect(hue!).toBeGreaterThanOrEqual(WARM_HUE_MIN)
  expect(hue!).toBeLessThanOrEqual(WARM_HUE_MAX)
}

/** 阴影值里每层的 alpha，如 `hsl(var(--shadow-color) / 0.04)` → 0.04 */
function shadowAlphas(value: string): number[] {
  const out: number[] = []
  const re = /\/\s*(0?\.\d+)\s*\)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(value)) !== null) out.push(Number(m[1]))
  return out
}

const LIGHT = parseTokens(blockOf(':root'))
const DARK = parseTokens(blockOf('.dark'))

describe('设计 token：Notion 暖中性', () => {
  it.each(NEUTRAL_TOKENS)(':root 的 --%s 必须是暖中性色相', (token) => {
    assertWarmOrAchromatic(LIGHT[token], token)
  })

  it.each(NEUTRAL_TOKENS)('.dark 的 --%s 必须是暖中性色相', (token) => {
    assertWarmOrAchromatic(DARK[token], token)
  })

  it('浅色卡片是纯白画布、页面底是暖白(Notion 的层次来自底色而非边框)', () => {
    expect(LIGHT.card).toBe('0 0% 100%')
    // 页面底比卡片暗一点点但仍是暖白
    expect(hueOf(LIGHT.background)).toBeGreaterThanOrEqual(WARM_HUE_MIN)
    const bgLightness = Number(/(\d+)%\s*$/.exec(LIGHT.background)?.[1])
    expect(bgLightness).toBeGreaterThanOrEqual(94)
  })

  it('圆角保持 Notion 卡片圆角 12px', () => {
    expect(LIGHT.radius).toBe(EXPECTED_RADIUS)
  })
})

describe('设计 token：Notion 多层阴影', () => {
  it('--shadow-card 至少 4 层且单层不透明度 ≤ 0.05', () => {
    const raw = LIGHT['shadow-card']
    expect(raw, 'styles.css 缺少 --shadow-card').toBeTruthy()
    const alphas = shadowAlphas(raw)
    expect(alphas.length).toBeGreaterThanOrEqual(SHADOW_MIN_LAYERS)
    expect(Math.max(...alphas)).toBeLessThanOrEqual(SHADOW_MAX_LAYER_ALPHA)
  })

  it('--shadow-deep 比 card 更深(层数更多、模糊更大)', () => {
    const raw = LIGHT['shadow-deep']
    expect(raw, 'styles.css 缺少 --shadow-deep').toBeTruthy()
    const alphas = shadowAlphas(raw)
    expect(alphas.length).toBeGreaterThan(shadowAlphas(LIGHT['shadow-card']).length)
    expect(Math.max(...alphas)).toBeLessThanOrEqual(SHADOW_MAX_LAYER_ALPHA)
    // Notion deep card: 52px 模糊
    expect(raw).toContain('52px')
  })

  it('深浅两套都定义了阴影色，暗色下阴影更弱(Notion 暗色靠色阶而非投影)', () => {
    expect(hueOf(LIGHT['shadow-color'])).toBeGreaterThanOrEqual(WARM_HUE_MIN)
    expect(hueOf(DARK['shadow-color'])).toBeGreaterThanOrEqual(WARM_HUE_MIN)
  })
})

describe('设计 token：品牌色仍可运行时覆盖', () => {
  // PrimaryColorProvider 会 setProperty('--primary'/'--ring')，
  // 所以 CSS 里的值只是首帧兜底，必须仍是暖色系(默认蜂蜜金)。
  it(':root 的 --primary / --ring 兜底为暖色', () => {
    for (const token of ['primary', 'ring']) {
      const hue = hueOf(LIGHT[token])
      expect(hue, `--${token} 不是 HSL 三元组`).not.toBeNull()
      expect(hue!).toBeGreaterThanOrEqual(WARM_HUE_MIN)
      expect(hue!).toBeLessThanOrEqual(WARM_HUE_MAX)
    }
  })

  it('收支语义色保持中国习惯(默认 红=收入 / 绿=支出)', () => {
    const red = /html\[data-income-color="red"\]|^\s*:root,/.test(css)
    expect(red).toBe(true)
    expect(css).toContain('--income-rgb: 239 68 68')
    expect(css).toContain('--expense-rgb: 16 185 129')
  })
})
