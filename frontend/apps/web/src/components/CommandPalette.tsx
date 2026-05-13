import { useCallback, useEffect, useState } from 'react'
import { Command } from 'cmdk'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  Camera,
  CornerDownLeft,
  CreditCard,
  Download,
  Upload,
  FileBarChart2,
  FileText,
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
import { dispatchOpenAsk } from '../lib/askDialogEvents'
import { dispatchOpenParseTxImage, dispatchOpenParseTxText } from '../lib/parseTxEvents'
import { useLocale, useT, useTheme, useToast } from '@beecount/ui'
import { VoiceInputButton } from './cmdk-ai/VoiceInputButton'

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

// cmdk 受控 value 用的稳定 key —— 不要用 label(随 i18n 变就解钩了),也别
// 用 hint(prefix 的 hint 文案会变)。固定字符串才能跨语言钉住默认高亮。
const VAL_SEARCH_IN_LIST = '__action__search-in-list'
const VAL_ASK_AI = '__action__ask-ai'
const VAL_AI_BILLING_TEXT = '__action__ai-billing-text'
const VAL_AI_BILLING_IMAGE = '__action__ai-billing-image'

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
  // cmdk 受控 value —— 用来钉默认高亮(回车命中)的项。仅在「粘贴」场景下
  // 主动设置成 AI 记账;其它情况留空,cmdk 自动选第一项(=「搜索交易」)。
  const [activeValue, setActiveValue] = useState<string>('')
  // 当前 query 是不是从粘贴来的 —— 决定是否把默认动作切到 AI 记账。手动
  // 敲键盘的搜索词不应该被这条规则影响(用户多数时候用搜索)。query 变空
  // 时(用户清空 / 改写后没内容)就重置回 false。
  const [pastedText, setPastedText] = useState(false)

  // 关闭时清空
  useEffect(() => {
    if (!open) {
      setQuery('')
      setResults(EMPTY_RESULTS)
      setActiveValue('')
      setPastedText(false)
    }
  }, [open])

  // query 完全清空 → 重置 paste 标记;否则只要还有内容,继续视为「来自粘贴」
  // (用户可能在粘贴的内容上微调,不应该把高亮切回搜索)。
  useEffect(() => {
    if (!query) setPastedText(false)
  }, [query])

  // debounce 搜索
  const trimmedQuery = query.trim()
  useEffect(() => {
    if (!open) return
    if (trimmedQuery.length < 2) {
      setResults(EMPTY_RESULTS)
      setSearching(false)
      return
    }
    setSearching(true)
    const handler = setTimeout(() => {
      void runSearch(token, activeLedgerId, trimmedQuery).then((r) => {
        setResults(r)
        setSearching(false)
      })
    }, 250)
    return () => clearTimeout(handler)
  }, [trimmedQuery, open, token, activeLedgerId])

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
    const q = trimmedQuery
    if (!q) return
    onClose()
    navigate(`/app/transactions?q=${encodeURIComponent(q)}`)
  }, [navigate, onClose, trimmedQuery])

  // 用户主动选「问 AI」选项 → 关 ⌘K + dispatch 全局事件,GlobalAskDialog 接住打开
  // 跟「新建交易」(GlobalEditDialogs)同模式 — AI 弹窗跟 ⌘K 解耦,不被 cmdk
  // 渲染规则吞内容
  const handleAskAi = useCallback(() => {
    const q = trimmedQuery.replace(/^\?+\s*/, '') // 兼容 `?xxx` 前缀输入
    if (!q) return
    onClose()
    dispatchOpenAsk(q)
  }, [trimmedQuery, onClose])

  // B2 截图记账 — pending image 由 ⌘V handler 写入
  // 用户先看到 input 旁有图片标识 + default actions 多一条「AI 记账(图片)」
  // → 主动点 / 回车选 → dispatch 进 Dialog
  const [pendingImage, setPendingImage] = useState<File | null>(null)
  // 关闭时清空
  useEffect(() => {
    if (!open) setPendingImage(null)
  }, [open])

  // ⌘V 监听:图片拦下来当 pending(图片没法显示在 input);文字不拦,
  // 让浏览器默认行为(进 input)。同时对文字 paste 标记 pastedText=true,
  // 用来把默认高亮切到「AI 记账」(普通敲键盘搜索不应被影响)。
  useEffect(() => {
    if (!open) return
    const handler = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items
      if (!items) return
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          const blob = item.getAsFile()
          if (blob) {
            e.preventDefault()
            setPendingImage(blob)
            return
          }
        }
      }
      // 文字 paste:不拦截,但记下来,默认动作切 AI 记账
      const pastedString = e.clipboardData?.getData('text')
      if (pastedString && pastedString.trim().length > 0) {
        setPastedText(true)
      }
    }
    document.addEventListener('paste', handler)
    return () => document.removeEventListener('paste', handler)
  }, [open])

  // 「AI 记账」单一入口 — 根据 pendingImage / query 决定 image / text 路径
  const handleAiBilling = useCallback(() => {
    if (pendingImage) {
      onClose()
      dispatchOpenParseTxImage(pendingImage)
      return
    }
    if (trimmedQuery) {
      onClose()
      dispatchOpenParseTxText(trimmedQuery)
    }
  }, [pendingImage, trimmedQuery, onClose])

  // 默认高亮项的 cmdk value:粘贴场景钉到 AI 记账,其它情况留空让 cmdk
  // 自动选第一项(=「搜索交易」)。手动敲键盘搜索是高频路径,不应被打断。
  useEffect(() => {
    if (!open) return
    if (pendingImage) {
      setActiveValue(VAL_AI_BILLING_IMAGE)
    } else if (pastedText && trimmedQuery) {
      setActiveValue(VAL_AI_BILLING_TEXT)
    } else {
      setActiveValue('')
    }
  }, [open, pendingImage, pastedText, trimmedQuery])

  if (!open) return null

  const hasQuery = trimmedQuery.length > 0
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
        value={activeValue}
        onValueChange={setActiveValue}
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
          <VoiceInputButton
            lang={
              locale === 'zh-CN'
                ? 'zh-CN'
                : locale === 'zh-TW'
                  ? 'zh-TW'
                  : 'en-US'
            }
            onInterim={(text) => setQuery(text)}
            onFinal={(text) => setQuery(text)}
          />
          {pendingImage && (
            <button
              type="button"
              onClick={() => setPendingImage(null)}
              className="flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary hover:bg-primary/20"
              title={t('cmdk.pendingImage.remove')}
            >
              <Camera className="h-3 w-3" />
              <span className="max-w-[80px] truncate">
                {pendingImage.name || 'screenshot'}
              </span>
              <span className="ml-0.5 text-primary/60">×</span>
            </button>
          )}
          <kbd className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            ESC
          </kbd>
        </div>

        <Command.List className="max-h-[60vh] overflow-y-auto p-1.5">
          <Command.Empty className="py-8 text-center text-xs text-muted-foreground">
            {searching ? t('cmdk.searching') : t('cmdk.empty')}
          </Command.Empty>

          {/* === 1. 默认动作 === Enter 命中 = 第一项。普通输入时第一项是
              「搜索交易」(用户高频路径);粘贴图片 / 文字时上面的 effect
              把 cmdk activeValue 钉到 AI 记账,Enter 改命中 AI 记账。 */}
          {(hasQuery || pendingImage) && (
            <Group heading={t('cmdk.group.default')}>
              {hasQuery && !pendingImage && (
                <Item
                  value={VAL_SEARCH_IN_LIST}
                  icon={<Search className="h-4 w-4" />}
                  label={t('cmdk.action.searchInList', { q: trimmedQuery })}
                  hint={t('cmdk.hint.enterToSearch')}
                  onSelect={handleSearchInList}
                />
              )}
              {hasQuery && !pendingImage && (
                <Item
                  value={VAL_ASK_AI}
                  icon={<Sparkles className="h-4 w-4 text-primary" />}
                  label={t('cmdk.action.askAi', { q: trimmedQuery })}
                  hint="?"
                  onSelect={handleAskAi}
                />
              )}
              {pendingImage ? (
                <Item
                  value={VAL_AI_BILLING_IMAGE}
                  icon={<Camera className="h-4 w-4 text-primary" />}
                  label={t('cmdk.action.aiBillingImage', {
                    name: pendingImage.name || 'screenshot',
                  })}
                  hint={t('cmdk.hint.enterToBill')}
                  onSelect={handleAiBilling}
                />
              ) : (
                hasQuery && (
                  <Item
                    value={VAL_AI_BILLING_TEXT}
                    icon={<FileText className="h-4 w-4 text-primary" />}
                    label={t('cmdk.action.aiBillingText', { q: trimmedQuery })}
                    hint={t('cmdk.hint.enterToBill')}
                    onSelect={handleAiBilling}
                  />
                )
              )}
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
          {hasQuery && !hasSearchResults && !searching && trimmedQuery.length >= 2 && (
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
              icon={<Upload className="h-4 w-4" />}
              label={t('cmdk.action.importLedger')}
              onSelect={() => {
                onClose()
                navigate('/app/import')
              }}
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
  value,
  onSelect,
}: {
  icon: React.ReactNode
  label: string
  hint?: string
  shortcut?: string
  active?: boolean
  /** cmdk value:外层 `<Command value=...>` 受控时,用这个钉默认高亮项;
   *  没传则 fallback 到 `${label} ${hint}`。 */
  value?: string
  onSelect: () => void
}) {
  // 选中态:`bg-accent` 在暗黑 + 黄主题色下是淡黄色,前景 `text-foreground`(淡灰)
  // 在淡黄底上几乎看不清。强制选中时用 `text-accent-foreground`(shadcn 配对色,
  // 自动适配深底色),子元素 hint / kbd / icon 也跟着变(group-aria-selected)。
  return (
    <Command.Item
      onSelect={onSelect}
      value={value ?? `${label} ${hint || ''}`}
      className={`group flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-foreground aria-selected:bg-accent aria-selected:text-accent-foreground ${
        active ? 'text-primary' : ''
      }`}
    >
      <span className="text-muted-foreground group-aria-selected:text-accent-foreground/80">
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
      {hint && (
        <span className="shrink-0 text-[11px] text-muted-foreground group-aria-selected:text-accent-foreground/80">
          {hint}
        </span>
      )}
      {shortcut && (
        <kbd className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground group-aria-selected:bg-accent-foreground/15 group-aria-selected:text-accent-foreground">
          {shortcut}
        </kbd>
      )}
      {active && (
        <ArrowRight className="h-3 w-3 text-primary group-aria-selected:text-accent-foreground" />
      )}
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
