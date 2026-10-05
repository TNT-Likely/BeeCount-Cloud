import type { ReadCategory, WorkspaceCategory } from '@beecount/api-client'

/**
 * 二级分类父子归属(#101)。
 *
 * server 侧 0013 起有 parent_sync_id 稳定 FK,但历史上父分类改名从不级联
 * 子分类的 parent_name,存量数据里存在"FK 有效但名字悬空"的行。前端分组 /
 * 守卫一律 **FK 优先、parent_name 兜底(仅老数据未回填 FK 时)**,父分类
 * 改名后子分类才不会从所有视图消失。
 *
 * server 侧 rename cascade / upsert 防御见 src/projection.py;这里只管展示
 * 与本地守卫,两条路径的语义必须一致。
 */

export type CategoryRowLike = Pick<
  ReadCategory,
  'id' | 'name' | 'kind' | 'parent_name'
> & {
  parent_sync_id?: string | null
}

/** 子分类的归属键:FK 有效 → 按 FK;否则按 (kind, parent_name) 兜底。 */
export function categoryChildGroupKey(row: CategoryRowLike): string | null {
  const fk = (row.parent_sync_id || '').trim()
  if (fk) return `id:${fk}`
  const parentName = (row.parent_name || '').trim().toLowerCase()
  if (parentName) return `name:${(row.kind || '').trim()}::${parentName}`
  return null
}

/** 父分类可能的归属键(FK 权威 + 名字兜底),查子分类时两个都查再按 id 去重 ——
 * 同一父级下可能同时挂着"有 FK 的子行"和"老数据只有名字的子行"。 */
export function categoryParentGroupKeys(parent: CategoryRowLike): string[] {
  const keys = [`id:${parent.id}`]
  const name = (parent.name || '').trim().toLowerCase()
  if (name) keys.push(`name:${(parent.kind || '').trim()}::${name}`)
  return keys
}

export function splitCategoryTree<T extends CategoryRowLike>(rows: T[]): {
  topLevel: T[]
  childGroups: Record<string, T[]>
} {
  const parentIds = new Set<string>()
  const parentNameKeys = new Set<string>()
  for (const row of rows) {
    parentIds.add(row.id)
    const name = (row.name || '').trim().toLowerCase()
    if (name) parentNameKeys.add(`name:${(row.kind || '').trim()}::${name}`)
  }
  const topLevel: T[] = []
  const childGroups: Record<string, T[]> = {}
  for (const row of rows) {
    const key = categoryChildGroupKey(row)
    // 归属键解析不到任何现存父行(FK 指向已删父分类的老孤儿)→ 按顶级渲染。
    // 绝不能让它挂在一个不存在的键上被静默吞掉 —— 那等于把 #101 的"从所有
    // 视图消失"换个姿势复现。
    const resolves =
      !!key &&
      (key.startsWith('id:') ? parentIds.has(key.slice(3)) : parentNameKeys.has(key))
    if (!resolves) {
      topLevel.push(row)
    } else {
      ;(childGroups[key as string] = childGroups[key as string] || []).push(row)
    }
  }
  return { topLevel, childGroups }
}

/** 取 parent 名下全部子分类(FK + 名字兜底两组合并,按 id 去重)。 */
export function childrenOfCategory<T extends CategoryRowLike>(
  childGroups: Record<string, T[]>,
  parent: CategoryRowLike,
): T[] {
  const out: T[] = []
  const seen = new Set<string>()
  for (const key of categoryParentGroupKeys(parent)) {
    for (const row of childGroups[key] || []) {
      if (seen.has(row.id)) continue
      seen.add(row.id)
      out.push(row)
    }
  }
  return out
}

/** WorkspaceCategory 是 ReadCategory 的超集,这里只要断言结构兼容即可。 */
export type AnyCategoryRow = ReadCategory | WorkspaceCategory
