import { useCallback, useEffect, useState } from 'react'
import { Command } from 'cmdk'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  CornerDownLeft,
  CreditCard,
  Download,
  FileBarChart2,
  FolderTree,
  Hash,
  LayoutDashboard,
  LogOut,
  Moon,
  Plus,
  Receipt,
  Search,
  Settings,
  Sparkles,
  Sun,
  Tag,
  Wallet,
} from 'lucide-react'

import {
  downloadWorkspaceTransactionsCsv,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useLocale, useT, useTheme, useToast } from '@beecount/ui'

import { useAuth } from '../context/AuthContext'
import { useLedgers } from '../context/LedgersContext'
import { localizeError } from '../i18n/errors'
import {
  dispatchOpenDetailAccount,
  dispatchOpenDetailCategory,
  dispatchOpenDetailTag,
  dispatchOpenDetailTx,
  dispatchOpenNewTx,
} from '../lib/txDialogEvents'
import { routePath, type AppSection } from '../state/router'

export type CommandPaletteProps = {
  open: boolean
  onClose: () => void
  onOpenAnnualReport: () => void
}

type SearchResults = {
  transactions: WorkspaceTransaction[]
  categories: WorkspaceCategory[]
  accounts: WorkspaceAccount[]
  tags: WorkspaceTag[]
}

const EMPTY_RESULTS: SearchResults = {
  transactions: [],
  categories: [],
  accounts: [],
  tags: [],
}

/**
 * 全局命令面板 — Cmd+K (Mac) / Ctrl+K (其他) 触发。
 *
 * 信息层级(自上而下):
 *   1. 默认动作:输入 ≥ 1 字时,首条「搜索 'xxx'」始终高亮 — 直接回车跳交易
 *      列表带 q;同时也作为「未找到结果」时的兜底入口。
 *   2. 搜索结果:输入 ≥ 2 字时拉(交易 / 分类 / 账户 / 标签),命中后以分组展示。
 *      点击交易直接打开编辑弹窗(命令式事件,跳转到交易页面)。
 *   3. 快捷操作:始终展示(新建交易、年度报告、切换主题、退出)。
 *   4. 切换账本 / 页面导航:常驻底部。
 *
 * 搜索 debounce 250ms,避免高频 API。打开新建/编辑都通过 txDialogEvents 派发,
 * 由 TransactionsPage 监听后命令式打开弹窗。
 */
export function CommandPalette({ open, onClose, onOpenAnnualReport }: CommandPaletteProps) {
  const t = useT()
  const navigate = useNavigate()
  const { token, logout, profileMe, isAdmin } = useAuth()
  const { ledgers, activeLedgerId, setActiveLedgerId } = useLedgers()
  const { resolved, setMode } = useTheme()
  const { locale } = useLocale()
  const toast = useToast()

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResults>(EMPTY_RESULTS)
  const [searching, setSearching] = useState(false)

  // 关闭时清空 query
  useEffect(() => {
    if (!open) {
      setQuery('')
      setResults(EMPTY_RESULTS)
    }
  }, [open])

  // debounce 搜索
  useEffect(() => {
    if (!open) return
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setResults(EMPTY_RESULTS)
      setSearching(false)
      return
    }
    setSearching(true)
    const handler = setTimeout(() => {
      void runSearch(token, activeLedgerId, trimmed).then((r) => {
        setResults(r)
        setSearching(false)
      })
    }, 250)
    return () => clearTimeout(handler)
  }, [query, open, token, activeLedgerId])

  const goto = useCallback(
    (section: AppSection) => {
      navigate(routePath({ kind: 'app', ledgerId: '', section }))
      onClose()
    },
    [navigate, onClose],
  )

  const switchLedger = useCallback(
    (ledgerId: string) => {
      setActiveLedgerId(ledgerId)
      onClose()
    },
    [setActiveLedgerId, onClose],
  )

  // 「新建交易」— GlobalEditDialogs 在 AppShell 顶层全局监听,任何页面都能直接
  // 派发新建事件,不再需要先 navigate 到 /app/transactions。
  const handleNewTransaction = useCallback(() => {
    onClose()
    dispatchOpenNewTx()
  }, [onClose])

  // 「导出 CSV」当月 / 当年 — 复用 active ledger,无 filter,date 用本地时间起算。
  // dateTo 独占,因此传"次月/次年 1 日 00:00"包含整个 period。
  const handleExportRange = useCallback(
    async (range: 'month' | 'year') => {
      if (!activeLedgerId) {
        toast.error(t('export.csv.noLedger'))
        return
      }
      onClose()
      const now = new Date()
      const dateFromDate =
        range === 'month'
          ? new Date(now.getFullYear(), now.getMonth(), 1)
          : new Date(now.getFullYear(), 0, 1)
      const dateToDate =
        range === 'month'
          ? new Date(now.getFullYear(), now.getMonth() + 1, 1)
          : new Date(now.getFullYear() + 1, 0, 1)
      try {
        await downloadWorkspaceTransactionsCsv(token, {
          ledgerId: activeLedgerId,
          dateFrom: dateFromDate.toISOString(),
          dateTo: dateToDate.toISOString(),
          lang: locale,
        })
        toast.success(t('export.csv.success'))
      } catch (err) {
        toast.error(localizeError(err, t))
      }
    },
    [activeLedgerId, locale, onClose, t, toast, token],
  )

  // 「点击交易结果」— 跳到交易页 + 打开详情弹窗(从详情可二次进编辑)
  const handleSelectTransaction = useCallback(
    (tx: WorkspaceTransaction) => {
      onClose()
      if (window.location.pathname !== '/app/transactions') {
        navigate('/app/transactions')
      }
      setTimeout(() => dispatchOpenDetailTx(tx), 50)
    },
    [navigate, onClose],
  )

  const handleSelectAccount = useCallback(
    (account: WorkspaceAccount) => {
      onClose()
      if (window.location.pathname !== '/app/accounts') {
        navigate('/app/accounts')
      }
      setTimeout(() => dispatchOpenDetailAccount(account), 50)
    },
    [navigate, onClose],
  )

  const handleSelectTag = useCallback(
    (tag: WorkspaceTag) => {
      onClose()
      if (window.location.pathname !== '/app/tags') {
        navigate('/app/tags')
      }
      setTimeout(() => dispatchOpenDetailTag(tag), 50)
    },
    [navigate, onClose],
  )

  const handleSelectCategory = useCallback(
    (cat: WorkspaceCategory) => {
      onClose()
      if (window.location.pathname !== '/app/categories') {
        navigate('/app/categories')
      }
      setTimeout(() => dispatchOpenDetailCategory(cat), 50)
    },
    [navigate, onClose],
  )

  // 「带搜索词去交易列表」— Enter 默认动作
  const handleSearchInList = useCallback(() => {
    const q = query.trim()
    if (!q) return
    onClose()
    navigate(`/app/transactions?q=${encodeURIComponent(q)}`)
  }, [navigate, onClose, query])

  if (!open) return null

  const hasQuery = query.trim().length > 0
  const hasSearchResults =
    results.transactions.length > 0 ||
    results.categories.length > 0 ||
    results.accounts.length > 0 ||
    results.tags.length > 0

  return (
    <div
      className="fixed inset-0 z-[150] flex items-start justify-center bg-black/40 p-4 pt-[15vh] backdrop-blur-sm"
      onClick={onClose}
    >
      <Command
        label="Command Menu"
        shouldFilter={false}
        loop
        className="w-full max-w-xl overflow-hidden rounded-xl border border-border/60 bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border/40 px-3">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder={t('cmdk.placeholder')}
            className="h-12 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            autoFocus
          />
          <kbd className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            ESC
          </kbd>
        </div>

        <Command.List className="max-h-[60vh] overflow-y-auto p-1.5">
          <Command.Empty className="py-8 text-center text-xs text-muted-foreground">
            {searching ? t('cmdk.searching') : t('cmdk.empty')}
          </Command.Empty>

          {/* === 1. 默认动作:始终最顶部,输入时第一个被高亮(回车直跳) === */}
          {hasQuery && (
            <Group heading={t('cmdk.group.default')}>
              <Item
                icon={<Search className="h-4 w-4" />}
                label={t('cmdk.action.searchInList', { q: query.trim() })}
                hint={t('cmdk.hint.enterToSearch')}
                onSelect={handleSearchInList}
              />
            </Group>
          )}

          {/* === 2. 搜索结果(命中时优先展示) === */}
          {results.transactions.length > 0 && (
            <Group heading={t('cmdk.group.transactions')}>
              {results.transactions.slice(0, 5).map((tx) => (
                <Item
                  key={tx.id}
                  icon={<Receipt className="h-4 w-4" />}
                  label={tx.note || tx.category_name || t('cmdk.transaction.untitled')}
                  hint={`${tx.tx_type === 'expense' ? '−' : tx.tx_type === 'income' ? '+' : ''}${formatAmount(tx.amount)} · ${formatDate(tx.happened_at)}`}
                  onSelect={() => handleSelectTransaction(tx)}
                />
              ))}
            </Group>
          )}

          {results.categories.length > 0 && (
            <Group heading={t('cmdk.group.categories')}>
              {results.categories.slice(0, 4).map((cat) => (
                <Item
                  key={cat.id}
                  icon={<FolderTree className="h-4 w-4" />}
                  label={cat.name}
                  hint={cat.kind === 'expense' ? t('enum.txType.expense') : cat.kind === 'income' ? t('enum.txType.income') : '—'}
                  onSelect={() => handleSelectCategory(cat)}
                />
              ))}
            </Group>
          )}

          {results.accounts.length > 0 && (
            <Group heading={t('cmdk.group.accounts')}>
              {results.accounts.slice(0, 4).map((acc) => (
                <Item
                  key={acc.id}
                  icon={<CreditCard className="h-4 w-4" />}
                  label={acc.name}
                  hint={`${formatAmount(acc.balance ?? 0)} ${acc.currency}`}
                  onSelect={() => handleSelectAccount(acc)}
                />
              ))}
            </Group>
          )}

          {results.tags.length > 0 && (
            <Group heading={t('cmdk.group.tags')}>
              {results.tags.slice(0, 4).map((tag) => (
                <Item
                  key={tag.id}
                  icon={<Hash className="h-4 w-4" />}
                  label={tag.name}
                  onSelect={() => handleSelectTag(tag)}
                />
              ))}
            </Group>
          )}

          {/* 输入了但没结果(且不在 loading)— 提示走默认动作或调整 */}
          {hasQuery && !hasSearchResults && !searching && query.trim().length >= 2 && (
            <div className="px-3 py-3 text-[11px] text-muted-foreground">
              {t('cmdk.hint.noResults')}
            </div>
          )}

          {/* === 3. 快捷操作 === */}
          <Group heading={t('cmdk.group.actions')}>
            <Item
              icon={<Plus className="h-4 w-4" />}
              label={t('cmdk.action.newTransaction')}
              shortcut="N"
              onSelect={handleNewTransaction}
            />
            <Item
              icon={<Sparkles className="h-4 w-4" />}
              label={t('nav.annualReport')}
              onSelect={() => {
                onOpenAnnualReport()
                onClose()
              }}
            />
            <Item
              icon={<Download className="h-4 w-4" />}
              label={t('cmdk.action.exportMonth')}
              onSelect={() => void handleExportRange('month')}
            />
            <Item
              icon={<Download className="h-4 w-4" />}
              label={t('cmdk.action.exportYear')}
              onSelect={() => void handleExportRange('year')}
            />
            <Item
              icon={resolved === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              label={
                resolved === 'dark'
                  ? t('cmdk.action.themeLight')
                  : t('cmdk.action.themeDark')
              }
              onSelect={() => {
                setMode(resolved === 'dark' ? 'light' : 'dark')
                onClose()
              }}
            />
            <Item
              icon={<LogOut className="h-4 w-4 text-destructive" />}
              label={t('cmdk.action.logout')}
              onSelect={() => {
                logout()
                onClose()
              }}
            />
          </Group>

          {/* === 4. 切换账本 === */}
          {ledgers.length > 1 && (
            <Group heading={t('cmdk.group.ledgers')}>
              {ledgers.map((ledger) => (
                <Item
                  key={ledger.ledger_id}
                  icon={<BookOpen className="h-4 w-4" />}
                  label={ledger.ledger_name}
                  hint={ledger.currency}
                  active={ledger.ledger_id === activeLedgerId}
                  onSelect={() => switchLedger(ledger.ledger_id)}
                />
              ))}
            </Group>
          )}

          {/* === 5. 页面导航 === */}
          <Group heading={t('cmdk.group.navigation')}>
            <Item icon={<LayoutDashboard className="h-4 w-4" />} label={t('nav.overview')} onSelect={() => goto('overview')} />
            <Item icon={<Receipt className="h-4 w-4" />} label={t('nav.transactions')} onSelect={() => goto('transactions')} />
            <Item icon={<CalendarDays className="h-4 w-4" />} label={t('nav.calendar')} onSelect={() => goto('calendar')} />
            <Item icon={<Wallet className="h-4 w-4" />} label={t('nav.accounts')} onSelect={() => goto('accounts')} />
            <Item icon={<FolderTree className="h-4 w-4" />} label={t('nav.categories')} onSelect={() => goto('categories')} />
            <Item icon={<Tag className="h-4 w-4" />} label={t('nav.tags')} onSelect={() => goto('tags')} />
            <Item icon={<FileBarChart2 className="h-4 w-4" />} label={t('nav.budgets')} onSelect={() => goto('budgets')} />
            <Item icon={<BookOpen className="h-4 w-4" />} label={t('nav.ledgers')} onSelect={() => goto('ledgers')} />
            <Item icon={<Settings className="h-4 w-4" />} label={t('nav.profile')} onSelect={() => goto('settings-profile')} />
            {isAdmin && (
              <Item icon={<Settings className="h-4 w-4" />} label={t('nav.users')} onSelect={() => goto('admin-users')} />
            )}
          </Group>
        </Command.List>

        <div className="flex items-center justify-between border-t border-border/40 bg-muted/30 px-3 py-2 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="rounded bg-background/80 px-1 py-0.5">↑↓</kbd>
              {t('cmdk.tip.navigate')}
            </span>
            <span className="flex items-center gap-1">
              <CornerDownLeft className="h-3 w-3" />
              {t('cmdk.tip.select')}
            </span>
          </span>
          <span className="truncate">{profileMe?.email}</span>
        </div>
      </Command>
    </div>
  )
}

// ====== 内部小组件 ======

function Group({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <Command.Group
      heading={heading}
      className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
    >
      <div className="flex flex-col gap-0.5 pt-1">{children}</div>
    </Command.Group>
  )
}

function Item({
  icon,
  label,
  hint,
  shortcut,
  active,
  onSelect,
}: {
  icon: React.ReactNode
  label: string
  hint?: string
  shortcut?: string
  active?: boolean
  onSelect: () => void
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      value={`${label} ${hint || ''}`}
      className={`flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-foreground aria-selected:bg-accent ${
        active ? 'text-primary' : ''
      }`}
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className="flex-1 truncate">{label}</span>
      {hint && <span className="shrink-0 text-[11px] text-muted-foreground">{hint}</span>}
      {shortcut && (
        <kbd className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {shortcut}
        </kbd>
      )}
      {active && <ArrowRight className="h-3 w-3 text-primary" />}
    </Command.Item>
  )
}

// ====== 工具函数 ======

async function runSearch(
  token: string,
  ledgerId: string | null,
  q: string,
): Promise<SearchResults> {
  // allSettled:任意一个失败不阻塞其它结果
  const results = await Promise.allSettled([
    fetchWorkspaceTransactions(token, { q, limit: 5, ledgerId: ledgerId || undefined }),
    fetchWorkspaceCategories(token, { q, limit: 4, ledgerId: ledgerId || undefined }),
    fetchWorkspaceAccounts(token, { q, limit: 4, ledgerId: ledgerId || undefined }),
    fetchWorkspaceTags(token, { q, limit: 4, ledgerId: ledgerId || undefined }),
  ])
  return {
    transactions: results[0].status === 'fulfilled' ? results[0].value.items : [],
    categories: results[1].status === 'fulfilled' ? results[1].value : [],
    accounts: results[2].status === 'fulfilled' ? results[2].value : [],
    tags: results[3].status === 'fulfilled' ? results[3].value : [],
  }
}

function formatAmount(value: number): string {
  return Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()}`
}
