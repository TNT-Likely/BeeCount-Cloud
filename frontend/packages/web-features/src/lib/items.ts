/** 归物 · 物品持有(0029)的纯派生逻辑。全部无副作用,便于单测。 */

import type { ReadItem } from '@beecount/api-client'

/** 成本拆分:购买金额 + 关联交易支出 - 二手卖出收入 = 净成本。 */
export type ItemCostParts = {
  /** 购买金额。 */
  purchase: number
  /** 关联交易支出累计(维护/配件/延保……)。 */
  linked: number
  /** 关联交易收入累计(回收款 / 折价补偿 / 保修理赔……),抵减成本。 */
  linkedIncome: number
  /** 处置回收款(仅二手卖出有;报废为 0)。 */
  disposalIncome: number
  /** 净成本 = purchase + linked - linkedIncome - disposalIncome(可为负)。 */
  net: number
  /** 日均成本 = 净成本 / 持有天数。 */
  daily: number
  /** 持有天数(含购买当天;已处置则定格在处置日)。 */
  heldDays: number
}

function num(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export function itemCostParts(item: ReadItem): ItemCostParts {
  const purchase = num(item.purchase_amount)
  const linked = num(item.linked_expense)
  const linkedIncome = num(item.linked_income)
  // 只有二手卖出才认回收款(服务端对 scrapped 已归零,这里再兜一层)。
  const disposalIncome = item.disposal_type === 'sold' ? num(item.disposal_amount) : 0
  const net = purchase + linked - linkedIncome - disposalIncome
  const heldDays = Math.max(num(item.held_days), 1)
  return {
    purchase,
    linked,
    linkedIncome,
    disposalIncome,
    net: Math.round(net * 100) / 100,
    daily: Math.round((net / heldDays) * 100) / 100,
    heldDays,
  }
}

export type ItemGroups = {
  /** 在用(继续摊成本)。 */
  inUse: ReadItem[]
  /** 已处置(二手卖出 / 报废):归档但可查看,天数定格。 */
  disposed: ReadItem[]
}

/**
 * 按状态分组。处置的东西不隐藏 —— 只是归到「已处置」分组(用户可查看历史),
 * 排序:在用按购买日倒序,已处置按处置日倒序(购买日兜底)。
 */
export function groupItemsByStatus(items: readonly ReadItem[]): ItemGroups {
  const inUse: ReadItem[] = []
  const disposed: ReadItem[] = []
  for (const item of items) {
    if (item.is_disposed || item.status === 'disposed') {
      disposed.push(item)
    } else {
      inUse.push(item)
    }
  }
  const byPurchaseDesc = (a: ReadItem, b: ReadItem) =>
    String(b.purchase_date || '').localeCompare(String(a.purchase_date || ''))
  inUse.sort(byPurchaseDesc)
  disposed.sort((a, b) => {
    const key = (x: ReadItem) => String(x.disposal_date || x.purchase_date || '')
    return key(b).localeCompare(key(a))
  })
  return { inUse, disposed }
}

/** 保修状态 → 语义标签 key(none / active / expired)。坏数据按 none 处理。 */
export function warrantyStatusOf(item: ReadItem): 'none' | 'active' | 'expired' {
  const raw = String(item.warranty_status || 'none')
  return raw === 'active' || raw === 'expired' ? raw : 'none'
}

/** 是否已过保(区别于「已处置」:过保的东西照样在用)。 */
export function isWarrantyExpired(item: ReadItem): boolean {
  return warrantyStatusOf(item) === 'expired'
}
