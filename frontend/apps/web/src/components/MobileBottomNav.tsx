import { useState } from 'react'
import {
  NAV_GROUPS,
  sectionIcon,
  type AppSection,
  type NavItem,
} from '@beecount/web-features'
import { Sheet, SheetContent, SheetHeader, SheetTitle, useT } from '@beecount/ui'
import { MoreHorizontal } from 'lucide-react'

interface Props {
  activeSection: AppSection
  onNavigate: (section: AppSection) => void
  /** 是否显示管理分组(与头像菜单保持一致)。 */
  isAdminUser?: boolean
}

/** 底部固定的 5 个高频主视图;其余入口收进「更多」抽屉。 */
const PRIMARY_SECTIONS: AppSection[] = [
  'overview',
  'transactions',
  'accounts',
  'categories',
  'tags',
]

/** 工具组不进顶部主导航,但手机端需要从「更多」里访问。 */
const TOOL_ITEMS: NavItem[] = [
  { key: 'calendar', labelKey: 'nav.calendar' },
  { key: 'attachments', labelKey: 'attachments.title' },
  { key: 'budgets', labelKey: 'nav.budgets' },
  { key: 'ledgers', labelKey: 'nav.ledgers' },
]

const ADMIN_ITEMS: NavItem[] = [
  { key: 'admin-users', labelKey: 'nav.users' },
  { key: 'admin-backup', labelKey: 'nav.backup' },
  { key: 'admin-data-cleanup', labelKey: 'nav.dataCleanup' },
]

/**
 * 移动端固定底部 tab bar:
 *   首页 / 交易 / 资产 / 分类 / 标签 + 「更多」
 *
 * 「更多」用底部抽屉(bottom Sheet)收纳所有新增业务页(成员/联系人/借款/
 * 定期/储蓄罐/报销/物品)、工具(日历/附件/预算/账本)与设置入口 —— 这些
 * 页面此前只在桌面顶部导航可见,手机端完全没有入口。
 *
 * 图标统一走 sectionIcons 注册表,与桌面顶部导航 / 命令面板一致。
 */
export function MobileBottomNav({ activeSection, onNavigate, isAdminUser }: Props) {
  const t = useT()
  const [moreOpen, setMoreOpen] = useState(false)

  const primaryTabs: Array<{ section: AppSection; label: string }> = [
    { section: 'overview', label: t('nav.overview') },
    { section: 'transactions', label: t('nav.transactions') },
    { section: 'accounts', label: t('nav.accounts') },
    { section: 'categories', label: t('nav.categories') },
    { section: 'tags', label: t('nav.tags') },
  ]

  const businessItems =
    NAV_GROUPS.find((g) => g.key === 'bookkeeping')?.items.filter(
      (item) => !PRIMARY_SECTIONS.includes(item.key),
    ) ?? []
  const settingsItems = NAV_GROUPS.find((g) => g.key === 'settings')?.items ?? []

  const go = (section: AppSection) => {
    setMoreOpen(false)
    onNavigate(section)
  }

  const moreActive = !PRIMARY_SECTIONS.includes(activeSection)

  const renderGroup = (title: string, items: NavItem[]) => {
    if (items.length === 0) return null
    return (
      <div className="space-y-1">
        <div className="px-1 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {items.map((item) => {
            const { Icon, color } = sectionIcon(item.key)
            const active = activeSection === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => go(item.key)}
                className={`flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-center text-[11px] transition-colors ${
                  active
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-border/60 bg-card/60 text-muted-foreground hover:bg-muted/60'
                }`}
              >
                <Icon
                  className="h-5 w-5"
                  style={{ color }}
                  strokeWidth={active ? 2.2 : 1.9}
                />
                <span className="leading-tight">{t(item.labelKey)}</span>
              </button>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <>
      <nav
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border/60 bg-background/95 backdrop-blur-md md:hidden"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        <div className="mx-auto flex max-w-3xl items-stretch">
          {primaryTabs.map(({ section, label }) => {
            const active = activeSection === section
            const { Icon, color } = sectionIcon(section)
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
                <Icon
                  className="h-5 w-5"
                  style={active ? { color } : undefined}
                  strokeWidth={active ? 2.2 : 1.9}
                />
                <span className="font-medium">{label}</span>
              </button>
            )
          })}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            aria-label={t('nav.more')}
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
            <MoreHorizontal className="h-5 w-5" strokeWidth={moreActive ? 2.2 : 1.9} />
            <span className="font-medium">{t('nav.more')}</span>
          </button>
        </div>
      </nav>

      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent
          side="bottom"
          className="max-h-[80vh] overflow-y-auto rounded-t-2xl px-4 pb-8"
        >
          <SheetHeader>
            <SheetTitle>{t('nav.more')}</SheetTitle>
          </SheetHeader>
          <div className="space-y-2 pt-2">
            {renderGroup(t('nav.group.bookkeeping'), businessItems)}
            {renderGroup(t('nav.group.tools'), TOOL_ITEMS)}
            {renderGroup(t('nav.group.settings'), settingsItems)}
            {isAdminUser ? renderGroup(t('nav.group.admin'), ADMIN_ITEMS) : null}
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
