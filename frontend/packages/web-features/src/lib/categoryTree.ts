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
  /** 仅名字兜底分组用:server 按 (name, kind, level=1) 反查父级,这里同契约。
   * FK 分组不要求 level —— FK 本身就是权威。缺省视为候选(server 老数据
   * level 可能为 NULL)。 */
  level?: number | null
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

/** 只有 level=1(或缺省)的行能凭**名字**收养子分类 —— server 侧按名反查
 * 父级时也限定 level=1,两边契约一致;否则一个 L2 孤儿若与某子分类的
 * parent_name 同名,会在 UI 里错误"收养"它。 */
function isParentCandidateByName(row: CategoryRowLike): boolean {
  return row.level == null || Number(row.level) === 1
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
    if (name && isParentCandidateByName(row)) {
      parentNameKeys.add(`name:${(row.kind || '').trim()}::${name}`)
    }
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

/**
 * 父分类 tile 的**显示**笔数:本分类直接笔数 + 全部子分类笔数。用户看"这个
 * 分类有几笔"的直觉是含子分类的(预算用量在 server 侧也是这个口径上卷)。
 *
 * **只用于展示**:父级候选("先空再分":有直接交易的分类不能再挂子分类,
 * mobile 同款契约)和删除守卫仍必须用直接笔数 tx_count,上卷数会让有子分类
 * 的父级从父级候选里消失、删除拦截文案失真。
 */
export function subtreeTxCount<T extends CategoryRowLike>(
  parent: T,
  childGroups: Record<string, T[]>,
  txCountById: Record<string, number>,
): number {
  let total = txCountById[parent.id] ?? 0
  for (const child of childrenOfCategory(childGroups, parent)) {
    total += txCountById[child.id] ?? 0
  }
  return total
}
