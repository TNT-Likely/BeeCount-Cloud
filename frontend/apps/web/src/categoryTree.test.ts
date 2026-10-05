import { childrenOfCategory, splitCategoryTree } from '@beecount/web-features'
import { describe, expect, it } from 'vitest'

/**
 * 二级分类父子归属契约(#101)—— FK(parent_sync_id)优先、parent_name 仅
 * 老数据兜底、孤儿(FK 指向已删父分类)按顶级渲染绝不静默吞掉。
 *
 * 历史 bug:server 改名从不级联 parent_name,前端又只按名分组,父分类一改名
 * 子分类就从所有视图消失。
 *
 * categoryTree 函数只读 id/name/kind/parent_name/parent_sync_id,用 partial
 * 造数据再 cast,免得每条都填全(同 assetAggregation.test.ts 手法)。
 */
type Row = Parameters<typeof splitCategoryTree>[0][number]

function row(partial: {
  id: string
  name: string
  kind?: string
  level?: number
  parent_name?: string | null
  parent_sync_id?: string | null
}): Row {
  return {
    kind: 'expense',
    parent_name: null,
    parent_sync_id: null,
    ...partial,
  } as Row
}

const PARENT = row({ id: 'cat-parent', name: '家常菜', level: 1 })
const CHILD_FK = row({
  id: 'cat-child-fk',
  name: '红烧肉',
  level: 2,
  parent_name: '家常菜',
  parent_sync_id: 'cat-parent',
})
/** 老数据:FK 未回填,只有名字。 */
const CHILD_LEGACY = row({
  id: 'cat-child-legacy',
  name: '凉拌菜',
  level: 2,
  parent_name: '家常菜',
  parent_sync_id: null,
})

describe('splitCategoryTree', () => {
  it('FK 有效 → 子分类挂在父级下;无 parent 字段 → 顶级', () => {
    const { topLevel, childGroups } = splitCategoryTree([PARENT, CHILD_FK])
    expect(topLevel.map((r) => r.id)).toEqual(['cat-parent'])
    expect(childGroups['id:cat-parent'].map((r) => r.id)).toEqual(['cat-child-fk'])
  })

  it('父分类改名(子行 parent_name 悬空但 FK 有效)→ 仍挂在新名父级下', () => {
    // 模拟 server 端 cascade 失效的历史数据:子行 parent_name 是旧名
    const renamedParent = row({ id: 'cat-parent', name: '私房菜', level: 1 })
    const danglingChild = row({
      id: 'cat-child-fk',
      name: '红烧肉',
      level: 2,
      parent_name: '家常菜', // 旧名,已失配
      parent_sync_id: 'cat-parent',
    })
    const { topLevel, childGroups } = splitCategoryTree([renamedParent, danglingChild])
    expect(topLevel.map((r) => r.id)).toEqual(['cat-parent'])
    expect(childGroups['id:cat-parent']).toHaveLength(1)
  })

  it('老数据(无 FK)按 (kind, parent_name) 兜底挂父', () => {
    const { topLevel, childGroups } = splitCategoryTree([PARENT, CHILD_LEGACY])
    expect(topLevel.map((r) => r.id)).toEqual(['cat-parent'])
    expect(childGroups['name:expense::家常菜'].map((r) => r.id)).toEqual([
      'cat-child-legacy',
    ])
  })

  it('孤儿(FK 指向已删父分类)→ 按顶级渲染,不被静默吞掉', () => {
    const orphan = row({
      id: 'cat-orphan',
      name: '午餐',
      level: 2,
      parent_name: '餐饮',
      parent_sync_id: 'cat-deleted',
    })
    const { topLevel, childGroups } = splitCategoryTree([PARENT, CHILD_FK, orphan])
    expect(topLevel.map((r) => r.id)).toContain('cat-orphan')
    expect(Object.keys(childGroups)).not.toContain('id:cat-deleted')
  })

  it('名字兜底限定同 kind —— 不同 kind 同名父级不误挂', () => {
    const incomeParent = row({ id: 'cat-income', name: '家常菜', kind: 'income', level: 1 })
    const { topLevel, childGroups } = splitCategoryTree([incomeParent, CHILD_LEGACY])
    // expense 子分类不挂 income 父 → 双双顶级
    expect(topLevel.map((r) => r.id).sort()).toEqual(['cat-child-legacy', 'cat-income'])
    expect(Object.keys(childGroups)).toHaveLength(0)
  })
})

describe('childrenOfCategory', () => {
  it('FK 子行 + 老数据子行合并去重,同一个父级都能挂上', () => {
    const { childGroups } = splitCategoryTree([PARENT, CHILD_FK, CHILD_LEGACY])
    const children = childrenOfCategory(childGroups, PARENT)
    expect(children.map((r) => r.id).sort()).toEqual(['cat-child-fk', 'cat-child-legacy'])
  })
})
