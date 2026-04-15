export type AppSection =
  | 'overview'
  | 'transactions'
  | 'accounts'
  | 'categories'
  | 'tags'
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
      { key: 'transactions', labelKey: 'nav.transactions' },
      { key: 'accounts', labelKey: 'nav.accounts' },
      { key: 'categories', labelKey: 'nav.categories' },
      { key: 'tags', labelKey: 'nav.tags' },
      { key: 'overview', labelKey: 'nav.overview' }
    ]
  },
  {
    key: 'settings',
    titleKey: 'nav.group.settings',
    items: [
      { key: 'settings-health', labelKey: 'nav.health' },
      { key: 'settings-devices', labelKey: 'nav.devices' }
    ]
  },
  {
    key: 'admin',
    titleKey: 'nav.group.admin',
    items: [{ key: 'admin-users', labelKey: 'nav.users' }]
  }
]

export function groupKeyBySection(section: AppSection): string {
  const hit = NAV_GROUPS.find((group) => group.items.some((item) => item.key === section))
  return hit?.key || 'bookkeeping'
}
