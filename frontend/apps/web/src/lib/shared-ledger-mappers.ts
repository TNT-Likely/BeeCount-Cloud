// SharedLedger* → ReadXxx 类型适配器
//
// `/ledgers/{id}/shared-resources` 返回的 SharedXxxItem 跟 ReadXxx 字段名/类型有
// 细微差异(snake_case 同源但子集),UI 组件普遍接 ReadXxx 形参,这里转一下让
// 共享账本场景的资源能直接喂给现有 picker / tile 渲染逻辑。
//
// 三个映射都把 sync_id 映射成 ReadXxx.id(UI 跨 ledger 唯一标识),其他字段
// 按需补齐。共享账本资源永远跟 Owner 的 last_change_id 同步,这里填 0 占位 —
// 调用方通过 useSharedLedgerResources 的 reload 机制做失效,不靠 change_id。

import type {
  ReadAccount,
  ReadCategory,
  ReadTag,
  SharedAccountItem,
  SharedCategoryItem,
  SharedResourcesBundle,
  SharedTagItem,
} from '@beecount/api-client'

export function sharedCategoryToReadCategory(c: SharedCategoryItem): ReadCategory {
  return {
    id: c.sync_id,
    name: c.name || '',
    kind: ((c.kind || 'expense') as ReadCategory['kind']),
    level: c.level,
    sort_order: c.sort_order,
    icon: c.icon,
    icon_type: c.icon_type,
    icon_cloud_file_id: c.icon_cloud_file_id,
    icon_cloud_sha256: c.icon_cloud_sha256,
    parent_name: c.parent_name,
    last_change_id: 0,
    ledger_id: null,
    ledger_name: null,
    created_by_user_id: null,
    created_by_email: null,
  }
}

export function sharedAccountToReadAccount(a: SharedAccountItem): ReadAccount {
  return {
    id: a.sync_id,
    name: a.name || '',
    account_type: a.account_type,
    currency: a.currency,
    initial_balance: a.initial_balance,
    last_change_id: 0,
    ledger_id: null,
    ledger_name: null,
    created_by_user_id: null,
    created_by_email: null,
    note: a.note,
    credit_limit: a.credit_limit,
    billing_day: a.billing_day,
    payment_due_day: a.payment_due_day,
    bank_name: a.bank_name,
    card_last_four: a.card_last_four,
  }
}

export function sharedTagToReadTag(t: SharedTagItem): ReadTag {
  return {
    id: t.sync_id,
    name: t.name || '',
    color: t.color,
    last_change_id: 0,
    ledger_id: null,
    ledger_name: null,
  }
}

/** 一次把整个 bundle 转成 ReadXxx[] 三件套(在 useMemo 里调一次) */
export function bundleToReadResources(bundle: SharedResourcesBundle | null) {
  if (!bundle) {
    return {
      categories: [] as ReadCategory[],
      accounts: [] as ReadAccount[],
      tags: [] as ReadTag[],
    }
  }
  return {
    categories: bundle.categories.map(sharedCategoryToReadCategory),
    accounts: bundle.accounts.map(sharedAccountToReadAccount),
    tags: bundle.tags.map(sharedTagToReadTag),
  }
}
