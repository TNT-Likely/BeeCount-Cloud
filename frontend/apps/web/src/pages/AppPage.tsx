import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getStoredDeviceId, getStoredUserId } from '@beecount/api-client'

import { useSyncSocket } from '../hooks/useSyncSocket'
import { drainPull, startPoller } from '../state/sync-client'
import { OverviewHero } from '../components/dashboard/OverviewHero'
import { OverviewKeyMetrics } from '../components/dashboard/OverviewKeyMetrics'
import { AssetCompositionDonut } from '../components/dashboard/AssetCompositionDonut'
import { MonthlyTrendBars } from '../components/dashboard/MonthlyTrendBars'
import { TopCategoriesList } from '../components/dashboard/TopCategoriesList'

import { LogOut, MoreHorizontal, SlidersHorizontal } from 'lucide-react'

import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  useToast,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  LanguageToggle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  ThemeToggle,
  Tooltip,
  useT
} from '@beecount/ui'

import {
  ApiError,
  batchAttachmentExists,
  deleteAdminUser,
  downloadAttachment,
  uploadAttachment,
  type AttachmentRef,
  type ReadAccount,
  type ReadCategory,
  type ReadLedger,
  type ReadTag,
  type ReadTransaction,
  type WorkspaceTag,
  type ProfileMe,
  type AdminDevice,
  type AdminHealth,
  type AdminOverview,
  type UserAdmin,
  createAccount,
  createAdminUser,
  createCategory,
  createLedger,
  deleteLedger,
  createTag,
  createTransaction,
  deleteAccount,
  deleteCategory,
  deleteTag,
  deleteTransaction,
  fetchAdminDevices,
  fetchAdminHealth,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchReadLedgerDetail,
  fetchReadLedgers,
  fetchWorkspaceAnalytics,
  type WorkspaceAnalytics,
  fetchProfileMe,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  patchProfileMe,
  patchAdminUser,
  updateAccount,
  updateCategory,
  updateLedgerMeta,
  updateTag,
  updateTransaction
} from '@beecount/api-client'

import {
  AccountsPanel,
  AdminUsersPanel,
  CategoriesPanel,
  ConfirmDialog,
  NAV_GROUPS,
  OpsDevicesPanel,
  TagsPanel,
  TransactionsPanel,
  accountDefaults,
  canManageLedger,
  canWriteTransactions,
  categoryDefaults,
  formatIsoDateTime,
  tagDefaults,
  txDefaults,
  type AccountForm,
  type CategoryForm,
  type TagForm,
  type TxForm
} from '@beecount/web-features'

import { localizeError } from '../i18n/errors'
import { AppLayout } from '../layout/AppLayout'
import type { AppRoute, AppSection } from '../state/router'

type Notice = {
  type: 'default' | 'destructive'
  title: string
  message: string
} | null

type PendingDelete =
  | { kind: 'tx'; id: string; ledgerId: string }
  | { kind: 'account'; id: string }
  | { kind: 'category'; id: string }
  | { kind: 'tag'; id: string }
  | { kind: 'ledger'; id: string; ledgerId: string }
  | null

type AttachmentPreviewState = {
  open: boolean
  fileName: string
  objectUrl: string
}

type AppPageProps = {
  token: string
  route: Extract<AppRoute, { kind: 'app' }>
  onNavigate: (next: AppRoute, options?: { replace?: boolean }) => void
  onLogout: () => void
}

type TxFilter = {
  q: string
  txType: '' | 'expense' | 'income' | 'transfer'
  accountName: string
}

const TX_PAGE_SIZE_DEFAULT = 20
const TX_FILTER_STORAGE_PREFIX = 'beecount:web:txFilter:v1'

function defaultTxFilter(): TxFilter {
  return { q: '', txType: '', accountName: '' }
}

function txFilterStorageKey(userId: string, ledgerFilter: string): string {
  const normalizedUserId = (userId || 'anonymous').trim() || 'anonymous'
  const normalizedLedgerFilter = (ledgerFilter || '__all__').trim() || '__all__'
  return `${TX_FILTER_STORAGE_PREFIX}:${normalizedUserId}:${normalizedLedgerFilter}`
}

function parseStoredTxFilter(raw: string | null): TxFilter | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<TxFilter>
    const txType = parsed.txType
    const normalizedTxType: TxFilter['txType'] =
      txType === 'expense' || txType === 'income' || txType === 'transfer' ? txType : ''
    return {
      q: typeof parsed.q === 'string' ? parsed.q : '',
      txType: normalizedTxType,
      accountName: typeof parsed.accountName === 'string' ? parsed.accountName : ''
    }
  } catch {
    return null
  }
}

function sectionNeedsLedger(section: AppSection): boolean {
  return ['overview'].includes(section)
}

function isListSection(section: AppSection): boolean {
  return ['transactions', 'accounts', 'categories', 'tags'].includes(section)
}

function wsUrl(token: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.port === '5173' ? `${window.location.hostname}:8080` : window.location.host
  return `${protocol}://${host}/ws?token=${encodeURIComponent(token)}`
}

function jwtUserId(token: string): string {
  try {
    const [, payload] = token.split('.')
    if (!payload) return ''
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = `${base64}${'='.repeat((4 - (base64.length % 4)) % 4)}`
    const raw = atob(padded)
    const parsed = JSON.parse(raw) as { sub?: string }
    return typeof parsed.sub === 'string' ? parsed.sub : ''
  } catch {
    return ''
  }
}

function normalizeAttachmentRef(raw: unknown, fallbackOrder: number): AttachmentRef | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const fileName = typeof row.fileName === 'string' ? row.fileName.trim() : ''
  if (!fileName) return null
  return {
    fileName,
    originalName: typeof row.originalName === 'string' ? row.originalName : null,
    fileSize: typeof row.fileSize === 'number' ? row.fileSize : null,
    width: typeof row.width === 'number' ? row.width : null,
    height: typeof row.height === 'number' ? row.height : null,
    sortOrder: typeof row.sortOrder === 'number' ? row.sortOrder : fallbackOrder,
    cloudFileId: typeof row.cloudFileId === 'string' ? row.cloudFileId : null,
    cloudSha256: typeof row.cloudSha256 === 'string' ? row.cloudSha256 : null
  }
}

function normalizeAttachmentRefs(raw: unknown): AttachmentRef[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item, index) => normalizeAttachmentRef(item, index))
    .filter((item): item is AttachmentRef => Boolean(item))
    .sort((a, b) => (a.sortOrder ?? Number.MAX_SAFE_INTEGER) - (b.sortOrder ?? Number.MAX_SAFE_INTEGER))
    .map((item, index) => ({ ...item, sortOrder: index }))
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data)
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'heic'])

function isPreviewableImage(mimeType: string | null, fileName: string | null | undefined): boolean {
  if (mimeType && mimeType.toLowerCase().startsWith('image/')) {
    return true
  }
  const normalizedName = (fileName || '').trim().toLowerCase()
  const extension = normalizedName.includes('.') ? normalizedName.split('.').pop() || '' : ''
  return IMAGE_EXTENSIONS.has(extension)
}

export function AppPage({ token, route, onNavigate, onLogout }: AppPageProps) {
  const t = useT()
  const previewRequestSeqRef = useRef(0)
  const txFilterRestoreInProgressRef = useRef(false)
  const txAttachmentPreviewUrlByFileIdRef = useRef<Record<string, string>>({})

  // 原来用 Notice 顶栏显示成功/失败，改成 toast 后不再需要这个 state。
  const [baseChangeId, setBaseChangeId] = useState(0)

  const [ledgers, setLedgers] = useState<ReadLedger[]>([])
  const [transactions, setTransactions] = useState<ReadTransaction[]>([])
  const [txTotal, setTxTotal] = useState(0)
  const [txPage, setTxPage] = useState(1)
  const [txPageSize, setTxPageSize] = useState(TX_PAGE_SIZE_DEFAULT)
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [categories, setCategories] = useState<ReadCategory[]>([])
  const [tags, setTags] = useState<WorkspaceTag[]>([])
  const [analyticsData, setAnalyticsData] = useState<WorkspaceAnalytics | null>(null)
  const [analyticsIncomeRanks, setAnalyticsIncomeRanks] = useState<WorkspaceAnalytics['category_ranks']>([])
  const [profileMe, setProfileMe] = useState<ProfileMe | null>(null)
  const [profileDisplayName, setProfileDisplayName] = useState('')
  const [adminUsers, setAdminUsers] = useState<UserAdmin[]>([])
  const [adminDevices, setAdminDevices] = useState<AdminDevice[]>([])
  const [adminOverview, setAdminOverview] = useState<AdminOverview | null>(null)
  const [adminHealth, setAdminHealth] = useState<AdminHealth | null>(null)
  const [isAdminUser, setIsAdminUser] = useState(false)
  const [isAdminResolved, setIsAdminResolved] = useState(false)
  const [txDictionaryLoading, setTxDictionaryLoading] = useState(false)
  const [txDictionaryAccounts, setTxDictionaryAccounts] = useState<ReadAccount[]>([])
  const [txDictionaryCategories, setTxDictionaryCategories] = useState<ReadCategory[]>([])
  const [txDictionaryTags, setTxDictionaryTags] = useState<ReadTag[]>([])

  const [listUserFilter, setListUserFilter] = useState('__all__')
  const [listQuery, setListQuery] = useState('')
  const [adminUserStatusFilter, setAdminUserStatusFilter] = useState<'enabled' | 'disabled' | 'all'>('enabled')
  const [devicesWindowDays, setDevicesWindowDays] = useState<'30' | 'all'>('30')
  const [activeLedgerId, setActiveLedgerId] = useState('')

  const [txWriteLedgerId, setTxWriteLedgerId] = useState('')

  const [txFilterApplied, setTxFilterApplied] = useState<TxFilter>(defaultTxFilter)
  const [txFilterDraft, setTxFilterDraft] = useState<TxFilter>(defaultTxFilter)
  const [txFilterOpen, setTxFilterOpen] = useState(false)

  const [txForm, setTxForm] = useState<TxForm>(txDefaults)
  const [accountForm, setAccountForm] = useState<AccountForm>(accountDefaults)
  const [categoryForm, setCategoryForm] = useState<CategoryForm>(categoryDefaults)
  const [tagForm, setTagForm] = useState<TagForm>(tagDefaults)
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null)

  const [adminCreateEmail, setAdminCreateEmail] = useState('')
  const [adminCreatePassword, setAdminCreatePassword] = useState('')
  const [adminCreateIsAdmin, setAdminCreateIsAdmin] = useState(false)
  const [adminCreateIsEnabled, setAdminCreateIsEnabled] = useState(true)
  const [categoryIconPreviewByFileId, setCategoryIconPreviewByFileId] = useState<Record<string, string>>({})
  const [attachmentPreview, setAttachmentPreview] = useState<AttachmentPreviewState>({
    open: false,
    fileName: '',
    objectUrl: ''
  })

  const [createLedgerName, setCreateLedgerName] = useState('')
  const [createCurrency, setCreateCurrency] = useState('CNY')
  const [createLedgerDialogOpen, setCreateLedgerDialogOpen] = useState(false)
  const [editLedgerName, setEditLedgerName] = useState('')
  const [editCurrency, setEditCurrency] = useState('CNY')

  const selectedLedger = useMemo(
    () => ledgers.find((ledger) => ledger.ledger_id === activeLedgerId) || null,
    [activeLedgerId, ledgers]
  )
  const sessionUserId = useMemo(() => jwtUserId(token), [token])
  const txFilterPersistKey = useMemo(
    () => txFilterStorageKey(sessionUserId || 'anonymous', activeLedgerId || '__all__'),
    [sessionUserId, activeLedgerId]
  )
  const profileDisplayLabel = useMemo(
    () => profileMe?.display_name?.trim() || profileMe?.email || sessionUserId || '-',
    [profileMe, sessionUserId]
  )
  const profileInitial = useMemo(
    () => profileDisplayLabel.trim().charAt(0).toUpperCase() || '?',
    [profileDisplayLabel]
  )

  const txWritableLedgers = useMemo(
    () => ledgers.filter((ledger) => canWriteTransactions(ledger.role)),
    [ledgers]
  )
  const ownerLedgers = useMemo(
    () => ledgers.filter((ledger) => canManageLedger(ledger.role)),
    [ledgers]
  )
  const canWriteTx = txWritableLedgers.length > 0
  const canManageAnyLedgerMeta = ownerLedgers.length > 0
  const canManageSelectedLedger = canManageLedger(selectedLedger?.role)
  const ledgerOptions = useMemo(
    () => ledgers.map((ledger) => ({ ledger_id: ledger.ledger_id, ledger_name: ledger.ledger_name })),
    [ledgers]
  )
  const txWriteLedgerOptions = useMemo(
    () => txWritableLedgers.map((ledger) => ({ ledger_id: ledger.ledger_id, ledger_name: ledger.ledger_name })),
    [txWritableLedgers]
  )
  // 交易可选账户：与"当前写入账本"币种一致 + 排除估值账户（不动产 / 车辆 /
  // 投资 / 保险 / 公积金 / 贷款 —— 这些是净值组件，不参与日常交易）。
  // 对应 mobile 端 account_picker 里的同一套过滤条件。
  const VALUATION_ACCOUNT_TYPES = useMemo(
    () =>
      new Set<string>(['real_estate', 'vehicle', 'investment', 'insurance', 'social_fund', 'loan']),
    []
  )
  const txWriteLedgerCurrency = useMemo(() => {
    const hit = ledgers.find((ledger) => ledger.ledger_id === (txWriteLedgerId || activeLedgerId))
    return (hit?.currency || 'CNY').trim().toUpperCase()
  }, [ledgers, txWriteLedgerId, activeLedgerId])
  const txWriteAccounts = useMemo(() => {
    return txDictionaryAccounts.filter((row) => {
      const currency = (row.currency || 'CNY').trim().toUpperCase()
      if (currency !== txWriteLedgerCurrency) return false
      if (VALUATION_ACCOUNT_TYPES.has(row.account_type || '')) return false
      return true
    })
  }, [txDictionaryAccounts, txWriteLedgerCurrency, VALUATION_ACCOUNT_TYPES])
  const txWriteCategories = txDictionaryCategories
  const txWriteTags = txDictionaryTags
  const txFilterAccountOptions = useMemo(
    () =>
      [...new Set(accounts.map((row) => (row.name || '').trim()).filter((value) => value.length > 0))].sort((a, b) =>
        a.localeCompare(b)
      ),
    [accounts]
  )
  const visibleNavGroups = useMemo(
    () => NAV_GROUPS.filter((group) => (group.key === 'admin' ? isAdminUser : true)),
    [isAdminUser]
  )
  const headerCoreItems = useMemo(
    () => visibleNavGroups.find((group) => group.key === 'bookkeeping')?.items || [],
    [visibleNavGroups]
  )
  const headerMoreGroups = useMemo(
    () => visibleNavGroups.filter((group) => group.key !== 'bookkeeping'),
    [visibleNavGroups]
  )
  const moreMenuActive = useMemo(
    () =>
      headerMoreGroups.some((group) =>
        group.items.some((item) => item.key === route.section)
      ),
    [headerMoreGroups, route.section]
  )

  // 统一 UI 提示通过右上角 toast 呈现，替换原来的顶部 Alert 横幅。
  // 保留函数名以避免修改几十处调用点。
  const toast = useToast()
  const setErrorNotice = (message: string) => {
    toast.error(message, t('notice.failed'))
  }
  const setSuccessNotice = (message: string) => {
    toast.success(message, t('notice.success'))
  }

  const isSessionError = (err: unknown): boolean => {
    if (!(err instanceof ApiError)) return false
    if (err.status === 401 || err.status === 403) return true
    return err.code === 'AUTH_INVALID_TOKEN' || err.code === 'AUTH_INSUFFICIENT_SCOPE'
  }

  const handleTopLevelLoadError = (err: unknown) => {
    setErrorNotice(renderError(err))
    if (isSessionError(err)) {
      onLogout()
    }
  }

  const syncRouteWithLedgers = (rows: ReadLedger[]) => {
    if (rows.length === 0) {
      if (sectionNeedsLedger(route.section)) {
        onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' }, { replace: true })
      }
      setActiveLedgerId('')
      setTxWriteLedgerId('')
      return ''
    }

    if (activeLedgerId && rows.some((row) => row.ledger_id === activeLedgerId)) {
      return activeLedgerId
    }

    const firstId = rows[0].ledger_id
    const firstTxWritableId = rows.find((row) => canWriteTransactions(row.role))?.ledger_id || ''
    setActiveLedgerId(firstId)
    setTxWriteLedgerId((prev) => prev || firstTxWritableId)
    return firstId
  }

  const loadLedgers = async (): Promise<string> => {
    const rows = await fetchReadLedgers(token)
    setLedgers(rows)
    return syncRouteWithLedgers(rows)
  }

  const loadProfile = async () => {
    const row = await fetchProfileMe(token)
    setProfileMe(row)
    setProfileDisplayName(row.display_name || '')
  }

  const loadLedgerBase = async (ledgerId: string) => {
    if (!ledgerId) {
      setBaseChangeId(0)
      return 0
    }
    const detail = await fetchReadLedgerDetail(token, ledgerId)
    setBaseChangeId(detail.source_change_id)
    return detail.source_change_id
  }

  const refreshSectionData = async (ledgerId: string, section: AppSection) => {
    if (sectionNeedsLedger(section) && !ledgerId) {
      return
    }

    // Overview 页：需要 accounts（资产构成饼图） + 最近交易（最近列表）。
    // 不等待图表 analytics 拉数（有单独 effect），这里只收集表数据。
    if (section === 'overview') {
      const [txPageResult, accountRows] = await Promise.all([
        fetchWorkspaceTransactions(token, { limit: 10 }),
        fetchWorkspaceAccounts(token, { limit: 500 })
      ])
      setTransactions(txPageResult.items)
      setTxTotal(txPageResult.total)
      setAccounts(accountRows)
      return
    }

    if (section === 'transactions') {
      const [txPageResult, accountRows, categoryRows, tagRows] = await Promise.all([
        fetchWorkspaceTransactions(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          q: listQuery || undefined,
          txType: txFilterApplied.txType || undefined,
          accountName: txFilterApplied.accountName || undefined,
          limit: txPageSize,
          offset: (txPage - 1) * txPageSize
        }),
        fetchWorkspaceAccounts(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          limit: 500
        }),
        fetchWorkspaceCategories(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          limit: 500
        }),
        fetchWorkspaceTags(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          limit: 500
        })
      ])
      setTransactions(txPageResult.items)
      setTxTotal(txPageResult.total)
      if (txPageResult.total > 0 && txPage > 1 && txPageResult.items.length === 0) {
        const lastPage = Math.max(1, Math.ceil(txPageResult.total / txPageSize))
        if (lastPage !== txPage) {
          setTxPage(lastPage)
          return
        }
      }
      setAccounts(accountRows)
      setCategories(categoryRows)
      setTags(tagRows)
      return
    }

    if (section === 'admin-users') {
      if (!isAdminUser) {
        setAdminUsers([])
        return
      }
      const list = await fetchAdminUsers(token, {
        q: listQuery || undefined,
        status: adminUserStatusFilter,
        limit: 500
      })
      setAdminUsers(list.items)
      return
    }

    if (section === 'accounts') {
      setAccounts(
        await fetchWorkspaceAccounts(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          q: listQuery || undefined,
          limit: 500
        })
      )
      return
    }

    if (section === 'categories') {
      setCategories(
        await fetchWorkspaceCategories(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          q: listQuery || undefined,
          limit: 500
        })
      )
      return
    }

    if (section === 'tags') {
      setTags(
        await fetchWorkspaceTags(token, {
          ledgerId: ledgerId || undefined,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          q: listQuery || undefined,
          limit: 500
        })
      )
      return
    }

    if (section === 'settings-devices') {
      const devices = await fetchAdminDevices(token, {
        user_id: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
        q: listQuery || undefined,
        active_within_days: devicesWindowDays === 'all' ? 0 : 30,
        limit: 200
      })
      setAdminDevices(devices.items)
      return
    }

    if (section === 'settings-health') {
      const [health, overview] = await Promise.all([
        fetchAdminHealth(token),
        isAdminUser ? fetchAdminOverview(token) : Promise.resolve<AdminOverview | null>(null)
      ])
      setAdminHealth(health)
      setAdminOverview(overview)
      return
    }

  }

  const refreshCurrent = async (preferredSection?: AppSection) => {
    const firstLedgerId = await loadLedgers()
    const section = preferredSection || route.section
    const effectiveLedgerId = activeLedgerId || firstLedgerId
    if (sectionNeedsLedger(section) && effectiveLedgerId) {
      await loadLedgerBase(effectiveLedgerId)
    } else if (!sectionNeedsLedger(section)) {
      setBaseChangeId(0)
    }
    await refreshSectionData(effectiveLedgerId, section)
  }

  // WS / polling 事件触发时用这个：不止刷当前 section，也把 tags/categories/accounts
  // 都重新拉一遍。否则用户停留在"交易"页看不到新建的标签，切过去时仍是旧缓存。
  // 交易数据在 section='transactions' 分支里已经并行 fetch 了四类；这里补齐非交易
  // 活动页时其他三类的兜底。
  const refreshAllSections = async () => {
    const firstLedgerId = await loadLedgers()
    const section = route.section
    const effectiveLedgerId = activeLedgerId || firstLedgerId
    if (sectionNeedsLedger(section) && effectiveLedgerId) {
      await loadLedgerBase(effectiveLedgerId)
    }
    await Promise.all([
      refreshSectionData(effectiveLedgerId, section),
      section === 'tags' ? Promise.resolve() : refreshSectionData(effectiveLedgerId, 'tags'),
      section === 'categories' ? Promise.resolve() : refreshSectionData(effectiveLedgerId, 'categories'),
      section === 'accounts' ? Promise.resolve() : refreshSectionData(effectiveLedgerId, 'accounts'),
    ])
  }

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        await refreshCurrent()
      } catch (err) {
        if (!cancelled) {
          handleTopLevelLoadError(err)
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.section, route.ledgerId])

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const profile = await fetchProfileMe(token)
        if (cancelled) return
        setProfileMe(profile)
        setProfileDisplayName(profile.display_name || '')
      } catch (err) {
        if (!cancelled) {
          setErrorNotice(renderError(err))
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const probe = await fetchAdminUsers(token, { limit: 1 })
        if (!cancelled) {
          setIsAdminUser(true)
          setAdminUsers(probe.items)
        }
        const users = await fetchAdminUsers(token, { limit: 500 })
        if (!cancelled) {
          setAdminUsers(users.items)
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && (err.status === 403 || err.status === 401)) {
            setIsAdminUser(false)
            setAdminUsers([])
          } else {
            handleTopLevelLoadError(err)
          }
        }
      } finally {
        if (!cancelled) {
          setIsAdminResolved(true)
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    if (!isAdminResolved) return
    if (route.section === 'admin-users' && !isAdminUser) {
      onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' }, { replace: true })
    }
  }, [isAdminResolved, isAdminUser, onNavigate, route.section])

  useEffect(() => {
    if (!selectedLedger) {
      setEditLedgerName('')
      setEditCurrency('CNY')
      return
    }
    setEditLedgerName(selectedLedger.ledger_name)
    setEditCurrency(selectedLedger.currency)
  }, [selectedLedger])

  // Connection supervisor: WebSocket with reconnect/heartbeat + /sync/pull
  // polling fallback + localStorage cursor, so mobile pushes still reach the
  // web tab when the socket silently dies behind a proxy or during tab sleep.
  const syncUserIdRef = useRef<string>('')
  if (!syncUserIdRef.current) {
    syncUserIdRef.current = getStoredUserId() || ''
  }
  const syncDeviceId = useMemo(() => getStoredDeviceId(), [token])
  const refreshCurrentRef = useRef(refreshCurrent)
  refreshCurrentRef.current = refreshCurrent
  const refreshAllSectionsRef = useRef(refreshAllSections)
  refreshAllSectionsRef.current = refreshAllSections

  const wsBuildUrl = useCallback((tok: string) => wsUrl(tok), [])

  useSyncSocket({
    token,
    buildUrl: wsBuildUrl,
    onEvent: (payload: unknown) => {
      const p = payload as { type?: string } | null
      if (p?.type === 'sync_change' || p?.type === 'backup_restore') {
        void refreshAllSectionsRef.current()
      }
    },
    onOpen: () => {
      // Socket (re)connected — catch up on anything the polling missed.
      if (token && syncUserIdRef.current) {
        void drainPull(token, syncUserIdRef.current, syncDeviceId).then((res) => {
          if (res.changes.length > 0) void refreshAllSectionsRef.current()
        })
      }
    }
  })

  useEffect(() => {
    if (!token) return
    const userId = syncUserIdRef.current
    if (!userId) return
    const poller = startPoller({
      token,
      userId,
      deviceId: syncDeviceId,
      onChanges: () => {
        void refreshAllSectionsRef.current()
      }
    })
    return () => poller.stop()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, syncDeviceId])

  useEffect(() => {
    if (route.section !== 'transactions') return
    if (typeof window === 'undefined') return
    txFilterRestoreInProgressRef.current = true
    const stored = parseStoredTxFilter(window.localStorage.getItem(txFilterPersistKey))
    const nextFilter = stored ?? defaultTxFilter()
    setListQuery(nextFilter.q)
    setTxFilterApplied(nextFilter)
    setTxFilterDraft(nextFilter)
    setTxPage(1)
    queueMicrotask(() => {
      txFilterRestoreInProgressRef.current = false
    })
  }, [route.section, txFilterPersistKey])

  useEffect(() => {
    if (route.section !== 'transactions') return
    if (typeof window === 'undefined') return
    if (txFilterRestoreInProgressRef.current) return
    const payload: TxFilter = {
      q: listQuery,
      txType: txFilterApplied.txType,
      accountName: txFilterApplied.accountName
    }
    window.localStorage.setItem(txFilterPersistKey, JSON.stringify(payload))
  }, [route.section, txFilterPersistKey, listQuery, txFilterApplied.txType, txFilterApplied.accountName])

  useEffect(() => {
    if (
      !isListSection(route.section) &&
      route.section !== 'admin-users' &&
      route.section !== 'settings-devices' &&
      route.section !== 'settings-health'
    ) {
      return
    }
    let cancelled = false
    const run = async () => {
      try {
        await refreshSectionData(activeLedgerId, route.section)
      } catch (err) {
        if (!cancelled) {
          setErrorNotice(renderError(err))
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    listUserFilter,
    listQuery,
    txFilterApplied.txType,
    txFilterApplied.accountName,
    route.section,
    isAdminUser,
    activeLedgerId,
    txPage,
    txPageSize,
    devicesWindowDays,
    adminUserStatusFilter
  ])

  useEffect(() => {
    if (route.section !== 'transactions') return
    if (txPage !== 1) {
      setTxPage(1)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLedgerId, listUserFilter, listQuery, txFilterApplied.txType, txFilterApplied.accountName, route.section])

  useEffect(() => {
    if (route.section !== 'transactions' || !isAdminResolved) return
    let cancelled = false
    const run = async () => {
      try {
        await loadTxDictionaries()
      } catch (err) {
        if (!cancelled) {
          setErrorNotice(renderError(err))
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.section, isAdminResolved, isAdminUser, listUserFilter, txForm.editingId, txForm.editingOwnerUserId, sessionUserId])

  useEffect(() => {
    if (!sectionNeedsLedger(route.section) || !activeLedgerId) return
    let cancelled = false
    const run = async () => {
      try {
        await loadLedgerBase(activeLedgerId)
        await refreshSectionData(activeLedgerId, route.section)
      } catch (err) {
        if (!cancelled) {
          setErrorNotice(renderError(err))
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLedgerId, route.section])

  useEffect(() => {
    const allowedIds = new Set(txWriteLedgerOptions.map((ledger) => ledger.ledger_id))
    if (txWriteLedgerId && allowedIds.has(txWriteLedgerId)) return
    if (activeLedgerId && allowedIds.has(activeLedgerId)) {
      setTxWriteLedgerId(activeLedgerId)
      return
    }
    setTxWriteLedgerId(txWriteLedgerOptions[0]?.ledger_id || '')
  }, [txWriteLedgerId, txWriteLedgerOptions, activeLedgerId])

  const resolveTxDictionaryUserId = (): string | undefined => {
    if (!isAdminUser) return undefined
    if (txForm.editingId && txForm.editingOwnerUserId.trim()) {
      return txForm.editingOwnerUserId.trim()
    }
    if (listUserFilter !== '__all__') {
      return listUserFilter
    }
    return sessionUserId || undefined
  }

  const resolveWorkspaceTargetUserId = (editingOwnerUserId?: string): string | undefined => {
    if (isAdminUser) {
      if (editingOwnerUserId && editingOwnerUserId.trim()) return editingOwnerUserId.trim()
      if (listUserFilter !== '__all__') return listUserFilter
    }
    return sessionUserId || undefined
  }

  const loadTxDictionaries = async () => {
    const targetUserId = resolveTxDictionaryUserId()
    // 账户 / 分类 / 标签在本产品里是"用户级"的 —— 一个用户的所有账本共享一套，
    // 所以这里拉全量，不按 ledger 过滤。具体哪些账户能在某个账本做交易的校验
    // 交给下面 useMemo（同币种 + 非估值账户）。
    setTxDictionaryLoading(true)
    try {
      const [accountRows, categoryRows, tagRows] = await Promise.all([
        fetchWorkspaceAccounts(token, {
          userId: targetUserId,
          limit: 2000
        }),
        fetchWorkspaceCategories(token, {
          userId: targetUserId,
          limit: 2000
        }),
        fetchWorkspaceTags(token, {
          userId: targetUserId,
          limit: 2000
        })
      ])
      setTxDictionaryAccounts(accountRows)
      setTxDictionaryCategories(categoryRows)
      setTxDictionaryTags(tagRows)
    } finally {
      setTxDictionaryLoading(false)
    }
  }

  const renderError = (err: unknown): string => localizeError(err, t)

  const fetchBaseChangeId = async (ledgerId: string): Promise<number> => {
    const detail = await fetchReadLedgerDetail(token, ledgerId)
    return detail.source_change_id
  }

  /**
   * Run a write that takes a base_change_id, auto-retrying on 409 WRITE_CONFLICT.
   * 409 almost always just means "mobile pushed a change between our base fetch
   * and our write"; the user's intent is still valid against the new head, so
   * we refetch and resubmit. Try up to 4 times (original + 3 retries) with a
   * tiny random back-off so we don't lock-step with a streaming mobile pusher.
   */
  const retryOnConflict = async <T,>(
    ledgerId: string,
    submit: (baseChangeId: number) => Promise<T>
  ): Promise<T> => {
    const maxAttempts = 4
    let lastErr: unknown
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const base = await fetchBaseChangeId(ledgerId)
      try {
        return await submit(base)
      } catch (err) {
        if (!(err instanceof ApiError) || err.code !== 'WRITE_CONFLICT') throw err
        lastErr = err
        if (attempt < maxAttempts - 1) {
          await new Promise((r) => setTimeout(r, 50 + Math.random() * 100))
        }
      }
    }
    throw lastErr
  }

  const handleWriteFailure = async (
    err: unknown,
    refreshTo: AppSection,
    ledgerId?: string
  ): Promise<boolean> => {
    if (!(err instanceof ApiError) || err.code !== 'WRITE_CONFLICT') return false
    if (!ledgerId) return false
    await loadLedgerBase(ledgerId)
    await refreshSectionData(ledgerId, refreshTo)
    setErrorNotice(localizeError(err, t))
    return true
  }

  const onRefresh = async () => {
    try {
      await Promise.all([refreshCurrent(), loadProfile()])
    } catch (err) {
      setErrorNotice(renderError(err))
    }
  }

  const onSaveProfileDisplayName = async () => {
    const nextName = profileDisplayName.trim()
    if (!nextName) {
      setErrorNotice(t('profile.error.displayNameRequired'))
      return
    }
    try {
      const updated = await patchProfileMe(token, { display_name: nextName })
      setProfileMe(updated)
      setProfileDisplayName(updated.display_name || '')
      setSuccessNotice(t('notice.profileUpdated'))
      await refreshSectionData(activeLedgerId, route.section)
    } catch (err) {
      setErrorNotice(renderError(err))
    }
  }

  const activeTxQuery = listQuery || txFilterApplied.q
  const txFilterActiveCount =
    Number(Boolean(activeTxQuery)) +
    Number(Boolean(txFilterApplied.txType)) +
    Number(Boolean(txFilterApplied.accountName))

  const onOpenTxFilter = () => {
    setTxFilterDraft({
      q: listQuery || txFilterApplied.q,
      txType: txFilterApplied.txType,
      accountName: txFilterApplied.accountName
    })
    setTxFilterOpen(true)
  }

  const onApplyTxFilter = () => {
    const next = { ...txFilterDraft }
    setTxFilterApplied((prev) => ({
      ...prev,
      txType: next.txType,
      q: next.q,
      accountName: next.accountName,
    }))
    setListQuery(next.q)
    setTxPage(1)
    setTxFilterOpen(false)
  }

  const onResetTxFilter = () => {
    const next = defaultTxFilter()
    setTxFilterDraft(next)
    setTxFilterApplied(next)
    setListQuery('')
    setTxPage(1)
    setTxFilterOpen(false)
  }

  const resolveTxAttachmentPreviewUrl = useCallback(
    async (attachment: AttachmentRef): Promise<string | null> => {
      const fileId = attachment.cloudFileId?.trim()
      if (!fileId) return null
      const cached = txAttachmentPreviewUrlByFileIdRef.current[fileId]
      if (cached) return cached

      try {
        const response = await downloadAttachment(token, fileId)
        const fileName =
          response.fileName ||
          attachment.originalName ||
          attachment.fileName ||
          `attachment-${fileId}`
        if (!isPreviewableImage(response.mimeType, fileName)) return null

        const blobUrl = URL.createObjectURL(response.blob)
        const latest = txAttachmentPreviewUrlByFileIdRef.current[fileId]
        if (latest) {
          URL.revokeObjectURL(blobUrl)
          return latest
        }
        txAttachmentPreviewUrlByFileIdRef.current[fileId] = blobUrl
        return blobUrl
      } catch {
        return null
      }
    },
    [token]
  )

  const openImagePreview = (objectUrl: string, fileName: string) => {
    setAttachmentPreview((prev) => {
      if (prev.objectUrl && prev.objectUrl !== objectUrl) {
        URL.revokeObjectURL(prev.objectUrl)
      }
      return {
        open: true,
        fileName,
        objectUrl
      }
    })
  }

  const onPreviewTxAttachment = async (attachment: AttachmentRef) => {
    const requestSeq = ++previewRequestSeqRef.current
    const fileId = attachment.cloudFileId?.trim()
    if (!fileId) {
      setErrorNotice(t('transactions.attachment.metadataOnly'))
      return
    }
    try {
      const response = await downloadAttachment(token, fileId)
      const fileName =
        response.fileName ||
        attachment.originalName ||
        attachment.fileName ||
        `attachment-${fileId}`
      if (!isPreviewableImage(response.mimeType, fileName)) {
        if (requestSeq !== previewRequestSeqRef.current) return
        setErrorNotice(t('transactions.attachment.notPreviewable'))
        return
      }
      if (requestSeq !== previewRequestSeqRef.current) return
      const blobUrl = URL.createObjectURL(response.blob)
      openImagePreview(blobUrl, fileName)
    } catch (err) {
      if (requestSeq !== previewRequestSeqRef.current) return
      setErrorNotice(renderError(err))
    }
  }

  const onUploadTxAttachments = async (files: File[]): Promise<AttachmentRef[]> => {
    const ledgerId = txWriteLedgerId.trim()
    if (!ledgerId) {
      setErrorNotice(t('transactions.error.ledgerRequired'))
      return []
    }
    if (files.length === 0) return []

    try {
      const fileWithDigest = await Promise.all(
        files.map(async (file) => {
          const digest = await sha256Hex(await file.arrayBuffer())
          return { file, digest }
        })
      )

      const exists = await batchAttachmentExists(token, {
        ledger_id: ledgerId,
        sha256_list: fileWithDigest.map((row) => row.digest)
      })
      const existsBySha = new Map(exists.items.map((row) => [row.sha256, row]))
      const out: AttachmentRef[] = []

      for (const row of fileWithDigest) {
        const existed = existsBySha.get(row.digest)
        let fileId = existed?.file_id || null
        let fileName = row.file.name
        let size = row.file.size
        if (!fileId) {
          const uploaded = await uploadAttachment(token, {
            ledger_id: ledgerId,
            file: row.file,
            mime_type: row.file.type || null
          })
          fileId = uploaded.file_id
          fileName = uploaded.file_name || row.file.name
          size = uploaded.size || row.file.size
        }

        const localFileName = fileId ? `${fileId}_${fileName}` : fileName

        out.push({
          fileName: localFileName,
          originalName: row.file.name,
          fileSize: size,
          sortOrder: out.length,
          cloudFileId: fileId,
          cloudSha256: row.digest
        })
      }
      return out
    } catch (err) {
      setErrorNotice(renderError(err))
      return []
    }
  }

  const ensureCategoryIconPreview = async (fileId: string) => {
    const normalized = fileId.trim()
    if (!normalized) return
    if (categoryIconPreviewByFileId[normalized]) return
    try {
      const response = await downloadAttachment(token, normalized)
      if (!isPreviewableImage(response.mimeType, response.fileName)) {
        return
      }
      const nextUrl = URL.createObjectURL(response.blob)
      setCategoryIconPreviewByFileId((prev) => {
        if (prev[normalized]) {
          URL.revokeObjectURL(nextUrl)
          return prev
        }
        return {
          ...prev,
          [normalized]: nextUrl
        }
      })
    } catch {
      // Keep category rendering non-blocking when icon file is unavailable.
    }
  }

  const onCreateLedger = async () => {
    try {
      const response = await createLedger(token, {
        ledger_name: createLedgerName.trim() || 'New Ledger',
        currency: createCurrency.trim() || 'CNY'
      })
      setCreateLedgerName('')
      setCreateCurrency('CNY')
      setCreateLedgerDialogOpen(false)
      await loadLedgers()
      setActiveLedgerId(response.ledger_id)
      setTxWriteLedgerId(response.ledger_id)
      onNavigate({ kind: 'app', ledgerId: '', section: 'overview' })
      setSuccessNotice(t('notice.ledgerCreated'))
    } catch (err) {
      setErrorNotice(renderError(err))
    }
  }

  const onUpdateLedgerMeta = async () => {
    if (!activeLedgerId) return
    try {
      const response = await retryOnConflict(activeLedgerId, (base) =>
        updateLedgerMeta(token, activeLedgerId, base, {
          ledger_name: editLedgerName,
          currency: editCurrency
        })
      )
      setBaseChangeId(response.new_change_id)
      await refreshCurrent('overview')
      setSuccessNotice(t('notice.ledgerUpdated'))
    } catch (err) {
      if (await handleWriteFailure(err, 'overview', activeLedgerId)) return
      setErrorNotice(renderError(err))
    }
  }

  const onSaveTransaction = async (): Promise<boolean> => {
    const ledgerId = txWriteLedgerId.trim()
    if (!ledgerId) {
      setErrorNotice(t('transactions.error.ledgerRequired'))
      return false
    }
    if (txForm.tx_type === 'transfer') {
      // 转账必须两边都选且不同 —— 否则语义无法表达。
      if (!txForm.from_account_name.trim() || !txForm.to_account_name.trim()) {
        setErrorNotice(t('transactions.error.transferAccountsRequired'))
        return false
      }
      if (txForm.from_account_name.trim() === txForm.to_account_name.trim()) {
        setErrorNotice(t('transactions.error.transferAccountsDifferent'))
        return false
      }
    }
    // 非转账交易允许不选账户（mobile 端 accountId 本来就是 nullable），之前 web
    // 强制校验导致 mobile 导入的无账户交易在 web 上无法编辑。

    try {
      const isTransfer = txForm.tx_type === 'transfer'
      const accountByName = new Map(
        txWriteAccounts
          .filter((row) => row.name.trim())
          .map((row) => [row.name.trim().toLowerCase(), row.id] as const)
      )
      const categoryByKey = new Map(
        txWriteCategories
          .filter((row) => row.name.trim())
          .map((row) => [`${row.kind}:${row.name.trim().toLowerCase()}`, row.id] as const)
      )
      const tagByName = new Map(
        txWriteTags
          .filter((row) => row.name.trim())
          .map((row) => [row.name.trim().toLowerCase(), row.id] as const)
      )

      const accountName = txForm.account_name.trim()
      const fromAccountName = txForm.from_account_name.trim()
      const toAccountName = txForm.to_account_name.trim()
      const categoryName = txForm.category_name.trim()
      const categoryKind = txForm.category_kind
      const txTagIds = txForm.tags
        .map((value) => tagByName.get(value.trim().toLowerCase()))
        .filter((value): value is string => Boolean(value))

      const payload = {
        tx_type: txForm.tx_type,
        amount: Number(txForm.amount || 0),
        happened_at: txForm.happened_at || new Date().toISOString(),
        note: txForm.note || null,
        category_name: isTransfer ? null : categoryName || null,
        category_kind: isTransfer ? null : categoryKind || null,
        category_id: isTransfer ? null : categoryByKey.get(`${categoryKind}:${categoryName.toLowerCase()}`) || null,
        account_name: isTransfer ? null : accountName || null,
        account_id: isTransfer ? null : accountByName.get(accountName.toLowerCase()) || null,
        from_account_name: isTransfer ? fromAccountName || null : null,
        from_account_id: isTransfer ? accountByName.get(fromAccountName.toLowerCase()) || null : null,
        to_account_name: isTransfer ? toAccountName || null : null,
        to_account_id: isTransfer ? accountByName.get(toAccountName.toLowerCase()) || null : null,
        tags: txForm.tags.length > 0 ? txForm.tags : null,
        tag_ids: txTagIds.length > 0 ? txTagIds : null,
        attachments: txForm.attachments.length > 0 ? txForm.attachments : null
      }
      // eslint-disable-next-line no-console
      console.info('[tx-save] request', {
        editingId: txForm.editingId,
        ledgerId,
        payload_tags: payload.tags,
        payload_account_name: payload.account_name,
        payload_account_id: payload.account_id
      })
      const res = await retryOnConflict(ledgerId, (base) =>
        txForm.editingId
          ? updateTransaction(token, ledgerId, txForm.editingId, base, payload)
          : createTransaction(token, ledgerId, base, payload)
      )
      // eslint-disable-next-line no-console
      console.info('[tx-save] response', {
        entity_id: res.entity_id,
        new_change_id: res.new_change_id,
        server_timestamp: res.server_timestamp
      })
      if (activeLedgerId === ledgerId) {
        setBaseChangeId(res.new_change_id)
      }
      const editingTxId = txForm.editingId
      setTxForm(txDefaults())
      const refreshLedger = activeLedgerId || ledgerId
      await refreshSectionData(refreshLedger, 'transactions')
      // 再打一次查询看服务端回给我们的具体这条 tx 的 tags/account_name；
      // 排查"更新没生效"时先看 server 是不是真的返回新值了。
      if (editingTxId) {
        try {
          const verifyPage = await fetchWorkspaceTransactions(token, {
            ledgerId: refreshLedger || undefined,
            limit: txPageSize,
            offset: (txPage - 1) * txPageSize
          })
          const hit = verifyPage.items.find((row) => row.id === editingTxId)
          // eslint-disable-next-line no-console
          console.info('[tx-save] server returned for updated tx', {
            id: editingTxId,
            tags: hit?.tags,
            tags_list: hit?.tags_list,
            account_name: hit?.account_name
          })
        } catch (_) {
          // 诊断用，静默失败
        }
      }
      setSuccessNotice(txForm.editingId ? t('notice.txUpdated') : t('notice.txCreated'))
      return true
    } catch (err) {
      if (await handleWriteFailure(err, 'transactions', ledgerId)) return false
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteTransaction = async (txId: string, ledgerId: string) => {
    if (!ledgerId) return
    const res = await retryOnConflict(ledgerId, (base) =>
      deleteTransaction(token, ledgerId, txId, base)
    )
    if (activeLedgerId === ledgerId) {
      setBaseChangeId(res.new_change_id)
    }
    await refreshSectionData(activeLedgerId || ledgerId, 'transactions')
    setSuccessNotice(t('notice.txDeleted'))
  }

  const onSaveAccount = async (): Promise<boolean> => {
    if (!activeLedgerId) return false
    try {
      const payload = {
        name: accountForm.name,
        account_type: accountForm.account_type || null,
        currency: accountForm.currency || null,
        initial_balance: Number(accountForm.initial_balance || 0)
      }
      const res = await retryOnConflict(activeLedgerId, (base) =>
        accountForm.editingId
          ? updateAccount(token, activeLedgerId, accountForm.editingId, base, payload)
          : createAccount(token, activeLedgerId, base, payload)
      )
      setBaseChangeId(res.new_change_id)
      setAccountForm(accountDefaults())
      await refreshSectionData(activeLedgerId, 'accounts')
      setSuccessNotice(accountForm.editingId ? t('notice.accountUpdated') : t('notice.accountCreated'))
      return true
    } catch (err) {
      if (await handleWriteFailure(err, 'accounts', activeLedgerId)) return false
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteAccount = async (accountId: string) => {
    if (!activeLedgerId) return
    const res = await retryOnConflict(activeLedgerId, (base) =>
      deleteAccount(token, activeLedgerId, accountId, base)
    )
    setBaseChangeId(res.new_change_id)
    await refreshSectionData(activeLedgerId, 'accounts')
    setSuccessNotice(t('notice.accountDeleted'))
  }

  const onSaveCategory = async (): Promise<boolean> => {
    if (!activeLedgerId) return false
    try {
      const payload = {
        name: categoryForm.name,
        kind: categoryForm.kind,
        level: categoryForm.level ? Number(categoryForm.level) : null,
        sort_order: categoryForm.sort_order ? Number(categoryForm.sort_order) : null,
        icon: categoryForm.icon || null,
        icon_type: categoryForm.icon_type || null,
        custom_icon_path: categoryForm.custom_icon_path || null,
        icon_cloud_file_id: categoryForm.icon_cloud_file_id || null,
        icon_cloud_sha256: categoryForm.icon_cloud_sha256 || null,
        parent_name: categoryForm.parent_name || null
      }
      const res = await retryOnConflict(activeLedgerId, (base) =>
        categoryForm.editingId
          ? updateCategory(token, activeLedgerId, categoryForm.editingId, base, payload)
          : createCategory(token, activeLedgerId, base, payload)
      )
      setBaseChangeId(res.new_change_id)
      setCategoryForm(categoryDefaults())
      await refreshSectionData(activeLedgerId, 'categories')
      setSuccessNotice(categoryForm.editingId ? t('notice.categoryUpdated') : t('notice.categoryCreated'))
      return true
    } catch (err) {
      if (await handleWriteFailure(err, 'categories', activeLedgerId)) return false
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteCategory = async (categoryId: string) => {
    if (!activeLedgerId) return
    const res = await retryOnConflict(activeLedgerId, (base) =>
      deleteCategory(token, activeLedgerId, categoryId, base)
    )
    setBaseChangeId(res.new_change_id)
    await refreshSectionData(activeLedgerId, 'categories')
    setSuccessNotice(t('notice.categoryDeleted'))
  }

  const onSaveTag = async (): Promise<boolean> => {
    if (!activeLedgerId) return false
    try {
      const payload = {
        name: tagForm.name,
        color: tagForm.color || null
      }
      const res = await retryOnConflict(activeLedgerId, (base) =>
        tagForm.editingId
          ? updateTag(token, activeLedgerId, tagForm.editingId, base, payload)
          : createTag(token, activeLedgerId, base, payload)
      )
      setBaseChangeId(res.new_change_id)
      setTagForm(tagDefaults())
      await refreshSectionData(activeLedgerId, 'tags')
      setSuccessNotice(tagForm.editingId ? t('notice.tagUpdated') : t('notice.tagCreated'))
      return true
    } catch (err) {
      if (await handleWriteFailure(err, 'tags', activeLedgerId)) return false
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteTag = async (tagId: string) => {
    if (!activeLedgerId) return
    const res = await retryOnConflict(activeLedgerId, (base) =>
      deleteTag(token, activeLedgerId, tagId, base)
    )
    setBaseChangeId(res.new_change_id)
    await refreshSectionData(activeLedgerId, 'tags')
    setSuccessNotice(t('notice.tagDeleted'))
  }

  const onPatchAdminUser = async (
    userId: string,
    payload: { is_admin?: boolean; is_enabled?: boolean }
  ): Promise<boolean> => {
    try {
      await patchAdminUser(token, userId, payload)
      if (route.section === 'admin-users') {
        await refreshSectionData('', 'admin-users')
      }
      setSuccessNotice(t('notice.userUpdated'))
      return true
    } catch (err) {
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onCreateAdminUser = async (): Promise<boolean> => {
    if (!adminCreateEmail.trim() || !adminCreatePassword.trim()) {
      setErrorNotice(t('admin.users.error.createRequired'))
      return false
    }
    try {
      await createAdminUser(token, {
        email: adminCreateEmail.trim(),
        password: adminCreatePassword,
        is_admin: adminCreateIsAdmin,
        is_enabled: adminCreateIsEnabled
      })
      await refreshSectionData('', 'admin-users')
      setAdminCreateEmail('')
      setAdminCreatePassword('')
      setAdminCreateIsAdmin(false)
      setAdminCreateIsEnabled(true)
      setSuccessNotice(t('notice.userCreated'))
      return true
    } catch (err) {
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteAdminUser = async (userId: string): Promise<boolean> => {
    try {
      await deleteAdminUser(token, userId)
      await refreshSectionData('', 'admin-users')
      setSuccessNotice(t('notice.userDeleted'))
      return true
    } catch (err) {
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteLedger = async (ledgerId: string) => {
    await deleteLedger(token, ledgerId)
    await refreshCurrent('overview')
    setSuccessNotice(t('notice.ledgerDeleted') || '账本已删除')
  }

  const onConfirmDelete = async () => {
    if (!pendingDelete) return
    try {
      if (pendingDelete.kind === 'tx') await onDeleteTransaction(pendingDelete.id, pendingDelete.ledgerId)
      if (pendingDelete.kind === 'account') await onDeleteAccount(pendingDelete.id)
      if (pendingDelete.kind === 'category') await onDeleteCategory(pendingDelete.id)
      if (pendingDelete.kind === 'tag') await onDeleteTag(pendingDelete.id)
      if (pendingDelete.kind === 'ledger') await onDeleteLedger(pendingDelete.ledgerId)
    } catch (err) {
      if (
        pendingDelete.kind === 'tx' &&
        (await handleWriteFailure(err, route.section, pendingDelete.ledgerId))
      ) {
        return
      }
      setErrorNotice(renderError(err))
    } finally {
      setPendingDelete(null)
    }
  }

  useEffect(() => {
    if (route.section !== 'categories') return
    const missingFileIds = categories
      .map((row) => row.icon_cloud_file_id || '')
      .filter((value) => value.trim().length > 0)
      .filter((value, index, arr) => arr.indexOf(value) === index)
      .filter((value) => !categoryIconPreviewByFileId[value])
    if (missingFileIds.length === 0) return
    for (const fileId of missingFileIds) {
      void ensureCategoryIconPreview(fileId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.section, categories, categoryIconPreviewByFileId, token])

  useEffect(() => {
    return () => {
      Object.values(txAttachmentPreviewUrlByFileIdRef.current).forEach((url) => {
        URL.revokeObjectURL(url)
      })
      txAttachmentPreviewUrlByFileIdRef.current = {}
    }
  }, [])

  // Overview 页的图表数据：跨账本的月度序列 + Top 分类排行。
  // 同步完成/账本切换时 analyticsRefreshTick 变更，触发重新拉。
  const [analyticsRefreshTick, setAnalyticsRefreshTick] = useState(0)
  useEffect(() => {
    if (route.section !== 'overview') return
    let cancelled = false
    ;(async () => {
      try {
        // 用 scope=year 覆盖整年数据，避免当月为空时 Top 分类/趋势图空白；
        // ledgerId 传 active 账本以缩小范围，用户在 header 切账本时同步刷新。
        // 支出/收入 Top 各一轮查询（backend 按 metric 过滤 category_ranks）。
        const [yearlyExpense, yearlyIncome] = await Promise.all([
          fetchWorkspaceAnalytics(token, {
            scope: 'year',
            metric: 'expense',
            ledgerId: activeLedgerId || undefined
          }),
          fetchWorkspaceAnalytics(token, {
            scope: 'year',
            metric: 'income',
            ledgerId: activeLedgerId || undefined
          })
        ])
        if (!cancelled) {
          setAnalyticsData(yearlyExpense)
          setAnalyticsIncomeRanks(yearlyIncome.category_ranks || [])
        }
      } catch (err) {
        // 静默降级：dashboard 空态已覆盖。
        void err
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.section, token, analyticsRefreshTick, activeLedgerId])

  // 同步/实时事件发生时 bump analytics 刷新 tick（复用 sync generation 思路）。
  useEffect(() => {
    setAnalyticsRefreshTick((v) => v + 1)
  }, [ledgers.length, transactions.length])

  // 每个 tag 的统计：笔数 / 支出 / 收入。服务端一次性按全账本全期汇总好直接
  // 放在 WorkspaceTag 上（tx_count / expense_total / income_total），不再
  // 基于分页后的 transactions 自己聚合 —— 那会导致收入常被漏（当前页只有支
  // 出）、笔数偏少。
  const tagStatsById = useMemo(() => {
    const out: Record<string, { count: number; expense: number; income: number }> = {}
    for (const tag of tags) {
      if (!tag.id) continue
      out[tag.id] = {
        count: tag.tx_count ?? 0,
        expense: tag.expense_total ?? 0,
        income: tag.income_total ?? 0
      }
    }
    return out
  }, [tags])

  const showTxFilter = route.section === 'transactions'

  return (
    <>
      <AppLayout
        header={
          <div className="sticky top-0 z-50 px-4 pb-2 pt-3 md:px-6 md:pt-4">
            <header className="card px-3 md:px-5">
              <div className="flex h-14 items-center justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <img alt="蜜蜂记账" className="h-8 w-8 shrink-0" src="/branding/logo.svg" />
                  <p className="text-[15px] font-bold text-foreground">蜜蜂记账</p>
                  {/* 账本切换器：常驻 header 左侧，全局控制 activeLedger。
                      需要账本上下文的分区（交易/账户/分类/标签/纵览）全部以它为准。 */}
                  {ledgers.length > 0 ? (
                    <Select
                      value={activeLedgerId || undefined}
                      onValueChange={setActiveLedgerId}
                    >
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
                  {ledgers.length === 0 ? (
                    <Button
                      className="ml-1 hidden h-8 px-3 text-xs md:inline-flex"
                      size="sm"
                      onClick={() => {
                        onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                        setCreateLedgerDialogOpen(true)
                      }}
                    >
                      {t('shell.createLedger')}
                    </Button>
                  ) : null}
                </div>

                <nav className="hidden flex-1 items-center justify-center gap-1 md:flex">
                  {headerCoreItems.map((item) => {
                    const active = route.section === item.key
                    return (
                      <button
                        key={item.key}
                        className="relative"
                        type="button"
                        onClick={() => onNavigate({ kind: 'app', ledgerId: '', section: item.key })}
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
                              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                          }`}
                        >
                          {t(item.labelKey)}
                        </span>
                      </button>
                    )
                  })}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        className={`relative rounded-xl px-3.5 py-2 text-[13px] font-medium transition-all ${
                          moreMenuActive
                            ? 'bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--secondary)/0.12))] text-foreground ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                            : 'text-muted-foreground hover:bg-accent hover:text-foreground'
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
                            const active = route.section === item.key
                            return (
                              <DropdownMenuItem
                                key={item.key}
                                className={`rounded-lg px-2.5 py-2 text-[12px] ${
                                  active
                                    ? 'bg-primary/10 text-primary'
                                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                                }`}
                                onClick={() => onNavigate({ kind: 'app', ledgerId: '', section: item.key })}
                              >
                                {t(item.labelKey)}
                              </DropdownMenuItem>
                            )
                          })}
                        </div>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </nav>

                <div className="flex items-center gap-2 rounded-2xl border border-border/40 bg-accent/20 px-2 py-1">
                  {/* 登录邮箱 + 头像；点头像打开菜单看完整邮箱/profile。 */}
                  {/* 只展示头像（hover 时 title 提示邮箱），不再用文字占横向空间 */}
                  {profileMe?.email ? (
                    <Tooltip content={profileMe.display_name || profileMe.email}>
                      {profileMe.avatar_url ? (
                        <img
                          src={profileMe.avatar_url}
                          alt=""
                          className="h-8 w-8 shrink-0 rounded-full border border-border/40 object-cover"
                        />
                      ) : (
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border/40 bg-muted text-[11px] font-semibold text-muted-foreground">
                          {profileMe.email.slice(0, 1).toUpperCase()}
                        </div>
                      )}
                    </Tooltip>
                  ) : null}
                  <LanguageToggle />
                  <ThemeToggle />
                  <Tooltip content={t('shell.logout')}>
                    <Button
                      aria-label={t('shell.logout')}
                      className="h-9 w-9 bg-transparent"
                      size="icon"
                      variant="ghost"
                      onClick={onLogout}
                    >
                      <LogOut className="h-4 w-4" />
                    </Button>
                  </Tooltip>
                </div>
              </div>

              {ledgers.length > 0 ? (
                <div className="flex items-center gap-2 border-t border-border/50 py-2 md:hidden">
                  <Select
                    value={activeLedgerId || undefined}
                    onValueChange={setActiveLedgerId}
                  >
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

              <div className="border-t border-border/50 py-2 md:hidden">
                <div className="flex items-center gap-2">
                  <div className="scrollbar flex-1 overflow-x-auto">
                    <div className="inline-flex min-w-max items-center gap-1 pr-1">
                      {headerCoreItems.map((item) => {
                        const active = route.section === item.key
                        return (
                          <button
                            key={item.key}
                            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                              active
                                ? 'bg-primary/12 text-primary ring-1 ring-primary/20'
                                : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                            }`}
                            type="button"
                            onClick={() => onNavigate({ kind: 'app', ledgerId: '', section: item.key })}
                          >
                            {t(item.labelKey)}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button aria-label={t('shell.more')} className="h-8 w-8" size="icon" variant="outline">
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56 rounded-xl border-border/60 bg-card/95 p-1.5">
                      {headerMoreGroups.map((group, groupIndex) => (
                        <div key={group.key}>
                          {groupIndex > 0 ? <DropdownMenuSeparator /> : null}
                          <DropdownMenuLabel className="px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                            {t(group.titleKey)}
                          </DropdownMenuLabel>
                          {group.items.map((item) => {
                            const active = route.section === item.key
                            return (
                              <DropdownMenuItem
                                key={item.key}
                                className={`rounded-lg px-2.5 py-2 text-[12px] ${
                                  active
                                    ? 'bg-primary/10 text-primary'
                                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                                }`}
                                onClick={() => onNavigate({ kind: 'app', ledgerId: '', section: item.key })}
                              >
                                {t(item.labelKey)}
                              </DropdownMenuItem>
                            )
                          })}
                        </div>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                  {ledgers.length === 0 ? (
                    <Button
                      className="h-8 px-3 text-xs"
                      size="sm"
                      onClick={() => {
                        onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                        setCreateLedgerDialogOpen(true)
                      }}
                    >
                      {t('shell.createLedger')}
                    </Button>
                  ) : null}
                </div>
              </div>
            </header>
          </div>
        }
      >
        <div className="space-y-4">
          {route.section === 'overview' ? (
            <div className="space-y-4">
              <OverviewHero ledgers={ledgers} monthSeries={analyticsData?.series} />
              <OverviewKeyMetrics summary={analyticsData?.summary} />
              <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                <AssetCompositionDonut accounts={accounts} />
                <MonthlyTrendBars data={analyticsData?.series || []} />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <TopCategoriesList
                  ranks={analyticsData?.category_ranks || []}
                  variant="expense"
                  title="今年支出 Top 5"
                  onClickCategory={(name) => {
                    setListQuery(name)
                    onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                  }}
                />
                <TopCategoriesList
                  ranks={analyticsIncomeRanks}
                  variant="income"
                  title="今年收入 Top 5"
                  onClickCategory={(name) => {
                    setListQuery(name)
                    onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                  }}
                />
              </div>
            </div>
          ) : null}

          {route.section === 'transactions' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardContent className="pt-4">
                  <div className="bc-toolbar flex flex-wrap items-center gap-3">
                    <Input
                      className="h-9 w-[220px] bg-muted lg:w-[320px]"
                      placeholder={t('shell.placeholder.keyword')}
                      value={listQuery}
                      onChange={(event) => setListQuery(event.target.value)}
                    />
                    {isAdminUser ? (
                      <Select value={listUserFilter} onValueChange={setListUserFilter}>
                        <SelectTrigger className="h-9 w-[240px] bg-muted shadow-sm">
                          <SelectValue placeholder={t('shell.userFilter')} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__all__">{t('shell.allUsers')}</SelectItem>
                          {adminUsers.map((user) => (
                            <SelectItem key={user.id} value={user.id}>
                              {user.email}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                    {showTxFilter ? (
                      <div className="relative">
                        <Tooltip content={t('shell.filter.title')}>
                          <Button
                            aria-label={t('shell.filter.title')}
                            className="h-9 w-9 bg-muted"
                            size="icon"
                            variant="outline"
                            onClick={onOpenTxFilter}
                          >
                            <SlidersHorizontal className="h-4 w-4" />
                          </Button>
                        </Tooltip>
                        {txFilterActiveCount > 0 ? (
                          <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-primary" />
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
              <TransactionsPanel
                form={txForm}
                rows={transactions}
                total={txTotal}
                page={txPage}
                pageSize={txPageSize}
                accounts={txWriteAccounts}
                categories={txWriteCategories}
                tags={txWriteTags}
                ledgerOptions={txWriteLedgerOptions}
                writeLedgerId={txWriteLedgerId}
                onWriteLedgerIdChange={setTxWriteLedgerId}
                onPageChange={setTxPage}
                onPageSizeChange={(size) => {
                  setTxPageSize(size)
                  setTxPage(1)
                }}
                canWrite={Boolean(canWriteTx)}
                dictionariesLoading={txDictionaryLoading}
                showCreatorColumn={isAdminUser}
                showLedgerColumn
                onFormChange={setTxForm}
                onSave={onSaveTransaction}
                onReset={() => {
                  setTxForm(txDefaults())
                  if (
                    activeLedgerId &&
                    txWriteLedgerOptions.some((option) => option.ledger_id === activeLedgerId)
                  ) {
                    setTxWriteLedgerId(activeLedgerId)
                    return
                  }
                  setTxWriteLedgerId(txWriteLedgerOptions[0]?.ledger_id || '')
                }}
                onReload={onRefresh}
                onPreviewAttachment={onPreviewTxAttachment}
                resolveAttachmentPreviewUrl={resolveTxAttachmentPreviewUrl}
                onEdit={(tx) => {
                  setTxWriteLedgerId(tx.ledger_id || txWriteLedgerOptions[0]?.ledger_id || '')
                  setTxForm({
                    editingId: tx.id,
                    editingOwnerUserId: tx.created_by_user_id || '',
                    tx_type: tx.tx_type,
                    amount: String(tx.amount),
                    happened_at: tx.happened_at,
                    note: tx.note || '',
                    category_name: tx.category_name || '',
                    category_kind: (tx.category_kind as TxForm['category_kind']) || 'expense',
                    account_name: tx.account_name || '',
                    from_account_name: tx.from_account_name || '',
                    to_account_name: tx.to_account_name || '',
                    tags:
                      tx.tags_list && tx.tags_list.length > 0
                        ? tx.tags_list
                        : (tx.tags || '')
                            .split(',')
                            .map((value) => value.trim())
                            .filter((value) => value.length > 0),
                    attachments: normalizeAttachmentRefs(tx.attachments)
                  })
                }}
                onDelete={(row) =>
                  setPendingDelete({
                    kind: 'tx',
                    id: row.id,
                    ledgerId: row.ledger_id || txWriteLedgerId || activeLedgerId
                  })
                }
              />
            </div>
          ) : null}

          {route.section === 'accounts' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardContent className="pt-4">
                  <div className="bc-toolbar flex flex-wrap items-center gap-3">
                    <Input
                      className="h-9 w-[220px] bg-muted lg:w-[320px]"
                      placeholder={t('shell.placeholder.keyword')}
                      value={listQuery}
                      onChange={(event) => setListQuery(event.target.value)}
                    />
                    {isAdminUser ? (
                      <Select value={listUserFilter} onValueChange={setListUserFilter}>
                        <SelectTrigger className="h-9 w-[240px] bg-muted shadow-sm">
                          <SelectValue placeholder={t('shell.userFilter')} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__all__">{t('shell.allUsers')}</SelectItem>
                          {adminUsers.map((user) => (
                            <SelectItem key={user.id} value={user.id}>
                              {user.email}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
              <AccountsPanel
                form={accountForm}
                rows={accounts}
                canManage
                showCreatorColumn={isAdminUser}
                onFormChange={setAccountForm}
                onSave={onSaveAccount}
                onReset={() => setAccountForm(accountDefaults())}
                onEdit={(row) => {
                  setAccountForm({
                    editingId: row.id,
                    editingOwnerUserId: row.created_by_user_id || '',
                    name: row.name,
                    account_type: row.account_type || '',
                    currency: row.currency || '',
                    initial_balance: String(row.initial_balance ?? 0)
                  })
                }}
              />
            </div>
          ) : null}

          {route.section === 'categories' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardContent className="pt-4">
                  <div className="bc-toolbar flex flex-wrap items-center gap-3">
                    <Input
                      className="h-9 w-[220px] bg-muted lg:w-[320px]"
                      placeholder={t('shell.placeholder.keyword')}
                      value={listQuery}
                      onChange={(event) => setListQuery(event.target.value)}
                    />
                    {isAdminUser ? (
                      <Select value={listUserFilter} onValueChange={setListUserFilter}>
                        <SelectTrigger className="h-9 w-[240px] bg-muted shadow-sm">
                          <SelectValue placeholder={t('shell.userFilter')} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__all__">{t('shell.allUsers')}</SelectItem>
                          {adminUsers.map((user) => (
                            <SelectItem key={user.id} value={user.id}>
                              {user.email}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
              <CategoriesPanel
                form={categoryForm}
                rows={categories}
                iconPreviewUrlByFileId={categoryIconPreviewByFileId}
                canManage
                showCreatorColumn={isAdminUser}
                onFormChange={setCategoryForm}
                onSave={onSaveCategory}
                onReset={() => setCategoryForm(categoryDefaults())}
                onEdit={(row) => {
                  setCategoryForm({
                    editingId: row.id,
                    editingOwnerUserId: row.created_by_user_id || '',
                    name: row.name,
                    kind: row.kind,
                    level: String(row.level ?? ''),
                    sort_order: String(row.sort_order ?? ''),
                    icon: row.icon || '',
                    icon_type: row.icon_type || 'material',
                    custom_icon_path: row.custom_icon_path || '',
                    icon_cloud_file_id: row.icon_cloud_file_id || '',
                    icon_cloud_sha256: row.icon_cloud_sha256 || '',
                    parent_name: row.parent_name || ''
                  })
                }}
                onUploadIcon={async (file) => {
                  if (!activeLedgerId) {
                    setErrorNotice(t('accounts.error.ledgerRequired'))
                    return null
                  }
                  try {
                    const out = await uploadAttachment(token, {
                      ledger_id: activeLedgerId,
                      file
                    })
                    return { fileId: out.file_id, sha256: out.sha256 }
                  } catch (err) {
                    setErrorNotice(renderError(err))
                    return null
                  }
                }}
              />
            </div>
          ) : null}

          {route.section === 'tags' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardContent className="pt-4">
                  <div className="bc-toolbar flex flex-wrap items-center gap-3">
                    <Input
                      className="h-9 w-[220px] bg-muted lg:w-[320px]"
                      placeholder={t('shell.placeholder.keyword')}
                      value={listQuery}
                      onChange={(event) => setListQuery(event.target.value)}
                    />
                    {isAdminUser ? (
                      <Select value={listUserFilter} onValueChange={setListUserFilter}>
                        <SelectTrigger className="h-9 w-[240px] bg-muted shadow-sm">
                          <SelectValue placeholder={t('shell.userFilter')} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__all__">{t('shell.allUsers')}</SelectItem>
                          {adminUsers.map((user) => (
                            <SelectItem key={user.id} value={user.id}>
                              {user.email}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
              <TagsPanel
                form={tagForm}
                rows={tags}
                canManage
                showCreatorColumn={isAdminUser}
                statsById={tagStatsById}
                onFormChange={setTagForm}
                onSave={onSaveTag}
                onReset={() => setTagForm(tagDefaults())}
                onEdit={(row) => {
                  setTagForm({
                    editingId: row.id,
                    editingOwnerUserId: row.created_by_user_id || '',
                    name: row.name,
                    color: row.color || '#F59E0B'
                  })
                }}
              />
            </div>
          ) : null}

          {route.section === 'settings-devices' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardContent className="pt-4">
                  <div className="bc-toolbar flex flex-wrap items-center gap-3">
                    <Input
                      className="h-9 w-[220px] bg-muted lg:w-[320px]"
                      placeholder={t('shell.placeholder.keyword')}
                      value={listQuery}
                      onChange={(event) => setListQuery(event.target.value)}
                    />
                    {isAdminUser ? (
                      <Select value={listUserFilter} onValueChange={setListUserFilter}>
                        <SelectTrigger className="h-9 w-[240px] bg-muted shadow-sm">
                          <SelectValue placeholder={t('shell.userFilter')} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__all__">{t('shell.allUsers')}</SelectItem>
                          {adminUsers.map((user) => (
                            <SelectItem key={user.id} value={user.id}>
                              {user.email}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                    <Select value={devicesWindowDays} onValueChange={(value) => setDevicesWindowDays(value as '30' | 'all')}>
                      <SelectTrigger className="h-9 w-[180px] bg-muted shadow-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="30">{t('ops.devices.window.30d')}</SelectItem>
                        <SelectItem value="all">{t('ops.devices.window.all')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </CardContent>
              </Card>
              <OpsDevicesPanel rows={adminDevices} onReload={onRefresh} />
            </div>
          ) : null}

          {route.section === 'settings-health' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('profile.title')}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap items-center gap-3">
                    {profileMe?.avatar_url ? (
                      <img
                        alt={profileDisplayLabel}
                        className="h-12 w-12 rounded-full border border-border/60 object-cover"
                        src={profileMe.avatar_url}
                      />
                    ) : (
                      <div className="flex h-12 w-12 items-center justify-center rounded-full border border-border/60 bg-muted text-sm font-semibold text-muted-foreground">
                        {profileInitial}
                      </div>
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{profileDisplayLabel}</p>
                      <p className="truncate text-xs text-muted-foreground">{profileMe?.email || '-'}</p>
                    </div>
                  </div>
                  <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                    <div className="space-y-1">
                      <Label>{t('profile.displayName')}</Label>
                      <Input
                        maxLength={32}
                        placeholder={t('profile.displayNamePlaceholder')}
                        value={profileDisplayName}
                        onChange={(event) => setProfileDisplayName(event.target.value)}
                      />
                    </div>
                    <div className="flex items-end">
                      <Button
                        className="h-9"
                        onClick={onSaveProfileDisplayName}
                        disabled={!profileDisplayName.trim() || profileDisplayName.trim() === (profileMe?.display_name || '').trim()}
                      >
                        {t('profile.save')}
                      </Button>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">{t('profile.avatarManagedByApp')}</p>
                </CardContent>
              </Card>

              <Card className="bc-panel">
                <CardHeader className="flex flex-row items-center justify-between gap-3">
                  <CardTitle>{t('ops.health.title')}</CardTitle>
                  <Button size="sm" variant="outline" onClick={onRefresh}>
                    {t('ops.health.button.refresh')}
                  </Button>
                </CardHeader>
                <CardContent>
                  {adminHealth ? (
                    <div className="grid gap-3 text-sm md:grid-cols-2">
                      <div className="rounded-md border border-border px-3 py-2">
                        <p className="text-xs text-muted-foreground">status</p>
                        <p className="font-medium">{adminHealth.status || '-'}</p>
                      </div>
                      <div className="rounded-md border border-border px-3 py-2">
                        <p className="text-xs text-muted-foreground">db</p>
                        <p className="font-medium">{adminHealth.db || '-'}</p>
                      </div>
                      <div className="rounded-md border border-border px-3 py-2">
                        <p className="text-xs text-muted-foreground">online_ws_users</p>
                        <p className="font-medium">{adminHealth.online_ws_users}</p>
                      </div>
                      <div className="rounded-md border border-border px-3 py-2">
                        <p className="text-xs text-muted-foreground">time</p>
                        <p className="font-medium">{formatIsoDateTime(adminHealth.time)}</p>
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">{t('table.empty')}</p>
                  )}
                </CardContent>
              </Card>

              {isAdminUser ? (
                <Card className="bc-panel">
                  <CardHeader>
                    <CardTitle>{t('tab.summary')}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {adminOverview ? (
                      <div className="grid gap-3 text-sm md:grid-cols-2 lg:grid-cols-3">
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">users_total</p>
                          <p className="font-medium">{adminOverview.users_total}</p>
                        </div>
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">users_enabled_total</p>
                          <p className="font-medium">{adminOverview.users_enabled_total}</p>
                        </div>
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">ledgers_total</p>
                          <p className="font-medium">{adminOverview.ledgers_total}</p>
                        </div>
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">transactions_total</p>
                          <p className="font-medium">{adminOverview.transactions_total}</p>
                        </div>
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">accounts_total</p>
                          <p className="font-medium">{adminOverview.accounts_total}</p>
                        </div>
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">categories_total</p>
                          <p className="font-medium">{adminOverview.categories_total}</p>
                        </div>
                        <div className="rounded-md border border-border px-3 py-2">
                          <p className="text-xs text-muted-foreground">tags_total</p>
                          <p className="font-medium">{adminOverview.tags_total}</p>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">{t('table.empty')}</p>
                    )}
                  </CardContent>
                </Card>
              ) : null}
            </div>
          ) : null}

          {route.section === 'admin-users' ? (
            isAdminUser ? (
              <div className="space-y-4">
                <Card className="bc-panel">
                  <CardContent className="pt-4">
                    <Input
                      className="h-9 w-[220px] bg-muted lg:w-[320px]"
                      placeholder={t('shell.placeholder.keyword')}
                      value={listQuery}
                      onChange={(event) => setListQuery(event.target.value)}
                    />
                  </CardContent>
                </Card>
                <AdminUsersPanel
                  rows={adminUsers}
                  onReload={onRefresh}
                  onPatch={onPatchAdminUser}
                  onDelete={onDeleteAdminUser}
                  statusFilter={adminUserStatusFilter}
                  onStatusFilterChange={setAdminUserStatusFilter}
                  createEmail={adminCreateEmail}
                  createPassword={adminCreatePassword}
                  createIsAdmin={adminCreateIsAdmin}
                  createIsEnabled={adminCreateIsEnabled}
                  onCreateEmailChange={setAdminCreateEmail}
                  onCreatePasswordChange={setAdminCreatePassword}
                  onCreateIsAdminChange={setAdminCreateIsAdmin}
                  onCreateIsEnabledChange={setAdminCreateIsEnabled}
                  onCreate={onCreateAdminUser}
                />
              </div>
            ) : (
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('admin.users.title')}</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">{t('admin.users.noPermission')}</CardContent>
              </Card>
            )
          ) : null}
        </div>
      </AppLayout>

      <Dialog open={txFilterOpen} onOpenChange={setTxFilterOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('shell.filter.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('shell.searchTx')}</Label>
              <Input
                placeholder={t('shell.placeholder.keyword')}
                value={txFilterDraft.q}
                onChange={(event) => setTxFilterDraft((prev) => ({ ...prev, q: event.target.value }))}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('shell.txFilter')}</Label>
              <Select
                value={txFilterDraft.txType || 'all'}
                onValueChange={(value) =>
                  setTxFilterDraft((prev) => ({
                    ...prev,
                    txType: value === 'all' ? '' : (value as TxFilter['txType'])
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('shell.filter.all')}</SelectItem>
                  <SelectItem value="expense">{t('enum.txType.expense')}</SelectItem>
                  <SelectItem value="income">{t('enum.txType.income')}</SelectItem>
                  <SelectItem value="transfer">{t('enum.txType.transfer')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('shell.accountFilter')}</Label>
              <Select
                value={txFilterDraft.accountName || '__all__'}
                onValueChange={(value) =>
                  setTxFilterDraft((prev) => ({
                    ...prev,
                    accountName: value === '__all__' ? '' : value,
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">{t('shell.filter.all')}</SelectItem>
                  {txFilterAccountOptions.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => void onResetTxFilter()}>
              {t('shell.filter.reset')}
            </Button>
            <Button onClick={() => void onApplyTxFilter()}>{t('shell.filter.apply')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={attachmentPreview.open}
        onOpenChange={(open) => {
          if (!open && attachmentPreview.objectUrl) {
            URL.revokeObjectURL(attachmentPreview.objectUrl)
            setAttachmentPreview({ open: false, fileName: '', objectUrl: '' })
            return
          }
          setAttachmentPreview((prev) => ({ ...prev, open }))
        }}
      >
        <DialogContent className="max-h-[88vh] max-w-4xl">
          <DialogHeader>
            <DialogTitle>{attachmentPreview.fileName || t('transactions.attachment.preview')}</DialogTitle>
          </DialogHeader>
          <div className="overflow-hidden rounded-md border border-border/70 bg-muted/30 p-2">
            {attachmentPreview.objectUrl ? (
              <img
                alt={attachmentPreview.fileName || 'attachment-preview'}
                className="max-h-[70vh] w-full rounded-md object-contain"
                src={attachmentPreview.objectUrl}
              />
            ) : (
              <div className="py-12 text-center text-sm text-muted-foreground">{t('table.empty')}</div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title={t('dialog.delete.title')}
        description={t('dialog.delete.description')}
        cancelText={t('dialog.cancel')}
        confirmText={t('dialog.delete.confirm')}
        onCancel={() => setPendingDelete(null)}
        onConfirm={onConfirmDelete}
      />

      {/* 创建账本对话框：常驻 root，任何分区 header 的"新建账本"按钮都能唤起。 */}
      <Dialog open={createLedgerDialogOpen} onOpenChange={setCreateLedgerDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('overview.createLedger.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('overview.createLedger.name')}</Label>
              <Input
                value={createLedgerName}
                onChange={(e) => setCreateLedgerName(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('overview.createLedger.currency')}</Label>
              <Input
                value={createCurrency}
                onChange={(e) => setCreateCurrency(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateLedgerDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button onClick={() => void onCreateLedger()}>
              {t('overview.createLedger.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
