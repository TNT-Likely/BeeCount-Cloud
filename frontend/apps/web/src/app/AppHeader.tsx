import { MoreHorizontal, ScrollText, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  LanguageToggle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  ThemeToggle,
  useT,
} from '@beecount/ui'
import { NAV_GROUPS, type AppSection } from '@beecount/web-features'

import { AvatarDropdown } from '../components/AvatarDropdown'
import { CommandPalette } from '../components/CommandPalette'
import { AnnualReportLauncher } from '../components/dashboard/AnnualReportEntry'
import { useAuth } from '../context/AuthContext'
import { useLedgers } from '../context/LedgersContext'
import { parseRoute, routePath } from '../state/router'

interface Props {
  onOpenLogs: () => void
  onOpenChangelog: () => void
}

/**
 * 全局 sticky header —— logo / 账本选择器 / nav / logs / 主题 / 语言 /
 * AvatarDropdown。
 *
 * 原本挂在 AppPage 里,跟页面内部 state 耦合;阶段 3 上提到 AppShell 下的
 * AppLayout 里,各 Page 切换时 header 不 unmount —— 避免 nav 激活态 /
 * dropdown 打开态在切换时被 flush。
 *
 * 导航通过 react-router `useNavigate`,当前高亮依据 `useLocation().pathname`
 * 反解析到 AppSection。
 */
export function AppHeader({ onOpenLogs, onOpenChangelog }: Props) {
  const t = useT()
  const navigate = useNavigate()
  const location = useLocation()
  const { profileMe, isAdmin, logout } = useAuth()
  const { ledgers, activeLedgerId, setActiveLedgerId } = useLedgers()
  const [annualReportOpen, setAnnualReportOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Cmd+K (Mac) / Ctrl+K (其他) 打开命令面板
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
        return
      }
      if (e.key === 'Escape' && paletteOpen) {
        setPaletteOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [paletteOpen])

  const currentSection: AppSection = useMemo(() => {
    const parsed = parseRoute(location.pathname)
    return parsed.kind === 'app' ? parsed.section : 'transactions'
  }, [location.pathname])

  const visibleNavGroups = useMemo(
    () => NAV_GROUPS.filter((group) => (group.key === 'admin' ? isAdmin : true)),
    [isAdmin]
  )
  const headerCoreItems = useMemo(
    () => visibleNavGroups.find((group) => group.key === 'bookkeeping')?.items || [],
    [visibleNavGroups]
  )
  const headerMoreGroups = useMemo(
    () => visibleNavGroups.filter((g) => g.key !== 'bookkeeping' && g.key !== 'settings'),
    [visibleNavGroups]
  )
  const avatarMenuItems = useMemo(
    () => visibleNavGroups.find((g) => g.key === 'settings')?.items || [],
    [visibleNavGroups]
  )
  const moreMenuActive = useMemo(
    () => headerMoreGroups.some((g) => g.items.some((i) => i.key === currentSection)),
    [headerMoreGroups, currentSection]
  )

  const goToSection = (section: AppSection) => {
    navigate(routePath({ kind: 'app', ledgerId: '', section }))
  }

  return (
    <div className="sticky top-0 z-50 px-2 pb-2 pt-3 md:px-6 md:pt-4">
      <header className="card px-2 md:px-5">
        <div className="flex h-14 items-center justify-between gap-2 md:gap-3">
          <div className="flex min-w-0 items-center gap-1.5 md:gap-2.5">
            <button
              type="button"
              onClick={() => goToSection('overview')}
              className="flex items-center gap-1.5 rounded-md transition-opacity hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring md:gap-2.5"
              aria-label={t('shell.goHome')}
            >
              <img alt={t('shell.appName')} className="h-7 w-7 shrink-0 md:h-8 md:w-8" src="/branding/logo.svg" />
              <div className="flex flex-col leading-none md:flex-row md:items-baseline md:gap-1.5 md:leading-tight">
                <p className="whitespace-nowrap text-[13px] font-bold text-foreground md:text-[15px]">
                  {t('shell.appName')}
                </p>
                <span
                  className="mt-0.5 font-mono text-[9px] text-muted-foreground/70 md:mt-0 md:text-[10px]"
                  title={`BeeCount Cloud v${__APP_VERSION__}`}
                >
                  v{__APP_VERSION__}
                </span>
              </div>
            </button>
            {ledgers.length > 0 ? (
              <Select value={activeLedgerId || undefined} onValueChange={setActiveLedgerId}>
                <SelectTrigger className="ml-1 hidden h-8 w-[180px] border-border/50 bg-background/60 text-xs md:flex">
                  <SelectValue placeholder={t('shell.ledger')} />
                </SelectTrigger>
                <SelectContent>
                  {ledgers.map((ledger) => (
                    <SelectItem key={ledger.ledger_id} value={ledger.ledger_id}>
                      {ledger.ledger_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
          </div>

          <nav className="hidden flex-1 items-center justify-center gap-1 md:flex">
            {headerCoreItems.map((item) => {
              const active = currentSection === item.key
              return (
                <button
                  key={item.key}
                  className="relative"
                  type="button"
                  onClick={() => goToSection(item.key)}
                >
                  <span
                    className={`absolute inset-0 rounded-xl transition-all ${
                      active
                        ? 'bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--secondary)/0.12))] ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                        : 'bg-transparent'
                    }`}
                  />
                  <span
                    className={`relative rounded-xl px-3.5 py-2 text-[13px] font-medium transition-all ${
                      active
                        ? 'text-foreground'
                        : 'text-muted-foreground hover:bg-primary/15 hover:text-primary'
                    }`}
                  >
                    {t(item.labelKey)}
                  </span>
                </button>
              )
            })}
            {headerMoreGroups.length > 0 ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    className={`relative rounded-xl px-3.5 py-2 text-[13px] font-medium transition-all ${
                      moreMenuActive
                        ? 'bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--secondary)/0.12))] text-foreground ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                        : 'text-muted-foreground hover:bg-primary/15 hover:text-primary'
                    }`}
                    aria-label={t('shell.more')}
                    type="button"
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-60 rounded-xl border-border/60 bg-card/95 p-1.5">
                  {headerMoreGroups.map((group, groupIndex) => (
                    <div key={group.key}>
                      {groupIndex > 0 ? <DropdownMenuSeparator /> : null}
                      <DropdownMenuLabel className="px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                        {t(group.titleKey)}
                      </DropdownMenuLabel>
                      {group.items.map((item) => {
                        const active = currentSection === item.key
                        return (
                          <DropdownMenuItem
                            key={item.key}
                            className={`rounded-lg px-2.5 py-2 text-[12px] ${
                              active
                                ? 'bg-primary/10 text-primary'
                                : 'text-muted-foreground hover:bg-primary/15 hover:text-primary'
                            }`}
                            onClick={() => goToSection(item.key)}
                          >
                            {t(item.labelKey)}
                          </DropdownMenuItem>
                        )
                      })}
                    </div>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
          </nav>

          <div className="flex shrink-0 items-center gap-0 rounded-2xl border border-border/40 bg-accent/20 px-0.5 py-0.5 md:gap-2 md:px-2 md:py-1">
            <button
              type="button"
              title={t('cmdk.headerButton')}
              aria-label={t('cmdk.headerButton')}
              onClick={() => setPaletteOpen(true)}
              className="hidden h-9 items-center gap-2 rounded-md px-2.5 text-[12px] text-muted-foreground transition-colors hover:bg-primary/15 hover:text-primary md:flex"
            >
              <Search className="h-3.5 w-3.5" />
              <span>{t('cmdk.headerButton')}</span>
              <kbd className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium">
                {navigator.platform.includes('Mac') ? '⌘K' : 'Ctrl+K'}
              </kbd>
            </button>
            <button
              type="button"
              title={t('cmdk.headerButton')}
              aria-label={t('cmdk.headerButton')}
              onClick={() => setPaletteOpen(true)}
              className="flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-primary/15 hover:text-primary md:h-9 md:w-9 md:hidden"
            >
              <Search className="h-4 w-4" />
            </button>
            {isAdmin ? (
              <button
                type="button"
                title={t('logs.open')}
                aria-label={t('logs.open')}
                onClick={onOpenLogs}
                className="flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-primary/15 hover:text-primary md:h-9 md:w-9"
              >
                <ScrollText className="h-4 w-4" />
              </button>
            ) : null}
            <LanguageToggle />
            <ThemeToggle />
            {profileMe?.email ? (
              <AvatarDropdown
                profileMe={{
                  email: profileMe.email,
                  display_name: profileMe.display_name ?? null,
                  avatar_url: profileMe.avatar_url ?? null,
                  avatar_version: profileMe.avatar_version ?? null,
                }}
                currentSection={currentSection}
                isAdminUser={isAdmin}
                avatarMenuItems={avatarMenuItems}
                onNavigate={goToSection}
                onLogout={logout}
                onOpenChangelog={onOpenChangelog}
                onOpenAnnualReport={() => setAnnualReportOpen(true)}
              />
            ) : null}
          </div>
        </div>

        {ledgers.length > 0 ? (
          <div className="flex items-center gap-2 border-t border-border/50 py-2 md:hidden">
            <Select value={activeLedgerId || undefined} onValueChange={setActiveLedgerId}>
              <SelectTrigger className="h-8 flex-1 border-border/50 bg-background/60 text-xs">
                <SelectValue placeholder={t('shell.ledger')} />
              </SelectTrigger>
              <SelectContent>
                {ledgers.map((ledger) => (
                  <SelectItem key={ledger.ledger_id} value={ledger.ledger_id}>
                    {ledger.ledger_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}
      </header>
      <AnnualReportLauncher open={annualReportOpen} onClose={() => setAnnualReportOpen(false)} />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onOpenAnnualReport={() => setAnnualReportOpen(true)}
      />
    </div>
  )
}
