import { describe, expect, it } from 'vitest'

import {
  EXPENSE_ICON_GROUPS,
  INCOME_ICON_GROUPS,
  ALL_GROUPED_ICON_KEYS,
  getIconGroupsByKind,
  resolveMaterialIconName,
} from '@beecount/web-features'

// 静态安全网:分组里出现的每个 stored key 都必须能被 Material Symbols 字体渲
// 出来(即在 resolveMaterialIconName 里命中 KNOWN_NAMES / FLUTTER_RENAMES),
// 否则会 fallback 成 'category' 字面文本。新增图标分组时,这条测试能在 CI 拦住
// "忘记把 key 加进 KNOWN_NAMES 导致图标变文字" 的回归。

describe('category icon groups', () => {
  it('every grouped icon key resolves to a real glyph (never falls back to "category")', () => {
    expect(ALL_GROUPED_ICON_KEYS.length).toBeGreaterThan(0)
    // 'category' 是合法的 stored 值(代表"其他/未分类"),它正好等于兜底名,
    // 不算缺失;真正的缺失是「非 category 的 key 却 fallback 成 category」。
    const bad: string[] = []
    for (const key of ALL_GROUPED_ICON_KEYS) {
      if (key !== 'category' && resolveMaterialIconName(key) === 'category') {
        bad.push(key)
      }
    }
    expect(bad, `这些图标 key 在字体子集里找不到: ${bad.join(', ')}`).toEqual([])
  })

  it('pins a "常用" (commonly-used) group at the top of both expense and income pickers', () => {
    const expense = getIconGroupsByKind('expense')
    const income = getIconGroupsByKind('income')
    expect(expense[0]?.labelKey).toBe('categories.iconGroup.common')
    expect(income[0]?.labelKey).toBe('categories.iconGroup.common')
    expect(expense[0]?.icons.length).toBeGreaterThan(0)
    expect(income[0]?.icons.length).toBeGreaterThan(0)
  })

  it('common group keys never collide with the literal "category" fallback', () => {
    const common = [...EXPENSE_ICON_GROUPS[0].icons, ...INCOME_ICON_GROUPS[0].icons]
    for (const item of common) {
      expect(resolveMaterialIconName(item.key)).not.toBe('category')
    }
  })
})
