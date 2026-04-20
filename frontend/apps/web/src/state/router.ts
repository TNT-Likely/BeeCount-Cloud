import type { AppSection } from '@beecount/web-features'

export type { AppSection } from '@beecount/web-features'

export type AppRoute =
  | { kind: 'login' }
  | {
      kind: 'app'
      ledgerId: string
      section: AppSection
    }

export const APP_SECTIONS: AppSection[] = [
  'transactions',
  'accounts',
  'categories',
  'tags',
  'budgets',
  'ledgers',
  'overview',
  'settings-profile',
  'settings-appearance',
  'settings-ai',
  'settings-health',
  'settings-devices',
  'admin-users'
]

export const DEFAULT_APP_SECTION: AppSection = 'transactions'

function parseWorkspaceSection(parts: string[]): AppSection {
  if (parts.length === 0) return DEFAULT_APP_SECTION
  switch (parts.join('/')) {
    case 'transactions':
      return 'transactions'
    case 'accounts':
      return 'accounts'
    case 'categories':
      return 'categories'
    case 'tags':
      return 'tags'
    case 'budgets':
      return 'budgets'
    default:
      return DEFAULT_APP_SECTION
  }
}

function parseSettingsSection(parts: string[]): AppSection {
  switch (parts.join('/')) {
    case 'profile':
      return 'settings-profile'
    case 'appearance':
      return 'settings-appearance'
    case 'ai':
      return 'settings-ai'
    case 'health':
      return 'settings-health'
    case 'devices':
      return 'settings-devices'
    default:
      return DEFAULT_APP_SECTION
  }
}

function parseRootSection(parts: string[]): AppSection {
  if (parts.length === 0) return DEFAULT_APP_SECTION
  const raw = parts.join('/')
  switch (raw) {
    case 'transactions':
      return 'transactions'
    case 'accounts':
      return 'accounts'
    case 'categories':
      return 'categories'
    case 'tags':
      return 'tags'
    case 'budgets':
      return 'budgets'
    case 'ledgers':
      return 'ledgers'
    case 'overview':
      return 'overview'
    case 'admin/users':
      return 'admin-users'
    case 'settings/profile':
      return 'settings-profile'
    case 'settings/appearance':
      return 'settings-appearance'
    case 'settings/ai':
      return 'settings-ai'
    case 'settings/health':
      return 'settings-health'
    case 'settings/devices':
      return 'settings-devices'
    default:
      return DEFAULT_APP_SECTION
  }
}

function parseLegacyLedgerSection(parts: string[]): AppSection {
  if (parts.length === 0) return 'overview'
  const raw = parts.join('/')
  switch (raw) {
    case 'transactions':
      return 'transactions'
    case 'accounts':
      return 'accounts'
    case 'categories':
      return 'categories'
    case 'tags':
      return 'tags'
    case 'budgets':
      return 'budgets'
    case 'overview':
      return 'overview'
    case 'settings/profile':
      return 'settings-profile'
    case 'settings/appearance':
      return 'settings-appearance'
    case 'settings/ai':
      return 'settings-ai'
    case 'settings/health':
      return 'settings-health'
    case 'settings/devices':
      return 'settings-devices'
    default:
      return DEFAULT_APP_SECTION
  }
}

export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/' || pathname === '/login') {
    return { kind: 'login' }
  }

  const normalized = pathname.replace(/\/+$/, '')
  const parts = normalized.split('/').filter(Boolean)
  if (parts[0] !== 'app') {
    return { kind: 'login' }
  }

  if (!parts[1]) {
    return { kind: 'app', ledgerId: '', section: DEFAULT_APP_SECTION }
  }

  if (parts[1] === 'workspace') {
    return { kind: 'app', ledgerId: '', section: parseWorkspaceSection(parts.slice(2)) }
  }
  if (parts[1] === 'admin') {
    return { kind: 'app', ledgerId: '', section: parts[2] === 'users' ? 'admin-users' : DEFAULT_APP_SECTION }
  }
  if (parts[1] === 'settings') {
    return { kind: 'app', ledgerId: '', section: parseSettingsSection(parts.slice(2)) }
  }
  if (
    parts[1] === 'transactions' ||
    parts[1] === 'accounts' ||
    parts[1] === 'categories' ||
    parts[1] === 'tags' ||
    parts[1] === 'budgets' ||
    parts[1] === 'overview'
  ) {
    return { kind: 'app', ledgerId: '', section: parseRootSection(parts.slice(1)) }
  }

  const ledgerId = decodeURIComponent(parts[1])
  return {
    kind: 'app',
    ledgerId,
    section: parseLegacyLedgerSection(parts.slice(2))
  }
}

export function routePath(route: AppRoute): string {
  if (route.kind === 'login') {
    return '/login'
  }
  switch (route.section) {
    case 'transactions':
      return '/app/transactions'
    case 'accounts':
      return '/app/accounts'
    case 'categories':
      return '/app/categories'
    case 'tags':
      return '/app/tags'
    case 'budgets':
      return '/app/budgets'
    case 'ledgers':
      return '/app/ledgers'
    case 'overview':
      return '/app/overview'
    case 'settings-profile':
      return '/app/settings/profile'
    case 'settings-appearance':
      return '/app/settings/appearance'
    case 'settings-ai':
      return '/app/settings/ai'
    case 'settings-health':
      return '/app/settings/health'
    case 'settings-devices':
      return '/app/settings/devices'
    case 'admin-users':
      return '/app/admin/users'
  }
}
