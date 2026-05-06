import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate as useNavigateRR, useSearchParams } from 'react-router-dom'

import { routePath, type AppRoute } from '../../state/router'

import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
// AvatarDropdown / ChangelogDialog / LogsDialog / MobileBottomNav 已搬到 AppShell。
// AccountDetailDialog 现仅被 AccountsPage 使用。
// TagDetailDialog 现仅被 TagsPage 使用。
// AdminUsersSection 现仅被 AdminUsersPage 使用。
// BudgetsPanel 在 web-features 包,BudgetsPage 直接消费。
// LedgersSection 现仅被 LedgersPage 使用。
// OverviewSection 现仅被 OverviewPage 使用。
// SettingsHealthSection 现仅被 SettingsHealthPage 使用。
// SettingsProfileAppearanceSection 现仅被 SettingsProfilePage 使用。
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'

import { Download, SlidersHorizontal } from 'lucide-react'

import {
  useToast,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  usePrimaryColor,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tooltip,
  useLocale,
  useT
} from '@beecount/ui'

import {
  ApiError,
  batchAttachmentExists,
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
  deleteLedger,
  createTransaction,
  deleteTransaction,
  downloadWorkspaceTransactionsCsv,
  fetchAdminUsers,
  fetchReadLedgerDetail,
  fetchReadLedgers,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTransaction,
  fetchProfileMe,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  patchProfileMe,
  updateLedgerMeta,
  updateTransaction
} from '@beecount/api-client'

import {
  CategoryPickerDialog,
  ConfirmDialog,
  TagPickerDialog,
  TransactionsPanel,
  canManageLedger,
  canWriteTransactions,
  txDefaults,
  type TxForm
} from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { localizeError } from '../../i18n/errors'
import { dispatchOpenDetailTx } from '../../lib/txDialogEvents'
// AppLayout 已搬到 AppShell。
import type { AppSection } from '../../state/router'

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
  /** 整组可预览附件。单附件时数组长度为 1；多附件时允许 prev/next 切换。 */
  attachments: AttachmentRef[]
  /** 当前显示的附件下标。 */
  currentIndex: number
  /** 当前附件的 blob URL（解码完成才设）。 */
  objectUrl: string
  /** 当前附件的文件名，用于 Dialog 标题。 */
  fileName: string
}

// TransactionsPage 是 /app/transactions 的独立 Page,不再接 legacy 桥 props。
// token / activeLedgerId / onLogout 全走 Auth/Ledgers context,route.section
// 固定为 'transactions'(外层 react-router 已经按 path 分派好了)。

type TxFilter = {
  q: string
  txType: '' | 'expense' | 'income' | 'transfer'
  accountName: string
  /** 金额下限(含),空字符串 = 不限。string 形式存以便绑 input,提交时转 number。 */
  amountMin: string
  /** 金额上限(含)。 */
  amountMax: string
  /** 日期下限(含),格式 YYYY-MM-DD,空 = 不限。 */
  dateFrom: string
  /** 日期上限(含整天)。提交时换成 next-day 00:00 传给 server 的 date_to。 */
  dateTo: string
  /** 分类 syncId 精确过滤。空 = 不限。 */
  categorySyncId: string
  /** 分类显示名(用来在按钮上展示当前选中,UI 维度,不传给 server)。 */
  categoryName: string
  /** 标签 syncId 精确过滤。空 = 不限。 */
  tagSyncId: string
  /** 标签显示名(同上,UI 维度)。 */
  tagName: string
}

const TX_PAGE_SIZE_DEFAULT = 20
// v1 → v2:加了 amount range / date range / category / tag 过滤,key 升版避免
// 旧 storage 数据 partial 回填出空字段。
const TX_FILTER_STORAGE_PREFIX = 'beecount:web:txFilter:v2'

function defaultTxFilter(): TxFilter {
  return {
    q: '',
    txType: '',
    accountName: '',
    amountMin: '',
    amountMax: '',
    dateFrom: '',
    dateTo: '',
    categorySyncId: '',
    categoryName: '',
    tagSyncId: '',
    tagName: '',
  }
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
      accountName: typeof parsed.accountName === 'string' ? parsed.accountName : '',
      amountMin: typeof parsed.amountMin === 'string' ? parsed.amountMin : '',
      amountMax: typeof parsed.amountMax === 'string' ? parsed.amountMax : '',
      dateFrom: typeof parsed.dateFrom === 'string' ? parsed.dateFrom : '',
      dateTo: typeof parsed.dateTo === 'string' ? parsed.dateTo : '',
      categorySyncId: typeof parsed.categorySyncId === 'string' ? parsed.categorySyncId : '',
      categoryName: typeof parsed.categoryName === 'string' ? parsed.categoryName : '',
      tagSyncId: typeof parsed.tagSyncId === 'string' ? parsed.tagSyncId : '',
      tagName: typeof parsed.tagName === 'string' ? parsed.tagName : '',
    }
  } catch {
    return null
  }
}

function sectionNeedsLedger(section: AppSection): boolean {
  // overview / budgets 必须跟账本绑定:切换账本时 refresh effect 会重新拉数据。
  // 其它(transactions/accounts/categories/tags)是跨账本聚合视图,用户切换
  // 账本时跟顶部 dropdown 不强耦合。
  return ['overview', 'budgets'].includes(section)
}

function isListSection(section: AppSection): boolean {
  return ['transactions', 'accounts', 'categories', 'tags'].includes(section)
}

// wsUrl 已搬到 SyncSocketContext。


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

export function TransactionsPage() {
  const _navigate = useNavigateRR()
  // 兼容 legacy 内部代码的 `onNavigate({ kind: 'app', section })` 调用:
  // 转成 react-router 的 push。只在本文件内部用,不外泄。
  const onNavigate = useCallback(
    (next: AppRoute, options?: { replace?: boolean }) => {
      _navigate(routePath(next), { replace: options?.replace })
    },
    [_navigate]
  )
  // route 固定为 transactions(路径已由外层 Route 分派),保留这个字段名
  // 是为了最小化 legacy 函数体内部 route.section 判断的改动。
  const route: Extract<AppRoute, { kind: 'app' }> = useMemo(
    () => ({ kind: 'app', ledgerId: '', section: 'transactions' }),
    []
  )
  const { token, logout: onLogout } = useAuth()
  const t = useT()
  const { locale } = useLocale()
  // mobile 推过来的主题色偏好:本地没 override 时跟随 server。
  // profileMe 订阅:主题色/收支配色 apply 现在由 AppShell 负责;这里仅保留
  // usePrimaryColor 是因为早期代码里有些 WS 事件路径还需要本地短路应用。
  const { applyServerColor: applyServerPrimaryColor } = usePrimaryColor()

  // AppShell 提供的数据 —— AppPage 不再自己 fetch ledgers / profileMe。
  const {
    profileMe,
    sessionUserId,
    isAdmin: isAdminUser,
    isAdminResolved,
    refreshProfile,
  } = useAuth()
  const {
    ledgers,
    activeLedgerId,
    setActiveLedgerId,
    refreshLedgers,
  } = useLedgers()

  const previewRequestSeqRef = useRef(0)
  const txFilterRestoreInProgressRef = useRef(false)
  const txAttachmentPreviewUrlByFileIdRef = useRef<Record<string, string>>({})

  // 原来用 Notice 顶栏显示成功/失败,改成 toast 后不再需要这个 state。
  const [baseChangeId, setBaseChangeId] = useState(0)

  // 列表数据走 PageDataCache(按账本分桶),切走再切回不闪烁。
  // 分页 / filter state 不进 cache —— 它们跟当前页面会话强相关,stale
  // 没有意义(用户期望 re-enter 时回到 page 1 看最新)。
  const txBucket = activeLedgerId || '__none__'
  const [transactions, setTransactions] = usePageCache<ReadTransaction[]>(
    `transactions:${txBucket}:rows`,
    []
  )
  const [txTotal, setTxTotal] = usePageCache<number>(`transactions:${txBucket}:total`, 0)
  const [txPage, setTxPage] = useState(1)
  const [txPageSize, setTxPageSize] = useState(TX_PAGE_SIZE_DEFAULT)
  // accounts / categories / tags 是全局字典,用于 form 下拉选项,跨页面共享一份。
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>('transactions:accounts', [])
  const [categories, setCategories] = usePageCache<ReadCategory[]>('transactions:categories', [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('transactions:tags', [])
  // budgets state 已迁到 BudgetsPage。
  // analyticsData / analyticsIncomeRanks 已迁到 OverviewPage。
  // 首页 Hero 支持 月/年/汇总 三个视角切换：预先一次性把三个 scope 拉回来，
  // 切换视角只在前端改 state，不再发请求。
  // currentMonth / currentYear / allTime 系列 summary + series 已迁到 OverviewPage。
  // 账本级 counts（对齐 mobile `getCountsForLedger`）——首页 Hero "记账笔数 /
  // 记账天数" 的权威来源，跟 analytics scope 没关系。
  // ledgerCounts 已迁到 OverviewPage。
  // 本月支出分类排行（scope=month&metric=expense 的 category_ranks），给
  // HomeMonthCategoryDonut 用。
  // currentMonthCategoryRanks 已迁到 OverviewPage。
  // profileMe / isAdminUser / isAdminResolved 已由 AppShell 通过 AuthContext 注入,
  // 不再在 AppPage 本地维护。profileDisplayName 还留下是因为 display_name 编辑
  // 表单用,后续跟 SettingsProfilePage 一起迁走。
  const [profileDisplayName, setProfileDisplayName] = useState('')
  // adminUsers 全局 state 已消失 —— admin-users / settings-devices 各自 fetch。
  // adminDevices state 已迁到 SettingsDevicesPage。
  // adminOverview / adminHealth state 已迁到 SettingsHealthPage。
  // logsOpen / changelogOpen 已搬到 AppShell。
  const [txDictionaryLoading, setTxDictionaryLoading] = useState(false)
  const [txDictionaryAccounts, setTxDictionaryAccounts] = useState<ReadAccount[]>([])
  const [txDictionaryCategories, setTxDictionaryCategories] = useState<ReadCategory[]>([])
  const [txDictionaryTags, setTxDictionaryTags] = useState<ReadTag[]>([])
  // 标签详情弹窗：点击标签卡片时打开，内部用 TransactionList 无限滚动加载
  // 该标签关联的交易。
  // tagDetail * state + TAG_DETAIL_PAGE_SIZE 已迁到 TagsPage。
  // 账户详情弹窗：点击账户卡片（资产页）时打开。
  // accountDetail * state 已迁到 AccountsPage。

  const [listUserFilter, setListUserFilter] = useState('__all__')
  // 支持 ?q=xxx 深链预填 —— Overview 点 TopCategory 跳过来时带上分类名。
  // 只取一次初值(search param 同步由 App 内部 setListQuery 自己走),避免
  // 每次 listQuery 改都跟 URL 双向拉扯。
  const [searchParams, setSearchParams] = useSearchParams()
  const [listQuery, setListQueryRaw] = useState(() => searchParams.get('q') || '')
  const setListQuery = useCallback(
    (next: string) => {
      setListQueryRaw(next)
    },
    []
  )
  // 用户手动输入 -> 清掉 URL 上的 q 参数,避免刷新后又自动回到旧预填值。
  // 只在 URL 里当前有 q 且跟 state 不一致时触发一次删除。
  useEffect(() => {
    if (searchParams.has('q') && searchParams.get('q') !== listQuery) {
      const next = new URLSearchParams(searchParams)
      next.delete('q')
      setSearchParams(next, { replace: true })
    }
  }, [listQuery, searchParams, setSearchParams])
  const [adminUserStatusFilter, setAdminUserStatusFilter] = useState<'enabled' | 'disabled' | 'all'>('enabled')
  // devicesWindowDays state 已迁到 SettingsDevicesPage。
  // activeLedgerId / setActiveLedgerId 由 AppShell 提供(useLedgers 已在
  // 顶部解构),含 localStorage 持久化 + 跨账本变更自动 reconcile。

  const [txWriteLedgerId, setTxWriteLedgerId] = useState('')

  const [txFilterApplied, setTxFilterApplied] = useState<TxFilter>(defaultTxFilter)
  const [txFilterDraft, setTxFilterDraft] = useState<TxFilter>(defaultTxFilter)
  const [txFilterOpen, setTxFilterOpen] = useState(false)
  // filter dialog 内嵌的 category / tag picker。这两个 dialog 跟 filter dialog
  // 同级渲染(filter dialog z-index 之外),避免嵌套 dialog 导致 portal 抖动。
  const [txFilterCategoryPickerOpen, setTxFilterCategoryPickerOpen] = useState(false)
  const [txFilterTagPickerOpen, setTxFilterTagPickerOpen] = useState(false)

  const [txForm, setTxForm] = useState<TxForm>(txDefaults)
  // tx dialog 显隐 lift 到 page,这样"新建交易"按钮可以跟搜索/筛选放同一
  // 行(panel 自己不再渲染按钮 + dialog state)。编辑流程通过 onEdit 回调
  // 设 form 后 page 这里 setOpen(true)。
  const [txDialogOpen, setTxDialogOpen] = useState(false)
  // CSV 导出 in-flight 标记 — 大账本流式下载 1-3s,期间按钮 disabled + 防重复点击
  const [exportingCsv, setExportingCsv] = useState(false)
  // accountForm state 已迁到 AccountsPage。
  // categoryForm state 已迁到 CategoriesPage。
  // tagForm state 已迁到 TagsPage。
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null)

  // adminCreate* 表单状态已迁到 AdminUsersPage。
  // 分类自定义图标的预览 URL 走全局 AttachmentCache(跟 CategoriesPage /
  // BudgetsPage 共享),避免每次进入交易页都重新拉一遍 → 之前的视觉「闪现」
  // 是因为本地 state 每次进页面都从 {} 起步,所有图标都要再跑一次 fetch。
  const { previewMap: categoryIconPreviewByFileId, ensureLoadedMany: ensureIconsLoaded } =
    useAttachmentCache()
  const [attachmentPreview, setAttachmentPreview] = useState<AttachmentPreviewState>({
    open: false,
    attachments: [],
    currentIndex: 0,
    fileName: '',
    objectUrl: ''
  })

  // Web 不再支持新建账本 —— 账本是 user-global 跨端同步的核心实体,
  // 建账本走 mobile app 更自然(首次 welcome 页就会引导建),避免 web/mobile
  // 双路径对初始化逻辑产生冲突。editLedger 还保留,只改名字和币种不建新账本。
  const [createCurrency] = useState('CNY')
  const [editLedgerName, setEditLedgerName] = useState('')
  const [editCurrency, setEditCurrency] = useState('CNY')

  const selectedLedger = useMemo(
    () => ledgers.find((ledger) => ledger.ledger_id === activeLedgerId) || null,
    [activeLedgerId, ledgers]
  )
  const txFilterPersistKey = useMemo(
    () => txFilterStorageKey(sessionUserId || 'anonymous', activeLedgerId || '__all__'),
    [sessionUserId, activeLedgerId]
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
  // visibleNavGroups 已搬到 AppHeader。
  // headerCoreItems / headerMoreGroups / avatarMenuItems / moreMenuActive
  // 已搬到 AppHeader。visibleNavGroups 目前还没人用到,保留 —— 后续如有
  // 非 header 场景再决定是否删。

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

  // loadLedgers / loadProfile / applyIncomeColorScheme 已移到 AppShell。
  // 这里保留两个 thin adapter,让 AppPage 原有调用点(WS profile push /
  // profileMe patch 成功 / onRefresh)不用大面积改。
  const loadLedgers = async (): Promise<string> => {
    await refreshLedgers()
    // 兼容旧调用者:返回当前 active ledger id(AppShell 已帮忙 reconcile)。
    return activeLedgerId || ''
  }
  const loadProfile = async (): Promise<void> => {
    await refreshProfile()
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

    // Overview / 首页：交易按当前账本筛，账户 / 标签是用户级（所有账本共享同
    // 一套），不跟账本 scope 绑定。
    // overview 的 fetch 已由 OverviewPage 自持。

    if (section === 'transactions') {
      // 交易是账本内的,按 ledger 过滤;账户/分类/标签都是 **用户全局**
      // (Flutter schema:Categories/Accounts/Tags 表都没 ledger_id 字段,一套
      // 跨所有账本共享)—— 拉字典不要按 ledger 过滤,避免多账本下漏数据。
      const txUserId = isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined
      // 把 filter draft / 应用值里 stringy 的字段转成 server 期望的形式:
      //   - amountMin/Max: '' → undefined,'12.5' → 12.5
      //   - dateFrom: 'YYYY-MM-DD' → ISO datetime 当天 00:00 (UTC)
      //   - dateTo:   'YYYY-MM-DD' → ISO datetime **次日** 00:00 (UTC),
      //     server 端用 happened_at < date_to,正好覆盖整个 dateTo 当天
      const minNum = Number(txFilterApplied.amountMin || '')
      const maxNum = Number(txFilterApplied.amountMax || '')
      const dateFromIso = txFilterApplied.dateFrom
        ? new Date(`${txFilterApplied.dateFrom}T00:00:00`).toISOString()
        : undefined
      const dateToIso = txFilterApplied.dateTo
        ? (() => {
            const d = new Date(`${txFilterApplied.dateTo}T00:00:00`)
            d.setDate(d.getDate() + 1)
            return d.toISOString()
          })()
        : undefined

      const [txPageResult, accountRows, categoryRows, tagRows] = await Promise.all([
        fetchWorkspaceTransactions(token, {
          ledgerId: ledgerId || undefined,
          userId: txUserId,
          q: listQuery || undefined,
          txType: txFilterApplied.txType || undefined,
          accountName: txFilterApplied.accountName || undefined,
          categorySyncId: txFilterApplied.categorySyncId || undefined,
          tagSyncId: txFilterApplied.tagSyncId || undefined,
          amountMin: Number.isFinite(minNum) && txFilterApplied.amountMin ? minNum : undefined,
          amountMax: Number.isFinite(maxNum) && txFilterApplied.amountMax ? maxNum : undefined,
          dateFrom: dateFromIso,
          dateTo: dateToIso,
          limit: txPageSize,
          offset: (txPage - 1) * txPageSize
        }),
        fetchWorkspaceAccounts(token, { userId: txUserId, limit: 500 }),
        fetchWorkspaceCategories(token, { userId: txUserId, limit: 500 }),
        fetchWorkspaceTags(token, { userId: txUserId, limit: 500 })
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

    // section === 'admin-users' 已由 AdminUsersPage 自持 fetch,不在 shared
    // refreshSectionData 里处理。

    // accounts / categories / tags 是用户全局的(Flutter schema 里表都没
    // ledger_id),跨账本共享一套。拉列表不要按 ledger 过滤,否则切账本会
    // 看到"分类没了"的假象。
    const userGlobalUserId = isAdminUser && listUserFilter !== '__all__' ? listUserFilter : undefined

    if (section === 'accounts') {
      setAccounts(
        await fetchWorkspaceAccounts(token, {
          userId: userGlobalUserId,
          q: listQuery || undefined,
          limit: 500
        })
      )
      return
    }

    if (section === 'categories') {
      setCategories(
        await fetchWorkspaceCategories(token, {
          userId: userGlobalUserId,
          q: listQuery || undefined,
          limit: 500
        })
      )
      return
    }

    if (section === 'tags') {
      setTags(
        await fetchWorkspaceTags(token, {
          userId: userGlobalUserId,
          q: listQuery || undefined,
          limit: 500
        })
      )
      return
    }

    // budgets 的 fetch 已由 BudgetsPage 自持。

    // settings-devices 的 fetch 已由 SettingsDevicesPage 自持。

    // settings-health 的 fetch 已由 SettingsHealthPage 自持。

    if (section === 'settings-profile') {
      await loadProfile()
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


  // profileMe 初次拉取已由 AppShell 负责,这里只在 isAdmin 解析完后
  // 同步 profileDisplayName(待 SettingsProfilePage 迁走后可连 displayName
  // 一起删掉)。
  useEffect(() => {
    setProfileDisplayName(profileMe?.display_name || '')
  }, [profileMe])

  // admin 用户列表 fetch 已分发到 AdminUsersPage / SettingsDevicesPage 各自的 effect。

  // 非 admin 误入 /app/admin/* 的兜底跳转已由 AppShell 处理,这里保留一份
  // 兼容老 onNavigate 路径的兜底(section 驱动,跟 shell 的路径驱动不重复)。
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

  // WS / polling / drain 全部搬到 AppShell 的 SyncSocketProvider。本页只订阅
  // 数据类事件触发自己的 refresh。profile_change 由 AppShell 自己 refreshProfile,
  // 此处不用订。
  const refreshAllSectionsRef = useRef(refreshAllSections)
  refreshAllSectionsRef.current = refreshAllSections
  useSyncRefresh(() => {
    void refreshAllSectionsRef.current()
  })

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
      ...txFilterApplied,
      q: listQuery,
    }
    window.localStorage.setItem(txFilterPersistKey, JSON.stringify(payload))
  }, [route.section, txFilterPersistKey, listQuery, txFilterApplied])

  // 列表类 section / admin / 设备 / 健康 页的数据加载。合并了"filter 变化
  // 刷新" + "切账本刷新" 两条触发路径 —— 原来分两个 useEffect 都调同一个
  // refreshSectionData,StrictMode 双击下刷新分类页会看到 5 次重复请求。
  // 现在单 effect + loadLedgerBase 串行,deps 汇到一起,一次 render 只发一轮。
  useEffect(() => {
    const section = route.section
    if (
      !isListSection(section) &&
      section !== 'admin-users' &&
      section !== 'settings-devices' &&
      section !== 'settings-health'
    ) {
      return
    }
    // 需要 ledger 的 section(用户还没选/没拉到 ledger 列表时静默等待)
    if (sectionNeedsLedger(section) && !activeLedgerId) return

    let cancelled = false
    const run = async () => {
      try {
        // 切账本 / 进页面时要更新 base_change_id 给写操作用。section 不需要
        // ledger 时(admin-users 等)不调,避免无意义请求。
        if (sectionNeedsLedger(section) && activeLedgerId) {
          await loadLedgerBase(activeLedgerId)
        }
        if (cancelled) return
        await refreshSectionData(activeLedgerId || '', section)
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
    txFilterApplied,
    route.section,
    isAdminUser,
    activeLedgerId,
    txPage,
    txPageSize
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

  // loadTagDetailPage 已迁到 TagsPage。

  // loadAccountDetailPage 已迁到 AccountsPage。

  const onSaveProfileDisplayName = async () => {
    const nextName = profileDisplayName.trim()
    if (!nextName) {
      setErrorNotice(t('profile.error.displayNameRequired'))
      return
    }
    try {
      await patchProfileMe(token, { display_name: nextName })
      await refreshProfile()
      setSuccessNotice(t('notice.profileUpdated'))
      await refreshSectionData(activeLedgerId || '', route.section)
    } catch (err) {
      setErrorNotice(renderError(err))
    }
  }

  const activeTxQuery = listQuery || txFilterApplied.q
  // 任何 filter 字段非空 = 1 个激活点。按钮上小圆点徽章靠它显隐;计数本身
  // 不展示具体数字(够用即可,具体是哪几个看 dialog 里勾选状态)。
  const txFilterActiveCount =
    Number(Boolean(activeTxQuery)) +
    Number(Boolean(txFilterApplied.txType)) +
    Number(Boolean(txFilterApplied.accountName)) +
    Number(Boolean(txFilterApplied.amountMin)) +
    Number(Boolean(txFilterApplied.amountMax)) +
    Number(Boolean(txFilterApplied.dateFrom)) +
    Number(Boolean(txFilterApplied.dateTo)) +
    Number(Boolean(txFilterApplied.categorySyncId)) +
    Number(Boolean(txFilterApplied.tagSyncId))

  const onOpenTxFilter = () => {
    // draft 必须 mirror 全部 applied 字段(原版只搬 q/txType/accountName,
    // 现在加了 amount/date/category/tag,漏一个就会被默认空值覆盖,等于关闭
    // 弹窗就把已应用过滤丢了)。
    setTxFilterDraft({
      ...txFilterApplied,
      q: listQuery || txFilterApplied.q,
    })
    setTxFilterOpen(true)
  }

  const onApplyTxFilter = () => {
    const next = { ...txFilterDraft }
    // 整体替换 applied(不再 spread prev),保证 draft 里清掉的字段也真的清掉。
    setTxFilterApplied(next)
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

  /** 加载指定 index 的附件 blob，复用 txAttachmentPreviewUrlByFileIdRef 缓存，
   *  返回 (fileName, objectUrl) 或 null（下载失败 / 非图片格式）。 */
  const loadAttachmentBlob = async (
    attachment: AttachmentRef
  ): Promise<{ fileName: string; objectUrl: string } | null> => {
    const fileId = attachment.cloudFileId?.trim()
    if (!fileId) return null
    const cached = txAttachmentPreviewUrlByFileIdRef.current[fileId]
    if (cached) {
      return {
        fileName:
          attachment.originalName ||
          attachment.fileName ||
          `attachment-${fileId}`,
        objectUrl: cached
      }
    }
    const response = await downloadAttachment(token, fileId)
    const fileName =
      response.fileName ||
      attachment.originalName ||
      attachment.fileName ||
      `attachment-${fileId}`
    if (!isPreviewableImage(response.mimeType, fileName)) {
      return null
    }
    const blobUrl = URL.createObjectURL(response.blob)
    txAttachmentPreviewUrlByFileIdRef.current[fileId] = blobUrl
    return { fileName, objectUrl: blobUrl }
  }

  /** 切换当前预览索引：异步下载目标附件 blob，更新 state。 */
  const switchPreviewIndex = async (nextIndex: number) => {
    const requestSeq = ++previewRequestSeqRef.current
    const list = attachmentPreview.attachments
    if (list.length === 0) return
    const clamped = ((nextIndex % list.length) + list.length) % list.length
    const target = list[clamped]
    try {
      const loaded = await loadAttachmentBlob(target)
      if (requestSeq !== previewRequestSeqRef.current) return
      if (!loaded) {
        setErrorNotice(t('transactions.attachment.notPreviewable'))
        return
      }
      setAttachmentPreview((prev) => ({
        ...prev,
        currentIndex: clamped,
        fileName: loaded.fileName,
        objectUrl: loaded.objectUrl
      }))
    } catch (err) {
      if (requestSeq !== previewRequestSeqRef.current) return
      setErrorNotice(renderError(err))
    }
  }

  const onPreviewTxAttachment = async (
    attachments: AttachmentRef[],
    startIndex: number
  ) => {
    const requestSeq = ++previewRequestSeqRef.current
    // 只预览有 cloudFileId 的附件（没上传的没法查 blob），index 对齐后的新数组。
    const ready = attachments.filter(
      (a) => typeof a.cloudFileId === 'string' && a.cloudFileId.trim().length > 0
    )
    if (ready.length === 0) {
      setErrorNotice(t('transactions.attachment.metadataOnly'))
      return
    }
    const safeIndex = Math.min(
      Math.max(0, startIndex),
      ready.length - 1
    )
    const target = ready[safeIndex]
    try {
      const loaded = await loadAttachmentBlob(target)
      if (requestSeq !== previewRequestSeqRef.current) return
      if (!loaded) {
        setErrorNotice(t('transactions.attachment.notPreviewable'))
        return
      }
      setAttachmentPreview({
        open: true,
        attachments: ready,
        currentIndex: safeIndex,
        fileName: loaded.fileName,
        objectUrl: loaded.objectUrl
      })
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

  // ensureCategoryIconPreview 已合并到全局 AttachmentCache.ensureLoadedMany 里,
  // 不再每个页面手动维护 inflight 去重。下面这个 noop 只是为了向下兼容
  // 保留旧调用点签名(其中一个 attachment 预览 fallback 还会调到)。
  const ensureCategoryIconPreview = async (fileId: string) => {
    const normalized = fileId.trim()
    if (!normalized) return
    ensureIconsLoaded([normalized])
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
    // 金额必须 > 0 —— mobile addTransaction 也校验,跨端一致防止 0 元交易
    // 污染统计 / 余额。
    const amountNum = Number((txForm.amount || '').toString().trim())
    if (!Number.isFinite(amountNum) || amountNum <= 0) {
      setErrorNotice(t('transactions.error.amountInvalid'))
      return false
    }
    // 非转账交易必须选分类(transfer 自动归虚拟"转账"分类,server 处理)。
    // mobile 端 transaction_editor_page 也强制必选,跨端一致避免 ungrouped tx
    // 污染分类统计。
    if (txForm.tx_type !== 'transfer' && !txForm.category_name.trim()) {
      setErrorNotice(t('transactions.error.categoryRequired'))
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

  // onSaveAccount 已迁到 AccountsPage。onDeleteAccount 走下面的 pendingDelete 链路。

  // onSaveCategory 已迁到 CategoriesPage。

  // onDeleteCategory 已迁到 CategoriesPage(走自己的 ConfirmDialog)。

  // onSaveTag / onDeleteTag 已迁到 TagsPage。

  // admin-users 的所有 CRUD handler 已迁到 AdminUsersPage。

  const onDeleteLedger = async (ledgerId: string) => {
    await deleteLedger(token, ledgerId)
    await refreshCurrent('overview')
    setSuccessNotice(t('notice.ledgerDeleted'))
  }

  const onConfirmDelete = async () => {
    if (!pendingDelete) return
    try {
      if (pendingDelete.kind === 'tx') await onDeleteTransaction(pendingDelete.id, pendingDelete.ledgerId)
      // pendingDelete.kind === 'account' 分支已删 —— AccountsPage 不走 pendingDelete;
      //   web AccountsPanel 根本不暴露 onDelete,按产品约定只能在 mobile 删账户。
      // category 删除已走 CategoriesPage 自带 ConfirmDialog,不再进 AppPage pendingDelete。
      // tag 删除走 TagsPage 自带 ConfirmDialog,不进 AppPage pendingDelete。
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
    // 把当前 categories 里所有 cloud icon fileId 一次性扔给全局 cache 预热。
    // ensureLoadedMany 内部去重 + dedupe inflight,重复调用零开销。
    // 这样切换 section 时图标不会"闪一下" — 切回来时 previewMap 还在。
    const sectionsNeedingIcons: Array<typeof route.section> = [
      'categories',
      'transactions',
      'budgets',
      'overview'
    ]
    if (!sectionsNeedingIcons.includes(route.section)) return
    const ids = categories
      .map((row) => row.icon_cloud_file_id || '')
      .filter((value) => value.trim().length > 0)
    if (ids.length > 0) ensureIconsLoaded(ids)
  }, [route.section, categories, ensureIconsLoaded])

  // 「新建交易」全局事件已交给 GlobalEditDialogs 处理(详情→编辑链 + 新建链
  // 都在 AppShell 顶层,任何页面派发都能接住,不需要先 navigate 到本页)。
  // 本页保留独立 dialog 状态供页面内 「+ 新建交易」按钮使用。

  useEffect(() => {
    return () => {
      Object.values(txAttachmentPreviewUrlByFileIdRef.current).forEach((url) => {
        URL.revokeObjectURL(url)
      })
      txAttachmentPreviewUrlByFileIdRef.current = {}
    }
  }, [])

  // Overview 图表 fetch 已迁到 OverviewPage。


  // tagStatsById 已迁到 TagsPage。

  const showTxFilter = route.section === 'transactions'

  return (
    <>
        <div className="space-y-4 pb-20 md:pb-0">
          {/* overview 已迁出到 OverviewPage */}

          {route.section === 'transactions' ? (
            <div className="space-y-3">
              {/* 交易搜索简化：keyword + 可选 filter 按钮，去掉 Card 包裹与
                  admin 用户选择（admin 场景走单独页，普通用户不需要暴露）。
                  左组 = 搜索输入 + 筛选;右组 = 导出 / 新建,
                  ml-auto 套在右组上(而不是单按钮),即便其中一个 button 隐藏
                  另一个仍会贴右,不会跟左组贴在一起。 */}
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex items-center gap-2">
                  <Input
                    className="h-9 w-[260px] bg-muted lg:w-[360px]"
                    placeholder={t('shell.placeholder.keyword')}
                    value={listQuery}
                    onChange={(event) => setListQuery(event.target.value)}
                  />
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
                <div className="ml-auto flex items-center gap-2">
                {/* 「导出 CSV」按钮 — 跟新建按钮做一组,布局对称。
                    复用当前 txFilterApplied 全部字段(date / type / q / amount /
                    category/tag/account syncId)— 所见即所得。 */}
                {activeLedgerId ? (
                  <Tooltip content={t('export.csv.tooltip')}>
                    <Button
                      variant="outline"
                      className="h-9"
                      disabled={exportingCsv}
                      onClick={async () => {
                        if (!activeLedgerId) return
                        setExportingCsv(true)
                        try {
                          const filter = txFilterApplied
                          // dateTo 是 YYYY-MM-DD 含整天 → 转成"次日 00:00 独占"
                          let dateTo: string | undefined
                          if (filter.dateTo) {
                            const [y, m, d] = filter.dateTo.split('-').map(Number)
                            const next = new Date(y, m - 1, d + 1)
                            dateTo = next.toISOString()
                          }
                          await downloadWorkspaceTransactionsCsv(token, {
                            ledgerId: activeLedgerId,
                            dateFrom: filter.dateFrom
                              ? new Date(filter.dateFrom + 'T00:00:00').toISOString()
                              : undefined,
                            dateTo,
                            txType: filter.txType || undefined,
                            // 头部 search bar (listQuery) 优先,跟列表一致;
                            // filter.q 是 filter modal 里的备用关键词。
                            q: listQuery || filter.q || undefined,
                            accountName: filter.accountName || undefined,
                            categorySyncId: filter.categorySyncId || undefined,
                            tagSyncId: filter.tagSyncId || undefined,
                            amountMin: filter.amountMin
                              ? Number(filter.amountMin)
                              : undefined,
                            amountMax: filter.amountMax
                              ? Number(filter.amountMax)
                              : undefined,
                            lang: locale,
                          })
                          toast.success(t('export.csv.success'))
                        } catch (err) {
                          toast.error(localizeError(err, t))
                        } finally {
                          setExportingCsv(false)
                        }
                      }}
                    >
                      <Download className="mr-1 h-3.5 w-3.5" />
                      {exportingCsv ? t('export.csv.loading') : t('export.csv')}
                    </Button>
                  </Tooltip>
                ) : null}
                {/* "新建交易" — 跟导出 CSV 一组,跟搜索框同一行同高(h-9)。
                    需要有写权限 + writeLedger 候选可用,否则隐藏。 */}
                {canWriteTx && txWriteLedgerOptions.length > 0 ? (
                  <Button
                    className="h-9"
                    onClick={() => {
                      setTxForm(txDefaults())
                      if (
                        activeLedgerId &&
                        txWriteLedgerOptions.some((option) => option.ledger_id === activeLedgerId)
                      ) {
                        setTxWriteLedgerId(activeLedgerId)
                      } else {
                        setTxWriteLedgerId(txWriteLedgerOptions[0]?.ledger_id || '')
                      }
                      setTxDialogOpen(true)
                    }}
                  >
                    {t('transactions.button.create')}
                  </Button>
                ) : null}
                </div>
              </div>
              <TransactionsPanel
                form={txForm}
                rows={transactions}
                total={txTotal}
                page={txPage}
                pageSize={txPageSize}
                accounts={txWriteAccounts}
                categories={txWriteCategories}
                iconPreviewUrlByFileId={categoryIconPreviewByFileId}
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
                onFormChange={setTxForm}
                dialogOpen={txDialogOpen}
                onDialogOpenChange={setTxDialogOpen}
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
                  setTxDialogOpen(true)
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
                    ledgerId: row.ledger_id || txWriteLedgerId || activeLedgerId || ''
                  })
                }
                onSelect={(row) => {
                  // 派发全局 detail 事件,弹窗由 GlobalEntityDialogs 渲染
                  dispatchOpenDetailTx(row as WorkspaceTransaction)
                }}
              />
            </div>
          ) : null}

          {/* accounts 已迁出到 AccountsPage */}

          {/* categories 已迁出到 CategoriesPage */}

          {/* tags 已迁出到 TagsPage */}

          {/* budgets 已迁出到 BudgetsPage */}

          {/* ledgers 已迁出到 LedgersPage */}

          {/* settings-ai 已迁出到 SettingsAiPage(react-router) */}

          {/* settings-devices 已迁出到 SettingsDevicesPage */}

          {/* settings-profile / settings-appearance 已迁出到 SettingsProfilePage */}

          {/* settings-health 已迁出到 SettingsHealthPage */}

          {/* admin-users 已迁出到 AdminUsersPage */}
        </div>

      {/* TagDetailDialog 已跟 tags section 一起迁到 TagsPage */}

      {/* AccountDetailDialog 已跟 accounts section 一起迁到 AccountsPage */}

      <Dialog open={txFilterOpen} onOpenChange={setTxFilterOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('shell.filter.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 max-h-[70vh] overflow-y-auto">
            <div className="space-y-1">
              <Label>{t('shell.searchTx')}</Label>
              <Input
                placeholder={t('shell.placeholder.keyword')}
                value={txFilterDraft.q}
                onChange={(event) => setTxFilterDraft((prev) => ({ ...prev, q: event.target.value }))}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
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

            {/* 日期范围 — date 输入,跟 mobile search_page 等价(start/end)。
                后端用半开区间 happened_at < dateTo,这里 dateTo 自动 +1 天。 */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>{t('shell.filter.dateFrom')}</Label>
                <Input
                  type="date"
                  value={txFilterDraft.dateFrom}
                  onChange={(event) =>
                    setTxFilterDraft((prev) => ({ ...prev, dateFrom: event.target.value }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>{t('shell.filter.dateTo')}</Label>
                <Input
                  type="date"
                  value={txFilterDraft.dateTo}
                  onChange={(event) =>
                    setTxFilterDraft((prev) => ({ ...prev, dateTo: event.target.value }))
                  }
                />
              </div>
            </div>

            {/* 金额范围 — number,允许小数 */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>{t('shell.filter.amountMin')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0"
                  value={txFilterDraft.amountMin}
                  onChange={(event) =>
                    setTxFilterDraft((prev) => ({ ...prev, amountMin: event.target.value }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>{t('shell.filter.amountMax')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="∞"
                  value={txFilterDraft.amountMax}
                  onChange={(event) =>
                    setTxFilterDraft((prev) => ({ ...prev, amountMax: event.target.value }))
                  }
                />
              </div>
            </div>

            {/* 分类 + 标签 — 简单显示选中名,点 trigger 弹 picker dialog。空 =
                不限。提供"清除"按钮一键解绑。 */}
            <div className="space-y-1">
              <Label>{t('shell.filter.category')}</Label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setTxFilterCategoryPickerOpen(true)}
                  className="flex h-10 flex-1 items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                >
                  <span className={`flex-1 truncate ${
                    txFilterDraft.categoryName ? '' : 'text-muted-foreground'
                  }`}>
                    {txFilterDraft.categoryName || t('shell.filter.all')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
                {txFilterDraft.categorySyncId ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setTxFilterDraft((prev) => ({
                        ...prev,
                        categorySyncId: '',
                        categoryName: '',
                      }))
                    }
                  >
                    {t('common.remove')}
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('shell.filter.tag')}</Label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setTxFilterTagPickerOpen(true)}
                  className="flex h-10 flex-1 items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                >
                  <span className={`flex-1 truncate ${
                    txFilterDraft.tagName ? '' : 'text-muted-foreground'
                  }`}>
                    {txFilterDraft.tagName || t('shell.filter.all')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
                {txFilterDraft.tagSyncId ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setTxFilterDraft((prev) => ({
                        ...prev,
                        tagSyncId: '',
                        tagName: '',
                      }))
                    }
                  >
                    {t('common.remove')}
                  </Button>
                ) : null}
              </div>
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

      {/* Filter dialog 内的"分类 / 标签 picker" — 复用统一组件,跟 transaction
          表单的选择器一致。category picker 不显示父级 0 笔限制(filter 是查询
          需求,任何已存在分类都能筛)。 */}
      <CategoryPickerDialog
        open={txFilterCategoryPickerOpen}
        onClose={() => setTxFilterCategoryPickerOpen(false)}
        kind={txFilterDraft.txType === 'income' ? 'income' : 'expense'}
        // ReadCategory shape ≈ WorkspaceCategory(后者只多 ledger_id/name/tx_count
        // 等可选字段),CategoryPicker 只用其中的 syncId/name/icon/parent_id 字段,
        // 行为兼容,这里强转避免上层换 fetch 接口。
        rows={txWriteCategories as unknown as WorkspaceCategory[]}
        iconPreviewUrlByFileId={categoryIconPreviewByFileId}
        selectedId={txFilterDraft.categorySyncId || undefined}
        title={t('shell.filter.category')}
        onSelect={(cat) =>
          setTxFilterDraft((prev) => ({
            ...prev,
            categorySyncId: cat.id,
            categoryName: cat.name,
          }))
        }
      />
      <TagPickerDialog
        open={txFilterTagPickerOpen}
        onClose={() => setTxFilterTagPickerOpen(false)}
        tags={txWriteTags}
        // filter 只支持单 tag(server tag_sync_id 是单值参数);转成单选语义:
        // selectedNames 只放当前选中的那一个。
        selectedNames={txFilterDraft.tagName ? [txFilterDraft.tagName] : []}
        onChange={(names) => {
          // 用户在 multi-select picker 里勾任意一个,我们当作"切换到这个"。
          // 取数组里最后一个非空的当作单选结果(用户最近的勾选意图)。
          const last = names.length > 0 ? names[names.length - 1] : ''
          if (!last) {
            setTxFilterDraft((prev) => ({ ...prev, tagSyncId: '', tagName: '' }))
            return
          }
          const tagRow = txWriteTags.find(
            (row) => (row.name || '').trim().toLowerCase() === last.trim().toLowerCase(),
          )
          setTxFilterDraft((prev) => ({
            ...prev,
            tagSyncId: tagRow?.id || '',
            tagName: tagRow?.name || last,
          }))
        }}
        title={t('shell.filter.tag')}
        onClearAll={() =>
          setTxFilterDraft((prev) => ({ ...prev, tagSyncId: '', tagName: '' }))
        }
      />

      <Dialog
        open={attachmentPreview.open}
        onOpenChange={(open) => {
          if (!open) {
            // 关闭时不在这里 revokeObjectURL —— blob URL 存在
            // txAttachmentPreviewUrlByFileIdRef 里，组件 unmount 时统一清理；
            // 否则下次预览同一附件会拿到 revoked 的 URL 加载失败。
            setAttachmentPreview({
              open: false,
              attachments: [],
              currentIndex: 0,
              fileName: '',
              objectUrl: ''
            })
            return
          }
          setAttachmentPreview((prev) => ({ ...prev, open }))
        }}
      >
        <DialogContent className="max-h-[88vh] max-w-4xl">
          <DialogHeader>
            <DialogTitle>
              {attachmentPreview.fileName || t('transactions.attachment.preview')}
              {attachmentPreview.attachments.length > 1 ? (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {attachmentPreview.currentIndex + 1} / {attachmentPreview.attachments.length}
                </span>
              ) : null}
            </DialogTitle>
          </DialogHeader>
          <div className="relative overflow-hidden rounded-md border border-border/70 bg-muted/30 p-2">
            {attachmentPreview.objectUrl ? (
              <img
                alt={attachmentPreview.fileName || 'attachment-preview'}
                className="max-h-[70vh] w-full rounded-md object-contain"
                src={attachmentPreview.objectUrl}
              />
            ) : (
              <div className="py-12 text-center text-sm text-muted-foreground">{t('table.empty')}</div>
            )}
            {attachmentPreview.attachments.length > 1 ? (
              <>
                <button
                  type="button"
                  aria-label={t('transactions.attachment.prev')}
                  className="absolute left-2 top-1/2 h-9 w-9 -translate-y-1/2 rounded-full border border-border bg-background/90 text-lg shadow hover:bg-background"
                  onClick={() =>
                    void switchPreviewIndex(attachmentPreview.currentIndex - 1)
                  }
                >
                  ‹
                </button>
                <button
                  type="button"
                  aria-label={t('transactions.attachment.next')}
                  className="absolute right-2 top-1/2 h-9 w-9 -translate-y-1/2 rounded-full border border-border bg-background/90 text-lg shadow hover:bg-background"
                  onClick={() =>
                    void switchPreviewIndex(attachmentPreview.currentIndex + 1)
                  }
                >
                  ›
                </button>
              </>
            ) : null}
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

      {/* TransactionDetailDialog 已迁到 GlobalEntityDialogs(AppShell 层)
          Edit 链路通过 dispatchOpenEditTx 事件由本页 useEffect 接管,
          不需要在这里渲染弹窗 */}

      {/* LogsDialog / ChangelogDialog / MobileBottomNav / AppLayout / header 全部迁到 AppShell。 */}
    </>
  )
}
