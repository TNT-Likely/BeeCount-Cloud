import { Home, LayoutGrid, LogOut, MoreHorizontal, Receipt, Tag, Wallet } from 'lucide-react'

import type { AppSection } from '@beecount/web-features'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  useT
} from '@beecount/ui'

interface Props {
  activeSection: AppSection
  isAdmin: boolean
  onNavigate: (section: AppSection) => void
  onLogout: () => void
}

/**
 * 移动端固定底部 tab bar，参考 PanWatch 的 5 个 tab 布局：
 *   首页 / 交易 / 资产 / 分类 / 更多
 *
 * "更多" 用 DropdownMenu 向上弹：标签、设置三件套（资料/健康/设备）、管理员
 * 入口（仅 admin 可见）、以及退出登录。
 *
 * 仅在 <md 显示；桌面端 layout 自带侧栏 + 顶部 nav，不需要这个。
 */
export function MobileBottomNav({
  activeSection,
  isAdmin,
  onNavigate,
  onLogout
}: Props) {
  const t = useT()

  // 6 tabs：首页 / 交易 / 资产 / 分类 / 标签 / 更多。用户明确要标签不收进
  // "更多"；为了还能塞下 settings + admin 入口，保留独立 "更多" tab。
  const tabs: Array<{ section: AppSection; label: string; Icon: typeof Home }> = [
    { section: 'overview', label: t('nav.overview'), Icon: Home },
    { section: 'transactions', label: t('nav.transactions'), Icon: Receipt },
    { section: 'accounts', label: t('nav.accounts'), Icon: Wallet },
    { section: 'categories', label: t('nav.categories'), Icon: LayoutGrid },
    { section: 'tags', label: t('nav.tags'), Icon: Tag }
  ]

  const moreActive =
    activeSection === 'budgets' ||
    activeSection === 'settings-profile' ||
    activeSection === 'settings-ai' ||
    activeSection === 'settings-health' ||
    activeSection === 'settings-devices' ||
    activeSection === 'admin-users'

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border/60 bg-background/95 backdrop-blur-md md:hidden"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      <div className="mx-auto flex max-w-3xl items-stretch">
        {tabs.map(({ section, label, Icon }) => {
          const active = activeSection === section
          return (
            <button
              key={section}
              type="button"
              onClick={() => onNavigate(section)}
              className={`relative flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] transition-colors ${
                active ? 'text-primary' : 'text-muted-foreground'
              }`}
            >
              {active ? (
                <span
                  className="absolute inset-x-6 top-0 h-[2px] rounded-full bg-primary"
                  aria-hidden
                />
              ) : null}
              <Icon className={`h-5 w-5 ${active ? 'text-primary' : ''}`} />
              <span className="font-medium">{label}</span>
            </button>
          )
        })}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={`relative flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] transition-colors ${
                moreActive ? 'text-primary' : 'text-muted-foreground'
              }`}
            >
              {moreActive ? (
                <span
                  className="absolute inset-x-6 top-0 h-[2px] rounded-full bg-primary"
                  aria-hidden
                />
              ) : null}
              <MoreHorizontal className={`h-5 w-5 ${moreActive ? 'text-primary' : ''}`} />
              <span className="font-medium">{t('shell.more')}</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side="top"
            align="end"
            sideOffset={6}
            className="mb-1 w-56 rounded-xl border-border/60 bg-card/95 p-1.5 backdrop-blur"
          >
            {/* 记账辅助视图(跟底部 5 个主 tab 同组但不够频繁放进 tab bar) */}
            <DropdownMenuLabel className="px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
              {t('nav.group.bookkeeping')}
            </DropdownMenuLabel>
            <DropdownMenuItem
              className={`rounded-lg px-2.5 py-2 text-[12px] ${
                activeSection === 'budgets'
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground'
              }`}
              onClick={() => onNavigate('budgets')}
            >
              {t('nav.budgets')}
            </DropdownMenuItem>

            <DropdownMenuSeparator />
            <DropdownMenuLabel className="px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
              {t('nav.group.settings')}
            </DropdownMenuLabel>
            {([
              { key: 'settings-profile' as AppSection, labelKey: 'nav.profile' },
              { key: 'settings-ai' as AppSection, labelKey: 'nav.ai' },
              { key: 'settings-health' as AppSection, labelKey: 'nav.health' },
              { key: 'settings-devices' as AppSection, labelKey: 'nav.devices' }
            ]).map((item) => (
              <DropdownMenuItem
                key={item.key}
                className={`rounded-lg px-2.5 py-2 text-[12px] ${
                  activeSection === item.key
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground'
                }`}
                onClick={() => onNavigate(item.key)}
              >
                {t(item.labelKey)}
              </DropdownMenuItem>
            ))}

            {isAdmin ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                  管理
                </DropdownMenuLabel>
                <DropdownMenuItem
                  className={`rounded-lg px-2.5 py-2 text-[12px] ${
                    activeSection === 'admin-users'
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground'
                  }`}
                  onClick={() => onNavigate('admin-users')}
                >
                  {t('nav.users')}
                </DropdownMenuItem>
              </>
            ) : null}

            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[12px] text-destructive hover:bg-destructive/10"
              onClick={onLogout}
            >
              <LogOut className="h-3.5 w-3.5" />
              {t('shell.logout')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  )
}
