import type { ReadCategory } from '@beecount/api-client'

type CategoryLink = Pick<ReadCategory, 'id' | 'name' | 'kind' | 'level' | 'parent_name' | 'parent_sync_id'>

/** Stable identity is authoritative. Names only resolve clients without an ID. */
export function categoryParent<T extends CategoryLink>(row: CategoryLink, rows: readonly T[]): T | undefined {
  const candidates = rows.filter((parent) =>
    parent.id !== row.id && parent.kind === row.kind &&
    Number(parent.level ?? 1) === 1 && !parent.parent_sync_id && !parent.parent_name?.trim(),
  )
  const id = row.parent_sync_id?.trim()
  if (id) return candidates.find((parent) => parent.id === id)
  const name = row.parent_name?.trim()
  if (!name) return undefined
  const matches = candidates.filter((parent) => parent.name.trim() === name)
  return matches.length === 1 ? matches[0] : undefined
}

export function categoryIsChild(row: CategoryLink, parent: CategoryLink): boolean {
  if (row.id === parent.id) return false
  const id = row.parent_sync_id?.trim()
  if (id) return id === parent.id
  return row.kind === parent.kind && !!row.parent_name?.trim() && row.parent_name.trim() === parent.name.trim()
}

/** Keep unresolvable historical rows visible so they can be reattached/deleted. */
export function groupCategories<T extends CategoryLink>(rows: readonly T[]) {
  const topLevels: T[] = []
  const childrenByParent: Record<string, T[]> = {}
  for (const row of rows) {
    const parent = categoryParent(row, rows)
    if (parent) (childrenByParent[parent.id] ??= []).push(row)
    else topLevels.push(row)
  }
  return { topLevels, childrenByParent }
}
