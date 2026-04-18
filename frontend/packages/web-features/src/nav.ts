export type AppSection =
  | 'overview'
  | 'transactions'
  | 'accounts'
  | 'categories'
  | 'tags'
  | 'settings-profile'
  | 'settings-appearance'
  | 'settings-health'
  | 'settings-devices'
  | 'admin-users'

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
      { key: 'accounts', labelKey: 'nav.accounts' },
      { key: 'categories', labelKey: 'nav.categories' },
      { key: 'tags', labelKey: 'nav.tags' }
    ]
  },
  {
    key: 'settings',
    titleKey: 'nav.group.settings',
    items: [
      { key: 'settings-profile', labelKey: 'nav.profile' },
      { key: 'settings-appearance', labelKey: 'nav.appearance' },
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
