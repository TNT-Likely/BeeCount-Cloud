import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { LogOut, MoreHorizontal, RefreshCw, SlidersHorizontal } from 'lucide-react'

import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
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
  type ProfileMe,
  type AdminDevice,
  type AdminHealth,
  type AdminOverview,
  type UserAdmin,
  createWorkspaceAccount,
  createAdminUser,
  createWorkspaceCategory,
  createLedger,
  createWorkspaceTag,
  createTransaction,
  deleteWorkspaceAccount,
  deleteWorkspaceCategory,
  deleteWorkspaceTag,
  deleteTransaction,
  fetchAdminDevices,
  fetchAdminHealth,
  fetchAdminOverview,
  fetchAdminUsers,
  fetchReadLedgerDetail,
  fetchReadLedgers,
  fetchProfileMe,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  patchProfileMe,
  patchAdminUser,
  updateWorkspaceAccount,
  updateWorkspaceCategory,
  updateLedgerMeta,
  updateWorkspaceTag,
  updateTransaction
} from '@beecount/api-client'

import {
  AccountsPanel,
  AdminUsersPanel,
  CategoriesPanel,
  ConfirmDialog,
  LedgerOverviewPanel,
  NAV_GROUPS,
  OpsDevicesPanel,
  StatusBadge,
  TagsPanel,
  TransactionsPanel,
  accountDefaults,
  canManageLedger,
  canWriteTransactions,
  categoryDefaults,
  formatIsoDateTime,
  formatLedgerLabel,
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

  const [notice, setNotice] = useState<Notice>(null)
  const [baseChangeId, setBaseChangeId] = useState(0)

  const [ledgers, setLedgers] = useState<ReadLedger[]>([])
  const [transactions, setTransactions] = useState<ReadTransaction[]>([])
  const [txTotal, setTxTotal] = useState(0)
  const [txPage, setTxPage] = useState(1)
  const [txPageSize, setTxPageSize] = useState(TX_PAGE_SIZE_DEFAULT)
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [categories, setCategories] = useState<ReadCategory[]>([])
  const [tags, setTags] = useState<ReadTag[]>([])
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

  const [listLedgerFilter, setListLedgerFilter] = useState('__all__')
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
    () => txFilterStorageKey(sessionUserId || 'anonymous', listLedgerFilter),
    [sessionUserId, listLedgerFilter]
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
  const txWriteAccounts = txDictionaryAccounts
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

  const setErrorNotice = (message: string) =>
    setNotice({
      type: 'destructive',
      title: t('notice.failed'),
      message
    })

  const setSuccessNotice = (message: string) =>
    setNotice({
      type: 'default',
      title: t('notice.success'),
      message
    })

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

    if (section === 'transactions') {
      const [txPageResult, accountRows, categoryRows, tagRows] = await Promise.all([
        fetchWorkspaceTransactions(token, {
          ledgerId: listLedgerFilter === '__all__' ? undefined : listLedgerFilter,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          q: listQuery || undefined,
          txType: txFilterApplied.txType || undefined,
          accountName: txFilterApplied.accountName || undefined,
          limit: txPageSize,
          offset: (txPage - 1) * txPageSize
        }),
        fetchWorkspaceAccounts(token, {
          ledgerId: listLedgerFilter === '__all__' ? undefined : listLedgerFilter,
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          limit: 500
        }),
        fetchWorkspaceCategories(token, {
          userId: isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined,
          limit: 500
        }),
        fetchWorkspaceTags(token, {
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

  useEffect(() => {
    if (!token) return
    const socket = new WebSocket(wsUrl(token))
    socket.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data) as any
        if (payload?.type === 'sync_change' || payload?.type === 'backup_restore') {
          await refreshCurrent()
        }
      } catch (err) {
        if (err instanceof SyntaxError) return
        handleTopLevelLoadError(err)
      }
    }
    return () => socket.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, route.section, activeLedgerId])

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
    listLedgerFilter,
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
  }, [listLedgerFilter, listUserFilter, listQuery, txFilterApplied.txType, txFilterApplied.accountName, route.section])

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
    if (listLedgerFilter !== '__all__' && allowedIds.has(listLedgerFilter)) {
      setTxWriteLedgerId(listLedgerFilter)
      return
    }
    setTxWriteLedgerId(txWriteLedgerOptions[0]?.ledger_id || '')
  }, [txWriteLedgerId, txWriteLedgerOptions, listLedgerFilter])

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
      const response = await updateLedgerMeta(token, activeLedgerId, baseChangeId, {
        ledger_name: editLedgerName,
        currency: editCurrency
      })
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
      if (!txForm.from_account_name.trim() || !txForm.to_account_name.trim()) {
        setErrorNotice(t('transactions.error.transferAccountsRequired'))
        return false
      }
      if (txForm.from_account_name.trim() === txForm.to_account_name.trim()) {
        setErrorNotice(t('transactions.error.transferAccountsDifferent'))
        return false
      }
    } else if (!txForm.account_name.trim()) {
      setErrorNotice(t('transactions.error.accountRequired'))
      return false
    }

    try {
      const baseChangeIdForLedger = await fetchBaseChangeId(ledgerId)
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
      const res = txForm.editingId
        ? await updateTransaction(token, ledgerId, txForm.editingId, baseChangeIdForLedger, payload)
        : await createTransaction(token, ledgerId, baseChangeIdForLedger, payload)
      if (activeLedgerId === ledgerId) {
        setBaseChangeId(res.new_change_id)
      }
      setTxForm(txDefaults())
      await refreshSectionData(activeLedgerId || ledgerId, 'transactions')
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
    const baseChangeIdForLedger = await fetchBaseChangeId(ledgerId)
    const res = await deleteTransaction(token, ledgerId, txId, baseChangeIdForLedger)
    if (activeLedgerId === ledgerId) {
      setBaseChangeId(res.new_change_id)
    }
    await refreshSectionData(activeLedgerId || ledgerId, 'transactions')
    setSuccessNotice(t('notice.txDeleted'))
  }

  const onSaveAccount = async (): Promise<boolean> => {
    try {
      const payload = {
        name: accountForm.name,
        account_type: accountForm.account_type || null,
        currency: accountForm.currency || null,
        initial_balance: Number(accountForm.initial_balance || 0)
      }
      const targetUserId = resolveWorkspaceTargetUserId(accountForm.editingOwnerUserId)
      if (accountForm.editingId) {
        await updateWorkspaceAccount(token, accountForm.editingId, payload)
      } else {
        await createWorkspaceAccount(token, payload, isAdminUser ? targetUserId : undefined)
      }
      setAccountForm(accountDefaults())
      await refreshSectionData(activeLedgerId, 'accounts')
      setSuccessNotice(accountForm.editingId ? t('notice.accountUpdated') : t('notice.accountCreated'))
      return true
    } catch (err) {
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteAccount = async (accountId: string) => {
    await deleteWorkspaceAccount(token, accountId)
    await refreshSectionData(activeLedgerId, 'accounts')
    setSuccessNotice(t('notice.accountDeleted'))
  }

  const onSaveCategory = async (): Promise<boolean> => {
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
      const targetUserId = resolveWorkspaceTargetUserId(categoryForm.editingOwnerUserId)
      if (categoryForm.editingId) {
        await updateWorkspaceCategory(token, categoryForm.editingId, payload)
      } else {
        await createWorkspaceCategory(token, payload, isAdminUser ? targetUserId : undefined)
      }
      setCategoryForm(categoryDefaults())
      await refreshSectionData(activeLedgerId, 'categories')
      setSuccessNotice(categoryForm.editingId ? t('notice.categoryUpdated') : t('notice.categoryCreated'))
      return true
    } catch (err) {
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteCategory = async (categoryId: string) => {
    await deleteWorkspaceCategory(token, categoryId)
    await refreshSectionData(activeLedgerId, 'categories')
    setSuccessNotice(t('notice.categoryDeleted'))
  }

  const onSaveTag = async (): Promise<boolean> => {
    try {
      const payload = {
        name: tagForm.name,
        color: tagForm.color || null
      }
      const targetUserId = resolveWorkspaceTargetUserId(tagForm.editingOwnerUserId)
      if (tagForm.editingId) {
        await updateWorkspaceTag(token, tagForm.editingId, payload)
      } else {
        await createWorkspaceTag(token, payload, isAdminUser ? targetUserId : undefined)
      }
      setTagForm(tagDefaults())
      await refreshSectionData(activeLedgerId, 'tags')
      setSuccessNotice(tagForm.editingId ? t('notice.tagUpdated') : t('notice.tagCreated'))
      return true
    } catch (err) {
      setErrorNotice(renderError(err))
      return false
    }
  }

  const onDeleteTag = async (tagId: string) => {
    await deleteWorkspaceTag(token, tagId)
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

  const onConfirmDelete = async () => {
    if (!pendingDelete) return
    try {
      if (pendingDelete.kind === 'tx') await onDeleteTransaction(pendingDelete.id, pendingDelete.ledgerId)
      if (pendingDelete.kind === 'account') await onDeleteAccount(pendingDelete.id)
      if (pendingDelete.kind === 'category') await onDeleteCategory(pendingDelete.id)
      if (pendingDelete.kind === 'tag') await onDeleteTag(pendingDelete.id)
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

                <div className="flex items-center gap-1.5 rounded-2xl border border-border/40 bg-accent/20 px-1.5 py-1">
                  <Tooltip content={t('shell.refresh')}>
                    <Button
                      aria-label={t('shell.refresh')}
                      className="h-9 w-9 bg-transparent"
                      size="icon"
                      variant="ghost"
                      onClick={onRefresh}
                    >
                      <RefreshCw className="h-4 w-4" />
                    </Button>
                  </Tooltip>
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
          {notice ? (
            <Alert variant={notice.type}>
              <AlertTitle>{notice.title}</AlertTitle>
              <AlertDescription>{notice.message}</AlertDescription>
            </Alert>
          ) : null}

          {sectionNeedsLedger(route.section) ? (
            <Card className="bc-panel">
              <CardContent className="pt-4">
                <div className="bc-toolbar flex flex-wrap items-center gap-3">
                  <Label className="text-xs uppercase tracking-wide text-muted-foreground">{t('shell.ledger')}</Label>
                  <Select
                    value={activeLedgerId || undefined}
                    onValueChange={setActiveLedgerId}
                    disabled={ledgers.length === 0}
                  >
                    <SelectTrigger className="w-[320px] max-w-full">
                      <SelectValue placeholder={t('shell.ledger')} />
                    </SelectTrigger>
                    <SelectContent>
                      {ledgers.map((ledger) => (
                        <SelectItem key={ledger.ledger_id} value={ledger.ledger_id}>
                          {formatLedgerLabel(ledger, t(`enum.role.${ledger.role}`))}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedLedger ? <StatusBadge value={selectedLedger.role} /> : null}
                </div>
              </CardContent>
            </Card>
          ) : null}

          {route.section === 'overview' ? (
            <>
              <LedgerOverviewPanel
                ledgers={ledgers}
                selectedLedger={selectedLedger}
                canManageMeta={Boolean(canManageSelectedLedger)}
                createDialogOpen={createLedgerDialogOpen}
                createLedgerName={createLedgerName}
                createCurrency={createCurrency}
                editLedgerName={editLedgerName}
                editCurrency={editCurrency}
                onCreateDialogOpenChange={setCreateLedgerDialogOpen}
                onCreateLedgerNameChange={setCreateLedgerName}
                onCreateCurrencyChange={setCreateCurrency}
                onEditLedgerNameChange={setEditLedgerName}
                onEditCurrencyChange={setEditCurrency}
                onCreateLedger={onCreateLedger}
                onUpdateLedgerMeta={onUpdateLedgerMeta}
              />
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('ledgers.title')}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {ledgers.length === 0 ? <p className="text-sm text-muted-foreground">{t('overview.empty')}</p> : null}
                  {ledgers.map((ledger) => (
                    <div
                      key={ledger.ledger_id}
                      className="flex items-center justify-between rounded-md border border-border px-3 py-2"
                    >
                      <div className="space-y-1">
                        <p className="text-sm font-medium">{ledger.ledger_name}</p>
                        <p className="text-xs text-muted-foreground">{ledger.ledger_id}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusBadge value={ledger.role} />
                        <Badge variant="secondary">{ledger.currency}</Badge>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </>
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
                    <Select value={listLedgerFilter} onValueChange={setListLedgerFilter}>
                      <SelectTrigger className="h-9 w-[240px] bg-muted shadow-sm">
                        <SelectValue placeholder={t('shell.ledgerFilter')} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">{t('shell.allLedgers')}</SelectItem>
                        {ledgers.map((ledger) => (
                          <SelectItem key={ledger.ledger_id} value={ledger.ledger_id}>
                            {formatLedgerLabel(ledger, t(`enum.role.${ledger.role}`))}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
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
                    listLedgerFilter !== '__all__' &&
                    txWriteLedgerOptions.some((option) => option.ledger_id === listLedgerFilter)
                  ) {
                    setTxWriteLedgerId(listLedgerFilter)
                    return
                  }
                  setTxWriteLedgerId(txWriteLedgerOptions[0]?.ledger_id || '')
                }}
                onReload={onRefresh}
                onUploadAttachments={onUploadTxAttachments}
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
                onDelete={(row) =>
                  setPendingDelete({
                    kind: 'account',
                    id: row.id
                  })
                }
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
                onDelete={(row) =>
                  setPendingDelete({
                    kind: 'category',
                    id: row.id
                  })
                }
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
                onDelete={(row) =>
                  setPendingDelete({
                    kind: 'tag',
                    id: row.id
                  })
                }
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
    </>
  )
}
