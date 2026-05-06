export type AppSection =
  | 'overview'
  | 'transactions'
  | 'calendar'
  | 'accounts'
  | 'categories'
  | 'tags'
  | 'budgets'
  | 'ledgers'
  | 'settings-profile'
  | 'settings-appearance'
  | 'settings-health'
  | 'settings-devices'
  | 'settings-ai'
  | 'admin-users'
  | 'admin-backup'

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
      { key: 'tags', labelKey: 'nav.tags' }
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
      { key: 'settings-devices', labelKey: 'nav.devices' }
    ]
  }
  // admin-users 不进顶部导航，只在头像 hover 下拉菜单里对 admin 用户展示。
]

export function groupKeyBySection(section: AppSection): string {
  const hit = NAV_GROUPS.find((group) => group.items.some((item) => item.key === section))
  return hit?.key || 'bookkeeping'
}
