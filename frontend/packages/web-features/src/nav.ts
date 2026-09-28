export type AppSection =
  | 'overview'
  | 'transactions'
  | 'calendar'
  | 'accounts'
  | 'categories'
  | 'tags'
  | 'projects'
  | 'comparison-report'
  | 'budgets'
  | 'recurring-rules'
  | 'installment-plans'
  | 'debts'
  | 'investments'
  | 'tx-templates'
  | 'ledgers'
  | 'settings-profile'
  | 'settings-appearance'
  | 'settings-health'
  | 'settings-devices'
  | 'settings-developer'
  | 'settings-ai'
  | 'admin-users'
  | 'admin-backup'
  | 'admin-data-cleanup'
  | 'admin-scheduled-jobs'
  | 'admin-app-version'
  | 'admin-licenses'
  | 'admin-broadcasts'
  | 'import'

export type NavItem = {
  key: AppSection
  labelKey: string
}

export type NavGroup = {
  key: string
  titleKey: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    key: 'bookkeeping',
    titleKey: 'nav.group.bookkeeping',
    items: [
      { key: 'overview', labelKey: 'nav.overview' },
      { key: 'transactions', labelKey: 'nav.transactions' },
      // calendar 不放主导航(高频但不是"主视图"等级,跟 transactions 重复语义);
      // 入口走 AppHeader 右上角图标 + ⌘K + AvatarDropdown 任一即可。
      { key: 'accounts', labelKey: 'nav.accounts' },
      { key: 'categories', labelKey: 'nav.categories' },
      { key: 'tags', labelKey: 'nav.tags' },
      // 专案(Phase 13)跟标签平级放在顶部导航,紧邻标签右侧 —— 用户明确
      // 要求从「标签分页底下的子分页」改成独立入口,不嵌在标签页面里。
      { key: 'projects', labelKey: 'nav.projects' },
      // 比較報表(MOZE comparison report):矩陣需要大螢幕,只在 Web 提供,
      // App 端對應的是「報表」分頁的統計報表。
      { key: 'comparison-report', labelKey: 'nav.comparisonReport' }
      // 预算从顶部 bookkeeping 组移出,改放头像下拉的"工具"里 —— 用户
      // 场景下预算访问频率低于 tx/account/category,顶部 nav 保持瘦。
    ]
  },
  {
    key: 'settings',
    titleKey: 'nav.group.settings',
    items: [
      // 个人资料 + 外观合并:两者都是"我的偏好",心智上不该分两处。
      // 保留 `settings-appearance` AppSection 是为了兼容老的分享链接,
      // AppPage 把 appearance 的 route section 也渲染同一个卡片集。
      { key: 'settings-profile', labelKey: 'nav.profile' },
      { key: 'settings-ai', labelKey: 'nav.ai' },
      { key: 'settings-health', labelKey: 'nav.health' },
      { key: 'settings-devices', labelKey: 'nav.devices' },
      // PAT / MCP 管理 — 给 LLM 客户端发长期 token 的地方。
      { key: 'settings-developer', labelKey: 'nav.developer' }
    ]
  }
  // admin-users 不进顶部导航，只在头像 hover 下拉菜单里对 admin 用户展示。
]

export function groupKeyBySection(section: AppSection): string {
  const hit = NAV_GROUPS.find((group) => group.items.some((item) => item.key === section))
  return hit?.key || 'bookkeeping'
}
