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
  'calendar',
  'accounts',
  'categories',
  'tags',
  'projects',
  'comparison-report',
  'budgets',
  'recurring-rules',
  'installment-plans',
  'debts',
  'investments',
  'realized-pnl',
  'tx-templates',
  'ledgers',
  'overview',
  'settings-profile',
  'settings-appearance',
  'settings-ai',
  'settings-health',
  'settings-devices',
  'settings-developer',
  'admin-users',
  'admin-backup',
  'admin-data-cleanup',
  'admin-scheduled-jobs',
  'admin-app-version',
  'admin-security-data-source',
  'admin-licenses',
  'admin-broadcasts'
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
    case 'developer':
      return 'settings-developer'
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
    case 'calendar':
      return 'calendar'
    case 'accounts':
      return 'accounts'
    case 'categories':
      return 'categories'
    case 'tags':
      return 'tags'
    case 'projects':
      return 'projects'
    case 'comparison-report':
      return 'comparison-report'
    case 'budgets':
      return 'budgets'
    case 'recurring-rules':
      return 'recurring-rules'
    case 'installment-plans':
      return 'installment-plans'
    case 'debts':
      return 'debts'
    case 'investments':
      return 'investments'
    case 'realized-pnl':
      return 'realized-pnl'
    case 'tx-templates':
      return 'tx-templates'
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
    case 'settings/developer':
      return 'settings-developer'
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
    case 'calendar':
      return 'calendar'
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
    case 'settings/developer':
      return 'settings-developer'
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
    if (parts[2] === 'users') return { kind: 'app', ledgerId: '', section: 'admin-users' }
    if (parts[2] === 'backup') return { kind: 'app', ledgerId: '', section: 'admin-backup' }
    if (parts[2] === 'data-cleanup') return { kind: 'app', ledgerId: '', section: 'admin-data-cleanup' }
    if (parts[2] === 'scheduled-jobs') return { kind: 'app', ledgerId: '', section: 'admin-scheduled-jobs' }
    if (parts[2] === 'app-version') return { kind: 'app', ledgerId: '', section: 'admin-app-version' }
    if (parts[2] === 'security-data-source') {
      return { kind: 'app', ledgerId: '', section: 'admin-security-data-source' }
    }
    if (parts[2] === 'licenses') return { kind: 'app', ledgerId: '', section: 'admin-licenses' }
    if (parts[2] === 'broadcasts') return { kind: 'app', ledgerId: '', section: 'admin-broadcasts' }
    return { kind: 'app', ledgerId: '', section: DEFAULT_APP_SECTION }
  }
  if (parts[1] === 'settings') {
    return { kind: 'app', ledgerId: '', section: parseSettingsSection(parts.slice(2)) }
  }
  if (
    parts[1] === 'transactions' ||
    parts[1] === 'calendar' ||
    parts[1] === 'accounts' ||
    parts[1] === 'categories' ||
    parts[1] === 'tags' ||
    parts[1] === 'projects' ||
    parts[1] === 'comparison-report' ||
    parts[1] === 'budgets' ||
    parts[1] === 'recurring-rules' ||
    parts[1] === 'installment-plans' ||
    parts[1] === 'debts' ||
    parts[1] === 'investments' ||
    parts[1] === 'realized-pnl' ||
    parts[1] === 'tx-templates' ||
    parts[1] === 'overview'
  ) {
    return { kind: 'app', ledgerId: '', section: parseRootSection(parts.slice(1)) }
  }
  if (parts[1] === 'import') {
    return { kind: 'app', ledgerId: '', section: 'import' }
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
    case 'calendar':
      return '/app/calendar'
    case 'accounts':
      return '/app/accounts'
    case 'categories':
      return '/app/categories'
    case 'tags':
      return '/app/tags'
    case 'projects':
      return '/app/projects'
    case 'comparison-report':
      return '/app/comparison-report'
    case 'budgets':
      return '/app/budgets'
    case 'recurring-rules':
      return '/app/recurring-rules'
    case 'installment-plans':
      return '/app/installment-plans'
    case 'debts':
      return '/app/debts'
    case 'investments':
      return '/app/investments'
    case 'realized-pnl':
      return '/app/realized-pnl'
    case 'tx-templates':
      return '/app/tx-templates'
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
    case 'settings-developer':
      return '/app/settings/developer'
    case 'admin-users':
      return '/app/admin/users'
    case 'admin-backup':
      return '/app/admin/backup'
    case 'admin-data-cleanup':
      return '/app/admin/data-cleanup'
    case 'admin-scheduled-jobs':
      return '/app/admin/scheduled-jobs'
    case 'admin-app-version':
      return '/app/admin/app-version'
    case 'admin-security-data-source':
      return '/app/admin/security-data-source'
    case 'admin-licenses':
      return '/app/admin/licenses'
    case 'admin-broadcasts':
      return '/app/admin/broadcasts'
    case 'import':
      return '/app/import'
  }
}
