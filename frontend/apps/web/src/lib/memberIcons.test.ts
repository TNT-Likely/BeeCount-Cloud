/**
 * 成员图标集(人物/家庭语义)契约。
 *
 * 重点守住两件事:
 * 1. 任何非法 / 空值都必须回退成默认人形图标 —— 不能把裸字符串喂给
 *    Material Symbols 字体(会渲成字面量文字,见 categoryIconMap 的安全网)。
 * 2. 每个图标名都必须落在字体子集 MATERIAL_SYMBOLS_SUBSET 里,并且
 *    index.html 的 icon_names 与子集同步 —— 漏一个就渲不出图标。
 */
import { describe, expect, it } from 'vitest'

import {
  MATERIAL_SYMBOLS_SUBSET,
  MEMBER_ICON_DEFAULT,
  MEMBER_ICONS,
  buildMaterialSymbolsFontUrl,
  resolveMemberIcon,
} from '@beecount/web-features'

import { readFileSync } from 'node:fs'

describe('memberIcons', () => {
  it('图标集非空、无重复、无空字符串', () => {
    expect(MEMBER_ICONS.length).toBeGreaterThan(0)
    expect(new Set(MEMBER_ICONS).size).toBe(MEMBER_ICONS.length)
    for (const name of MEMBER_ICONS) {
      expect(name.trim()).toBe(name)
      expect(name.length).toBeGreaterThan(0)
    }
  })

  it('默认图标本身必须在图标集里', () => {
    expect(MEMBER_ICONS).toContain(MEMBER_ICON_DEFAULT)
  })

  it('空值 / 未知值一律回退默认图标', () => {
    expect(resolveMemberIcon(null)).toBe(MEMBER_ICON_DEFAULT)
    expect(resolveMemberIcon(undefined)).toBe(MEMBER_ICON_DEFAULT)
    expect(resolveMemberIcon('')).toBe(MEMBER_ICON_DEFAULT)
    expect(resolveMemberIcon('   ')).toBe(MEMBER_ICON_DEFAULT)
    expect(resolveMemberIcon('not_a_real_icon')).toBe(MEMBER_ICON_DEFAULT)
  })

  it('合法图标名原样透传(含首尾空格容错)', () => {
    for (const name of MEMBER_ICONS) {
      expect(resolveMemberIcon(name)).toBe(name)
      expect(resolveMemberIcon(` ${name} `)).toBe(name)
    }
  })

  it('所有成员图标名都在 Material Symbols 字体子集里', () => {
    const subset = new Set(MATERIAL_SYMBOLS_SUBSET)
    for (const name of MEMBER_ICONS) {
      expect(subset.has(name), `${name} 不在字体子集里,会渲成字面量`).toBe(true)
    }
  })

  it('index.html 的 icon_names 与子集同步(否则线上字体缺字形)', () => {
    const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
    const url = buildMaterialSymbolsFontUrl()
    const expected = new URL(url).searchParams.get('icon_names') || ''
    expect(html).toContain(expected)
  })
})
