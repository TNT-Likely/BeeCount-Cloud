import { describe, expect, it } from 'vitest'
import type { WorkspaceCategory } from '@beecount/api-client'
import { categoryIsChild, categoryParent, groupCategories } from '../../../packages/web-features/src/lib/categoryHierarchy'

const category = (id: string, name: string, extra: Partial<WorkspaceCategory> = {}): WorkspaceCategory => ({
  id, name, kind: 'expense', level: 1, sort_order: 0, icon: null, icon_type: null,
  parent_name: null, parent_sync_id: null, last_change_id: 0, tx_count: 0,
  ledger_id: null, ledger_name: null, created_by_user_id: null, created_by_email: null, ...extra,
})

describe('category parent identity', () => {
  it('keeps renamed children grouped by ID with a stale display name', () => {
    const parent = category('parent', '伙食')
    const child = category('child', '早餐', { level: 2, parent_name: '餐饮', parent_sync_id: 'parent' })
    expect(groupCategories([parent, child])).toEqual({ topLevels: [parent], childrenByParent: { parent: [child] } })
    expect(categoryParent(child, [parent, child])).toBe(parent)
    expect(categoryIsChild(child, parent)).toBe(true)
  })

  it('uses same-kind unique names only for legacy rows without an ID', () => {
    const parent = category('expense', '餐饮')
    const income = category('income', '餐饮', { kind: 'income' })
    const child = category('child', '早餐', { level: 2, parent_name: '餐饮' })
    expect(categoryParent(child, [parent, income, child])).toBe(parent)
    expect(categoryIsChild(child, income)).toBe(false)
  })

  it('does not reattach a missing ID to a different same-name parent', () => {
    const parent = category('replacement', '餐饮')
    const orphan = category('orphan', '早餐', { level: 2, parent_name: '餐饮', parent_sync_id: 'deleted' })
    expect(categoryParent(orphan, [parent, orphan])).toBeUndefined()
    expect(categoryIsChild(orphan, parent)).toBe(false)
    expect(groupCategories([parent, orphan]).topLevels).toEqual([parent, orphan])
  })

  it('keeps ambiguous legacy and unresolved categories visible', () => {
    const parents = [category('a', '餐饮'), category('b', '餐饮')]
    const orphan = category('child', '早餐', { level: 2, parent_name: '餐饮' })
    expect(categoryParent(orphan, [...parents, orphan])).toBeUndefined()
    expect(groupCategories([...parents, orphan]).topLevels).toHaveLength(3)
  })

  it('does not accept self, cross-kind or nested parents', () => {
    const child = category('child', '早餐', { level: 2, parent_sync_id: 'parent' })
    for (const parent of [category('parent', '收入', { kind: 'income' }), category('parent', '二级', { level: 2 })]) {
      expect(categoryParent(child, [parent, child])).toBeUndefined()
    }
    expect(categoryParent({ ...child, parent_sync_id: 'child' }, [child])).toBeUndefined()
  })
})
