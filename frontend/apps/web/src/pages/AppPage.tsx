import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getStoredDeviceId, getStoredUserId } from '@beecount/api-client'

import { useSyncSocket } from '../hooks/useSyncSocket'
import { jwtUserId } from '../state/jwt'
import { drainPull, startPoller } from '../state/sync-client'
import { LogsDialog } from '../components/LogsDialog'
import { MobileBottomNav } from '../components/MobileBottomNav'
import { HomeHero } from '../components/dashboard/HomeHero'
import { HomeHabitStats } from '../components/dashboard/HomeHabitStats'
import { HomeYearHeatmap } from '../components/dashboard/HomeYearHeatmap'
import { HomeMonthCategoryDonut } from '../components/dashboard/HomeMonthCategoryDonut'
import { HomeTopTags } from '../components/dashboard/HomeTopTags'
import { HomeTopAccounts } from '../components/dashboard/HomeTopAccounts'
import { AssetCompositionDonut } from '../components/dashboard/AssetCompositionDonut'
import { MonthlyTrendBars } from '../components/dashboard/MonthlyTrendBars'
import { TopCategoriesList } from '../components/dashboard/TopCategoriesList'

import { LogOut, MoreHorizontal, ScrollText, SlidersHorizontal } from 'lucide-react'

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
  PrimaryColorPicker,
  usePrimaryColor,
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
  changeAdminUserPassword,
  deleteAdminUser,
  downloadAttachment,
  uploadAttachment,
  type AttachmentRef,
  type ReadAccount,
  type ReadBudget,
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
  fetchWorkspaceLedgerCounts,
  type WorkspaceAccount,
  type WorkspaceAnalytics,
  type WorkspaceLedgerCounts,
  type WorkspaceTransaction,
  fetchProfileMe,
  fetchReadBudgets,
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
  TransactionList,
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
  /** 整组可预览附件。单附件时数组长度为 1；多附件时允许 prev/next 切换。 */
  attachments: AttachmentRef[]
  /** 当前显示的附件下标。 */
  currentIndex: number
  /** 当前附件的 blob URL（解码完成才设）。 */
  objectUrl: string
  /** 当前附件的文件名，用于 Dialog 标题。 */
  fileName: string
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

/** 脱敏 API key:前 4 后 4,中间用 ••• 掩掉。空 / 短串返回 null,让 caller 走 i18n。 */
function maskApiKey(key: unknown): string | null {
  if (typeof key !== 'string' || key.trim().length === 0) return null
  const s = key.trim()
  if (s.length <= 8) return '•'.repeat(s.length)
  return `${s.slice(0, 4)}•••${s.slice(-4)}`
}

/** AI 配置的只读展示 —— 提供商数组 + 能力绑定 + 其它开关/自定义提示词。
 *  全部从 profileMe.ai_config 的 dict 结构里读。移动端的 snapshotForSync()
 *  定义了字段约定:providers / binding / custom_prompt / strategy /
 *  bill_extraction_enabled / use_vision。 */
function AIConfigReadOnly({ config }: { config: Record<string, any> }) {
  const t = useT()
  const providers = Array.isArray(config.providers) ? config.providers : []
  const binding =
    typeof config.binding === 'object' && config.binding !== null
      ? (config.binding as Record<string, any>)
      : {}
  const providerNameById = new Map<string, string>()
  for (const p of providers) {
    if (p && typeof p === 'object' && typeof p.id === 'string') {
      providerNameById.set(p.id, typeof p.name === 'string' ? p.name : p.id)
    }
  }

  const capability = [
    { key: 'textProviderId', label: t('ai.binding.text') },
    { key: 'visionProviderId', label: t('ai.binding.vision') },
    { key: 'speechProviderId', label: t('ai.binding.speech') }
  ]

  const onOff = (v: unknown) =>
    v === true ? t('common.on') : v === false ? t('common.off') : t('common.dash')

  return (
    <div className="space-y-4">
      {/* 顶栏:策略 / 开关 */}
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('ai.strategy.label')}</p>
          <p className="mt-1 text-sm font-medium">
            {config.strategy || t('common.dash')}
          </p>
        </div>
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('ai.billExtraction.label')}</p>
          <p className="mt-1 text-sm font-medium">{onOff(config.bill_extraction_enabled)}</p>
        </div>
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('ai.useVision.label')}</p>
          <p className="mt-1 text-sm font-medium">{onOff(config.use_vision)}</p>
        </div>
      </div>

      {/* 能力绑定 */}
      <div>
        <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('ai.binding.title')}
        </p>
        <div className="space-y-1.5">
          {capability.map((cap) => {
            const providerId = binding[cap.key] as string | undefined
            const name = providerId
              ? providerNameById.get(providerId) || providerId
              : t('common.dash')
            return (
              <div
                key={cap.key}
                className="flex items-center justify-between rounded-md border border-border/60 bg-muted/10 px-3 py-1.5"
              >
                <span className="text-sm">{cap.label}</span>
                <span className="text-sm text-muted-foreground">{name}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* 服务商列表 */}
      <div>
        <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('ai.providers.title')} ({providers.length})
        </p>
        <div className="space-y-2">
          {providers.map((p: any, idx: number) => {
            if (!p || typeof p !== 'object') return null
            const name = typeof p.name === 'string' ? p.name : t('ai.providers.unnamed')
            const apiKeyMasked = maskApiKey(p.apiKey)
            return (
              <div
                key={typeof p.id === 'string' ? p.id : idx}
                className="rounded-lg border border-border/60 bg-muted/10 px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{name}</span>
                  {p.isBuiltIn ? (
                    <span className="rounded-full border border-border/60 bg-card px-2 py-0.5 text-[10px] uppercase tracking-wider">
                      {t('ai.providers.badge.builtin')}
                    </span>
                  ) : null}
                </div>
                <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
                  <span className="text-muted-foreground">{t('ai.providers.field.apiKey')}</span>
                  <span className="font-mono">{apiKeyMasked || t('common.unset')}</span>
                  {p.baseUrl ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.baseUrl')}</span>
                      <span className="truncate font-mono">{String(p.baseUrl)}</span>
                    </>
                  ) : null}
                  {p.textModel ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.textModel')}</span>
                      <span>{String(p.textModel)}</span>
                    </>
                  ) : null}
                  {p.visionModel ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.visionModel')}</span>
                      <span>{String(p.visionModel)}</span>
                    </>
                  ) : null}
                  {p.audioModel ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.audioModel')}</span>
                      <span>{String(p.audioModel)}</span>
                    </>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* 自定义提示词 */}
      {typeof config.custom_prompt === 'string' && config.custom_prompt.trim().length > 0 ? (
        <div>
          <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('ai.customPrompt.title')}
          </p>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-[12px]">
            {config.custom_prompt}
          </pre>
        </div>
      ) : null}
    </div>
  )
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
  // overview / budgets 必须跟账本绑定:切换账本时 refresh effect 会重新拉数据。
  // 其它(transactions/accounts/categories/tags)是跨账本聚合视图,用户切换
  // 账本时跟顶部 dropdown 不强耦合。
  return ['overview', 'budgets'].includes(section)
}

function isListSection(section: AppSection): boolean {
  return ['transactions', 'accounts', 'categories', 'tags'].includes(section)
}

function wsUrl(token: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.port === '5173' ? `${window.location.hostname}:8080` : window.location.host
  return `${protocol}://${host}/ws?token=${encodeURIComponent(token)}`
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
  // mobile 推过来的主题色偏好：本地没 override 时跟随 server。
  const { applyServerColor: applyServerPrimaryColor } = usePrimaryColor()
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
  const [budgets, setBudgets] = useState<ReadBudget[]>([])
  const [analyticsData, setAnalyticsData] = useState<WorkspaceAnalytics | null>(null)
  const [analyticsIncomeRanks, setAnalyticsIncomeRanks] = useState<WorkspaceAnalytics['category_ranks']>([])
  // 首页 Hero 支持 月/年/汇总 三个视角切换：预先一次性把三个 scope 拉回来，
  // 切换视角只在前端改 state，不再发请求。
  const [currentMonthSummary, setCurrentMonthSummary] =
    useState<WorkspaceAnalytics['summary'] | null>(null)
  const [currentMonthSeries, setCurrentMonthSeries] = useState<
    WorkspaceAnalytics['series']
  >([])
  const [currentYearSummary, setCurrentYearSummary] =
    useState<WorkspaceAnalytics['summary'] | null>(null)
  const [currentYearSeries, setCurrentYearSeries] = useState<
    WorkspaceAnalytics['series']
  >([])
  const [allTimeSummary, setAllTimeSummary] =
    useState<WorkspaceAnalytics['summary'] | null>(null)
  const [allTimeSeries, setAllTimeSeries] = useState<
    WorkspaceAnalytics['series']
  >([])
  // 账本级 counts（对齐 mobile `getCountsForLedger`）——首页 Hero "记账笔数 /
  // 记账天数" 的权威来源，跟 analytics scope 没关系。
  const [ledgerCounts, setLedgerCounts] = useState<WorkspaceLedgerCounts | null>(null)
  // 本月支出分类排行（scope=month&metric=expense 的 category_ranks），给
  // HomeMonthCategoryDonut 用。
  const [currentMonthCategoryRanks, setCurrentMonthCategoryRanks] = useState<
    WorkspaceAnalytics['category_ranks']
  >([])
  const [profileMe, setProfileMe] = useState<ProfileMe | null>(null)
  const [profileDisplayName, setProfileDisplayName] = useState('')
  const [adminUsers, setAdminUsers] = useState<UserAdmin[]>([])
  const [adminDevices, setAdminDevices] = useState<AdminDevice[]>([])
  const [adminOverview, setAdminOverview] = useState<AdminOverview | null>(null)
  const [adminHealth, setAdminHealth] = useState<AdminHealth | null>(null)
  const [isAdminUser, setIsAdminUser] = useState(false)
  const [isAdminResolved, setIsAdminResolved] = useState(false)
  const [logsOpen, setLogsOpen] = useState(false)
  const [txDictionaryLoading, setTxDictionaryLoading] = useState(false)
  const [txDictionaryAccounts, setTxDictionaryAccounts] = useState<ReadAccount[]>([])
  const [txDictionaryCategories, setTxDictionaryCategories] = useState<ReadCategory[]>([])
  const [txDictionaryTags, setTxDictionaryTags] = useState<ReadTag[]>([])
  // 标签详情弹窗：点击标签卡片时打开，内部用 TransactionList 无限滚动加载
  // 该标签关联的交易。
  const [tagDetail, setTagDetail] = useState<ReadTag | null>(null)
  const [tagDetailTransactions, setTagDetailTransactions] = useState<WorkspaceTransaction[]>([])
  const [tagDetailTotal, setTagDetailTotal] = useState(0)
  const [tagDetailLoading, setTagDetailLoading] = useState(false)
  const [tagDetailOffset, setTagDetailOffset] = useState(0)
  const TAG_DETAIL_PAGE_SIZE = 20
  // 账户详情弹窗：点击账户卡片（资产页）时打开。
  const [accountDetail, setAccountDetail] = useState<ReadAccount | null>(null)
  const [accountDetailTransactions, setAccountDetailTransactions] = useState<WorkspaceTransaction[]>([])
  const [accountDetailTotal, setAccountDetailTotal] = useState(0)
  const [accountDetailLoading, setAccountDetailLoading] = useState(false)
  const [accountDetailOffset, setAccountDetailOffset] = useState(0)
  const ACCOUNT_DETAIL_PAGE_SIZE = 20

  const [listUserFilter, setListUserFilter] = useState('__all__')
  const [listQuery, setListQuery] = useState('')
  const [adminUserStatusFilter, setAdminUserStatusFilter] = useState<'enabled' | 'disabled' | 'all'>('enabled')
  const [devicesWindowDays, setDevicesWindowDays] = useState<'30' | 'all'>('30')
  // header 账本选择器的值持久化到 localStorage，key 里带 userId 避免多账号
  // 登入时互相干扰。`jwtUserId(token)` 需在这里直接解析，因 sessionUserId
  // useMemo 声明在下面，且 useState 初值只跑一次，不用在意重算。
  const ACTIVE_LEDGER_KEY = useMemo(
    () => `beecount.active-ledger.${jwtUserId(token) || 'anon'}`,
    [token]
  )
  const [activeLedgerId, setActiveLedgerIdRaw] = useState<string>(() => {
    if (typeof window === 'undefined') return ''
    try {
      const initialKey = `beecount.active-ledger.${jwtUserId(token) || 'anon'}`
      return window.localStorage.getItem(initialKey) || ''
    } catch {
      return ''
    }
  })
  const setActiveLedgerId = (next: string | ((prev: string) => string)) => {
    setActiveLedgerIdRaw((prev) => {
      const value = typeof next === 'function' ? next(prev) : next
      try {
        if (value) {
          window.localStorage.setItem(ACTIVE_LEDGER_KEY, value)
        } else {
          window.localStorage.removeItem(ACTIVE_LEDGER_KEY)
        }
      } catch {
        // 忽略 storage quota / 隐私模式错误
      }
      return value
    })
  }

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
    attachments: [],
    currentIndex: 0,
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
  // "..." 菜单里只留 admin 组（用户管理）；settings 那组迁到右上角头像下拉。
  const headerMoreGroups = useMemo(
    () =>
      visibleNavGroups.filter(
        (group) => group.key !== 'bookkeeping' && group.key !== 'settings'
      ),
    [visibleNavGroups]
  )
  // 头像下拉里展示的设置组（个人资料 / 健康 / 设备）。
  const avatarMenuItems = useMemo(
    () => visibleNavGroups.find((group) => group.key === 'settings')?.items || [],
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
    console.info(
      '[profile] loaded',
      'avatar_url=', row.avatar_url,
      'avatar_version=', row.avatar_version,
      'income_is_red=', row.income_is_red,
      'theme_primary_color=', row.theme_primary_color
    )
    setProfileMe(row)
    setProfileDisplayName(row.display_name || '')
    // 收支颜色方案：mobile 写入后 server 广播下来，web 这边只读应用。
    // null 视为 true（mobile 默认红色收入）。
    applyIncomeColorScheme(row.income_is_red ?? true)
    // 主题色：PrimaryColorProvider 的 applyServerColor 自带 "本地有 override
    // 就 skip" 的短路逻辑，这里直接调。
    applyServerPrimaryColor(row.theme_primary_color)
  }

  /**
   * 把收支颜色方案写到 <html data-income-color>，styles.css 对应 CSS var
   * 自动切换。不走 localStorage，因为这个值来自 server，刷新时 loadProfile
   * 会再拉一次 —— 避免 mobile 切换后 web 本地缓存不同步。
   */
  const applyIncomeColorScheme = (incomeIsRed: boolean) => {
    if (typeof document === 'undefined') return
    document.documentElement.dataset.incomeColor = incomeIsRed ? 'red' : 'green'
  }

  /**
   * 头像 URL cache-bust：避免浏览器把旧头像长期留在 disk cache。优先用后端
   * 给的 `?v=<version>` 参数；如果 URL 里没有 v（老客户端 / 代理 strip 了）
   * 再手动拼一次。`img` 外层也会用 `key={avatar_version}` 强制重挂，两层兜底。
   */
  const withAvatarCacheBust = (url?: string | null, version?: number | null): string => {
    if (!url) return ''
    if (version == null) return url
    // 已经有 v 参数就 replace，没有就 append
    const separator = url.includes('?') ? '&' : '?'
    if (/[?&]v=\d+/.test(url)) {
      return url.replace(/([?&])v=\d+/, `$1v=${version}`)
    }
    return `${url}${separator}v=${version}`
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
    if (section === 'overview') {
      const [txPageResult, accountRows, tagRows] = await Promise.all([
        fetchWorkspaceTransactions(token, {
          ledgerId: ledgerId || undefined,
          limit: 10
        }),
        fetchWorkspaceAccounts(token, {
          limit: 500
        }),
        fetchWorkspaceTags(token, {
          limit: 500
        })
      ])
      setTransactions(txPageResult.items)
      setTxTotal(txPageResult.total)
      setAccounts(accountRows)
      setTags(tagRows)
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

    if (section === 'budgets') {
      // 预算按账本取 —— 没选账本就清空。跟 workspace 的跨账本聚合不同,
      // 预算天然是账本级概念。ledgerId 参数这里是 route.ledgerId(老链接
      // 兼容),新路由是空串;真正的"当前账本"来自 activeLedgerId 状态,
      // 跟 accounts / categories 一致。
      const effectiveId = ledgerId || activeLedgerId
      if (!effectiveId) {
        setBudgets([])
        return
      }
      setBudgets(await fetchReadBudgets(token, effectiveId))
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
      const p = payload as {
        type?: string
        avatar_version?: number
        income_is_red?: boolean | null
        theme_primary_color?: string | null
      } | null
      console.info('[profile] ws event', p)
      if (p?.type === 'profile_change') {
        // 优先用 WS payload 里的字段（比等 loadProfile 完成快）
        if (p.income_is_red !== undefined && p.income_is_red !== null) {
          applyIncomeColorScheme(p.income_is_red)
        }
        if (p.theme_primary_color) {
          applyServerPrimaryColor(p.theme_primary_color)
        }
        // profile_change 只需重拉 profile，不必 refetch 交易 / 账户。
        void loadProfile()
        return
      }
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

  /** 拉一页标签详情的交易。后端 q 参数会同时 fuzzy 匹配 tags 字段（见 read.py
   *  list_workspace_transactions 的 searchable 拼接），标签名足够独特时
   *  够用；若出现重名再补专用 tag 过滤。 */
  const loadTagDetailPage = async (tagName: string, offset: number) => {
    setTagDetailLoading(true)
    try {
      const page = await fetchWorkspaceTransactions(token, {
        q: tagName,
        limit: TAG_DETAIL_PAGE_SIZE,
        offset
      })
      setTagDetailTransactions((prev) =>
        offset === 0 ? page.items : [...prev, ...page.items]
      )
      setTagDetailTotal(page.total)
      setTagDetailOffset(offset + page.items.length)
    } catch (err) {
      setErrorNotice(renderError(err))
    } finally {
      setTagDetailLoading(false)
    }
  }

  /** 拉一页账户详情的交易。后端的 `account_name` 专门做精确账户过滤，比
   *  `q` 关键词靠谱（避免账户名 "现金" 恰好也是某条备注的 substring）。 */
  const loadAccountDetailPage = async (accountName: string, offset: number) => {
    setAccountDetailLoading(true)
    try {
      const page = await fetchWorkspaceTransactions(token, {
        accountName,
        limit: ACCOUNT_DETAIL_PAGE_SIZE,
        offset
      })
      setAccountDetailTransactions((prev) =>
        offset === 0 ? page.items : [...prev, ...page.items]
      )
      setAccountDetailTotal(page.total)
      setAccountDetailOffset(offset + page.items.length)
    } catch (err) {
      setErrorNotice(renderError(err))
    } finally {
      setAccountDetailLoading(false)
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
    payload: { email?: string; is_enabled?: boolean }
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

  const onChangeAdminUserPassword = async (
    userId: string,
    adminPassword: string,
    newPassword: string
  ): Promise<boolean> => {
    try {
      await changeAdminUserPassword(token, userId, {
        admin_password: adminPassword,
        new_password: newPassword
      })
      setSuccessNotice(t('notice.userPasswordUpdated'))
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
    setSuccessNotice(t('notice.ledgerDeleted'))
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
        // 本月 / 上月各一次 scope=month（period=YYYY-MM），避免跨时区分桶误差。
        const now = new Date()
        const currentPeriod = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
        const prevDate = new Date(now.getFullYear(), now.getMonth() - 1, 1)
        const prevPeriod = `${prevDate.getFullYear()}-${String(prevDate.getMonth() + 1).padStart(2, '0')}`
        // 用 allSettled 而不是 all：单个请求失败时其它请求的数据依然 set。
        // 三视角预拉：month / year / all，加上 year 的 income metric（Top 5 收入）。
        const results = await Promise.allSettled([
          fetchWorkspaceAnalytics(token, {
            scope: 'year',
            metric: 'expense',
            ledgerId: activeLedgerId || undefined
          }),
          fetchWorkspaceAnalytics(token, {
            scope: 'year',
            metric: 'income',
            ledgerId: activeLedgerId || undefined
          }),
          fetchWorkspaceAnalytics(token, {
            scope: 'month',
            metric: 'expense',
            period: currentPeriod,
            ledgerId: activeLedgerId || undefined
          }),
          fetchWorkspaceAnalytics(token, {
            scope: 'all',
            metric: 'expense',
            ledgerId: activeLedgerId || undefined
          }),
          fetchWorkspaceLedgerCounts(token, {
            ledgerId: activeLedgerId || undefined
          })
        ])
        if (cancelled) return
        const [rYearExpense, rYearIncome, rMonthly, rAll, rCounts] = results
        if (rYearExpense.status === 'fulfilled') {
          setAnalyticsData(rYearExpense.value)
          setCurrentYearSummary(rYearExpense.value.summary)
          setCurrentYearSeries(rYearExpense.value.series || [])
        }
        if (rYearIncome.status === 'fulfilled') {
          setAnalyticsIncomeRanks(rYearIncome.value.category_ranks || [])
        }
        if (rMonthly.status === 'fulfilled') {
          setCurrentMonthSummary(rMonthly.value.summary)
          setCurrentMonthSeries(rMonthly.value.series || [])
          setCurrentMonthCategoryRanks(rMonthly.value.category_ranks || [])
        }
        if (rAll.status === 'fulfilled') {
          setAllTimeSummary(rAll.value.summary)
          setAllTimeSeries(rAll.value.series || [])
        }
        if (rCounts.status === 'fulfilled') {
          setLedgerCounts(rCounts.value)
        } else {
          if (rYearExpense.status === 'fulfilled') {
            const s = rYearExpense.value.summary
            setLedgerCounts({
              tx_count: s?.transaction_count ?? 0,
              days_since_first_tx: s?.distinct_days ?? 0,
              distinct_days: s?.distinct_days ?? 0,
              first_tx_at: s?.first_tx_at ?? null
            })
          }
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
                  <img alt={t('shell.appName')} className="h-8 w-8 shrink-0" src="/branding/logo.svg" />
                  {/* BeeCount Cloud 版本。web bundle 的 package.json version
                      跟 server src/version.py 保持同步(发版时一起改),直接
                      从 __APP_VERSION__ vite define 注入,不走接口。
                      移动端空间紧张,版本号换到第二行;桌面端保留 baseline 同行。 */}
                  <div className="flex flex-col leading-tight md:flex-row md:items-baseline md:gap-1.5">
                    <p className="text-[15px] font-bold text-foreground">{t('shell.appName')}</p>
                    <span
                      className="font-mono text-[10px] text-muted-foreground/70"
                      title={`BeeCount Cloud v${__APP_VERSION__}`}
                    >
                      v{__APP_VERSION__}
                    </span>
                  </div>
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
                  {/* 只有当 headerMoreGroups 非空时才展示 "更多"。标签 / 设置
                      / admin-users 之前都搬去头像下拉 + 底部 tab 了，desktop
                      nav 里大多数时候这个 dropdown 为空，得把按钮本身也藏掉
                      —— 否则点开是空菜单，视觉垃圾。 */}
                  {headerMoreGroups.length > 0 ? (
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
                  ) : null}
                </nav>

                <div className="flex items-center gap-2 rounded-2xl border border-border/40 bg-accent/20 px-2 py-1">
                  {isAdminUser ? (
                    <button
                      type="button"
                      title={t('logs.open')}
                      aria-label={t('logs.open')}
                      onClick={() => setLogsOpen(true)}
                      className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
                    >
                      <ScrollText className="h-4 w-4" />
                    </button>
                  ) : null}
                  <LanguageToggle />
                  <ThemeToggle />
                  {/* 头像 hover 悬浮下拉：Radix 原生只支持 click，这里用 pure
                      CSS group-hover + focus-within 组合，hover 进 avatar 包
                      裹区就出面板，离开 400ms 后关（菜单面板顶部有空 buffer
                      防止光标从头像移到菜单时闪烁关闭）。键盘 tab 聚焦也能出。
                      分组：个人（资料 / 健康）+ 运维（设备）+ 操作（退出登录）。
                      退出按钮从右上角彻底移走了。 */}
                  {profileMe?.email ? (
                    <div className="group relative" tabIndex={-1}>
                      <button
                        type="button"
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                        title={profileMe.display_name || profileMe.email}
                      >
                        {profileMe.avatar_url ? (
                          <img
                            // key 跟 avatar_version 绑定：服务端 bump 版本号后
                            // React 会重挂载 <img>，彻底绕开浏览器 disk cache
                            // 把旧帧当新 URL 继续复用的场景。
                            key={profileMe.avatar_version ?? 0}
                            src={withAvatarCacheBust(
                              profileMe.avatar_url,
                              profileMe.avatar_version
                            )}
                            alt=""
                            className="h-8 w-8 rounded-full border border-border/40 object-cover"
                          />
                        ) : (
                          <div className="flex h-8 w-8 items-center justify-center rounded-full border border-border/40 bg-muted text-[11px] font-semibold text-muted-foreground">
                            {profileMe.email.slice(0, 1).toUpperCase()}
                          </div>
                        )}
                      </button>
                      {/* 悬浮面板 —— 默认透明不接收指针，hover/focus 状态打开 */}
                      <div
                        className="invisible absolute right-0 top-full z-50 w-60 pt-2 opacity-0 transition-[opacity,visibility] duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
                      >
                        <div className="rounded-xl border border-border/60 bg-card/95 p-1.5 shadow-xl backdrop-blur">
                          {/* 头部：用户身份 */}
                          <div className="px-2 py-2">
                            <div className="truncate text-[13px] font-semibold text-foreground">
                              {profileMe.display_name || t('shell.userDefault')}
                            </div>
                            <div className="truncate text-[11px] font-normal text-muted-foreground">
                              {profileMe.email}
                            </div>
                          </div>
                          <div className="mx-1 h-px bg-border/60" />
                          {/* 工具组:预算从顶部 bookkeeping 搬过来,访问频率
                              不够 tab 高,放下拉里刚好。 */}
                          <div className="px-1 pb-1 pt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                            {t('nav.group.tools')}
                          </div>
                          <button
                            type="button"
                            className={`block w-full rounded-lg px-2.5 py-2 text-left text-[12px] ${
                              route.section === 'budgets'
                                ? 'bg-primary/10 text-primary'
                                : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                            }`}
                            onClick={() =>
                              onNavigate({
                                kind: 'app',
                                ledgerId: '',
                                section: 'budgets'
                              })
                            }
                          >
                            {t('nav.budgets')}
                          </button>
                          <div className="mx-1 my-1 h-px bg-border/60" />
                          {/* 分组：按 avatarMenuItems 原来的顺序直出 —— 个人资料 /
                              健康 / 设备。目前三个 item 混在一组足够，等未来项
                              多了再拆子 section。 */}
                          <div className="px-1 pb-1 pt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                            {t('nav.group.settings')}
                          </div>
                          {avatarMenuItems.map((item) => {
                            const active = route.section === item.key
                            return (
                              <button
                                key={item.key}
                                type="button"
                                className={`block w-full rounded-lg px-2.5 py-2 text-left text-[12px] ${
                                  active
                                    ? 'bg-primary/10 text-primary'
                                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                                }`}
                                onClick={() =>
                                  onNavigate({
                                    kind: 'app',
                                    ledgerId: '',
                                    section: item.key
                                  })
                                }
                              >
                                {t(item.labelKey)}
                              </button>
                            )
                          })}
                          {/* admin-users 从顶部 nav 搬进来，只对管理员显示。 */}
                          {isAdminUser ? (
                            <>
                              <div className="mx-1 my-1 h-px bg-border/60" />
                              <div className="px-1 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                                {t('nav.group.admin')}
                              </div>
                              <button
                                type="button"
                                className={`block w-full rounded-lg px-2.5 py-2 text-left text-[12px] ${
                                  route.section === 'admin-users'
                                    ? 'bg-primary/10 text-primary'
                                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                                }`}
                                onClick={() =>
                                  onNavigate({
                                    kind: 'app',
                                    ledgerId: '',
                                    section: 'admin-users'
                                  })
                                }
                              >
                                {t('nav.users')}
                              </button>
                            </>
                          ) : null}
                          {/* 主题色 / 收支颜色 从这里移到独立的"外观"设置页，
                              头像下拉只留账户 + 登出的快捷入口。 */}
                          <div className="mx-1 my-1 h-px bg-border/60" />
                          <div className="px-1 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                            {t('avatar.group.actions')}
                          </div>
                          <button
                            type="button"
                            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-destructive hover:bg-destructive/10"
                            onClick={onLogout}
                          >
                            <LogOut className="h-3.5 w-3.5" />
                            {t('shell.logout')}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : null}
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

              {/* 移动端顶部不再塞导航，导航走底部 tab bar（见 <MobileBottomNav />） */}
            </header>
          </div>
        }
      >
        <div className="space-y-4 pb-20 md:pb-0">
          {route.section === 'overview' ? (
            <div className="space-y-4">
              <HomeHero
                ledgers={ledgers}
                currentLedgerId={activeLedgerId}
                monthSummary={currentMonthSummary || undefined}
                monthSeries={currentMonthSeries}
                yearSummary={currentYearSummary || undefined}
                yearSeries={currentYearSeries}
                allSummary={allTimeSummary || undefined}
                allSeries={allTimeSeries}
                ledgerCounts={ledgerCounts || undefined}
              />

              <HomeHabitStats
                monthSummary={currentMonthSummary || undefined}
                ledgerCounts={ledgerCounts || undefined}
                currency={
                  ledgers.find((l) => l.ledger_id === activeLedgerId)?.currency || 'CNY'
                }
              />

              {/* 扩展分析：Web 端独有的加强仪表，不属于 mobile 首页对标范围。 */}
              <div className="flex items-center gap-2 pt-2">
                <span className="h-px flex-1 bg-border/60" aria-hidden />
                <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
                  {t('analytics.ext.title')}
                </span>
                <span className="h-px flex-1 bg-border/60" aria-hidden />
              </div>
              <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
                <HomeMonthCategoryDonut
                  ranks={currentMonthCategoryRanks}
                  currency={
                    ledgers.find((l) => l.ledger_id === activeLedgerId)?.currency || 'CNY'
                  }
                />
                <HomeYearHeatmap
                  yearSeries={currentYearSeries}
                  currency={
                    ledgers.find((l) => l.ledger_id === activeLedgerId)?.currency || 'CNY'
                  }
                />
              </div>
              <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                <AssetCompositionDonut accounts={accounts} />
                <MonthlyTrendBars data={analyticsData?.series || []} />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <TopCategoriesList
                  ranks={analyticsData?.category_ranks || []}
                  variant="expense"
                  title={t('analytics.expenseTop5')}
                  onClickCategory={(name) => {
                    setListQuery(name)
                    onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                  }}
                />
                <TopCategoriesList
                  ranks={analyticsIncomeRanks}
                  variant="income"
                  title={t('analytics.incomeTop5')}
                  onClickCategory={(name) => {
                    setListQuery(name)
                    onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                  }}
                />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <HomeTopTags
                  tags={tags}
                  currency={
                    ledgers.find((l) => l.ledger_id === activeLedgerId)?.currency || 'CNY'
                  }
                  onClickTag={(name) => {
                    setListQuery(name)
                    onNavigate({ kind: 'app', ledgerId: '', section: 'transactions' })
                  }}
                />
                <HomeTopAccounts
                  accounts={accounts as WorkspaceAccount[]}
                  currency={
                    ledgers.find((l) => l.ledger_id === activeLedgerId)?.currency || 'CNY'
                  }
                />
              </div>
            </div>
          ) : null}

          {route.section === 'transactions' ? (
            <div className="space-y-3">
              {/* 交易搜索简化：keyword + 可选 filter 按钮，去掉 Card 包裹与
                  admin 用户选择（admin 场景走单独页，普通用户不需要暴露）。 */}
              <div className="flex flex-wrap items-center gap-2">
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
            <AccountsPanel
              form={accountForm}
              rows={accounts}
              canManage
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
              onClickAccount={(row) => {
                setAccountDetail(row)
                setAccountDetailTransactions([])
                setAccountDetailTotal(0)
                setAccountDetailOffset(0)
                void loadAccountDetailPage(row.name, 0)
              }}
            />
          ) : null}

          {route.section === 'categories' ? (
            <CategoriesPanel
              form={categoryForm}
              rows={categories}
              iconPreviewUrlByFileId={categoryIconPreviewByFileId}
              canManage
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
          ) : null}

          {route.section === 'tags' ? (
            <TagsPanel
              form={tagForm}
              rows={tags}
              canManage
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
              onClickTag={(row) => {
                setTagDetail(row)
                setTagDetailTransactions([])
                setTagDetailTotal(0)
                setTagDetailOffset(0)
                void loadTagDetailPage(row.name, 0)
              }}
            />
          ) : null}

          {route.section === 'budgets' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('nav.budgets')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="mb-3 text-xs text-muted-foreground">{t('budgets.desc')}</p>
                  {!activeLedgerId ? (
                    <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
                  ) : budgets.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t('budgets.empty')}</p>
                  ) : (
                    <div className="space-y-2">
                      {budgets.map((b) => (
                        <div
                          key={b.id}
                          className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-4 py-3"
                        >
                          <div className="flex items-center gap-3">
                            <span className="inline-flex items-center rounded-full border border-border/60 bg-card px-2 py-0.5 text-[10px] uppercase tracking-wider">
                              {b.type === 'total' ? t('budgets.type.total') : t('budgets.type.category')}
                            </span>
                            <span className="text-sm font-medium">
                              {b.type === 'category'
                                ? (b.category_name || b.category_id || t('budgets.label.unknownCategory'))
                                : t('budgets.label.allLedger')}
                            </span>
                            {!b.enabled ? (
                              <span className="text-[11px] text-muted-foreground">
                                {t('budgets.disabled')}
                              </span>
                            ) : null}
                          </div>
                          <div className="text-right">
                            <div className="font-semibold tabular-nums">
                              {b.amount.toLocaleString(undefined, {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2
                              })}
                            </div>
                            <div className="text-[11px] text-muted-foreground">
                              {b.period === 'monthly'
                                ? t('budgets.period.monthly')
                                : b.period === 'weekly'
                                  ? t('budgets.period.weekly')
                                  : b.period === 'yearly'
                                    ? t('budgets.period.yearly')
                                    : b.period}
                              {' · '}
                              {t('budgets.startDay').replace('{day}', String(b.start_day))}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          ) : null}

          {route.section === 'settings-ai' ? (
            <div className="space-y-4">
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('ai.title')}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-xs text-muted-foreground">{t('ai.desc')}</p>
                  {!profileMe?.ai_config ? (
                    <p className="text-sm text-muted-foreground">{t('ai.empty')}</p>
                  ) : (
                    <AIConfigReadOnly config={profileMe.ai_config} />
                  )}
                </CardContent>
              </Card>
            </div>
          ) : null}

          {route.section === 'settings-devices' ? (
            <div className="space-y-4">
              {/* 工具栏直出,不再外套 Card —— 跟列表同一张 panel 视觉更平整 */}
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
              <OpsDevicesPanel rows={adminDevices} onReload={onRefresh} />
            </div>
          ) : null}

          {/* 个人资料 + 外观合并为同一个页面。
              用户侧心智模型:这些都是"我的偏好",不该拆两处。
              原 `settings-appearance` 路由被合进来,旧链接兼容由头像菜单
              只保留 `settings-profile` 项实现。 */}
          {route.section === 'settings-profile' ||
          route.section === 'settings-appearance' ? (
            <div className="space-y-4">
              {/* 账号卡片:头像 + display_name 编辑 */}
              <Card className="bc-panel overflow-hidden">
                <div className="relative">
                  <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/20 via-primary/5 to-transparent" />
                  <CardContent className="relative space-y-5 p-6">
                    <div className="flex flex-wrap items-center gap-4">
                      {profileMe?.avatar_url ? (
                        <img
                          alt={profileDisplayLabel}
                          className="h-16 w-16 rounded-full border-2 border-primary/30 object-cover shadow-sm"
                          src={profileMe.avatar_url}
                        />
                      ) : (
                        <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-primary/30 bg-muted text-base font-semibold text-muted-foreground">
                          {profileInitial}
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-lg font-semibold">{profileDisplayLabel}</p>
                        <p className="truncate text-xs text-muted-foreground">{profileMe?.email || '-'}</p>
                      </div>
                    </div>
                    {/* 显示名称 / 头像编辑已移除:跟主题色 / 收支配色一样,
                        统一在移动端"我的"里修改,web 只读展示。避免两端
                        都能改导致 LWW 抖动。 */}
                    <p className="text-xs text-muted-foreground">{t('profile.avatarManagedByApp')}</p>
                  </CardContent>
                </div>
              </Card>

              {/* 主题色(web 本地生效) */}
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('profile.theme.title')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="mb-3 text-xs text-muted-foreground">{t('profile.theme.desc')}</p>
                  <PrimaryColorPicker />
                </CardContent>
              </Card>

              {/* 从 mobile 同步下来的偏好(只读展示) */}
              <Card className="bc-panel">
                <CardHeader>
                  <CardTitle>{t('profile.sync.title')}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-xs text-muted-foreground">{t('profile.sync.desc')}</p>

                  {/* 收支颜色方案 */}
                  <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-block h-4 w-4 rounded-full ring-2 ring-background"
                          style={{ background: 'rgb(var(--income-rgb))' }}
                          aria-label={t('enum.txType.income')}
                        />
                        <span className="text-sm">{t('enum.txType.income')}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-block h-4 w-4 rounded-full ring-2 ring-background"
                          style={{ background: 'rgb(var(--expense-rgb))' }}
                          aria-label={t('enum.txType.expense')}
                        />
                        <span className="text-sm">{t('enum.txType.expense')}</span>
                      </div>
                    </div>
                    <span className="rounded-full border border-border/60 bg-card px-3 py-1 text-xs font-medium">
                      {(profileMe?.income_is_red ?? true)
                        ? t('profile.sync.incomeScheme.red')
                        : t('profile.sync.incomeScheme.green')}
                    </span>
                  </div>

                  {/* 外观 JSON:月显示格式 / 紧凑金额 / 交易时间 */}
                  <div className="grid gap-2 sm:grid-cols-3">
                    <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('profile.sync.headerDecoration')}
                      </p>
                      <p className="mt-1 text-sm font-medium">
                        {profileMe?.appearance?.header_decoration_style || t('common.dash')}
                      </p>
                    </div>
                    <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('profile.sync.compactAmount')}
                      </p>
                      <p className="mt-1 text-sm font-medium">
                        {profileMe?.appearance?.compact_amount === true
                          ? t('common.on')
                          : profileMe?.appearance?.compact_amount === false
                            ? t('common.off')
                            : t('common.dash')}
                      </p>
                    </div>
                    <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('profile.sync.showTime')}
                      </p>
                      <p className="mt-1 text-sm font-medium">
                        {profileMe?.appearance?.show_transaction_time === true
                          ? t('common.on')
                          : profileMe?.appearance?.show_transaction_time === false
                            ? t('common.off')
                            : t('common.dash')}
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          ) : null}

          {route.section === 'settings-health' ? (
            <div className="space-y-4">
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
                  onChangePassword={onChangeAdminUserPassword}
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

        {/* 移动端底部 tab bar（参考 PanWatch），桌面端不渲染。 */}
        <MobileBottomNav
          activeSection={route.section}
          isAdmin={isAdminUser}
          onNavigate={(section) =>
            onNavigate({ kind: 'app', ledgerId: '', section })
          }
          onLogout={onLogout}
        />
      </AppLayout>

      <Dialog
        open={Boolean(tagDetail)}
        onOpenChange={(open) => {
          if (!open) {
            setTagDetail(null)
            setTagDetailTransactions([])
            setTagDetailTotal(0)
            setTagDetailOffset(0)
          }
        }}
      >
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle className="flex items-center gap-2">
              <span
                className="flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold text-white"
                style={{ background: tagDetail?.color || '#94a3b8' }}
              >
                #
              </span>
              <span className="truncate">{tagDetail?.name || ''}</span>
            </DialogTitle>
          </DialogHeader>
          {tagDetail ? (
            <div className="flex min-h-0 flex-1 flex-col">
              {/* 统计摘要 */}
              {(() => {
                const stats = tagStatsById[tagDetail.id]
                if (!stats) return null
                return (
                  <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('detail.stats.txCount')}
                      </div>
                      <div className="mt-0.5 font-mono text-xl font-bold tabular-nums">
                        {stats.count}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('detail.stats.accumExpense')}
                      </div>
                      <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-expense">
                        {stats.expense.toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2
                        })}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('detail.stats.accumIncome')}
                      </div>
                      <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-income">
                        {stats.income.toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2
                        })}
                      </div>
                    </div>
                  </div>
                )
              })()}

              <div className="min-h-0 flex-1 overflow-y-auto">
                <TransactionList
                  items={tagDetailTransactions}
                  tags={tags}
                  variant="compact"
                  loading={tagDetailLoading}
                  hasMore={tagDetailTransactions.length < tagDetailTotal}
                  onLoadMore={() => {
                    if (!tagDetailLoading && tagDetail) {
                      void loadTagDetailPage(tagDetail.name, tagDetailOffset)
                    }
                  }}
                  onPreviewAttachment={onPreviewTxAttachment}
                  resolveAttachmentPreviewUrl={resolveTxAttachmentPreviewUrl}
                  emptyTitle={t('transactions.empty.forTag.title')}
                  emptyDescription={t('transactions.empty.forTag.desc')}
                />
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(accountDetail)}
        onOpenChange={(open) => {
          if (!open) {
            setAccountDetail(null)
            setAccountDetailTransactions([])
            setAccountDetailTotal(0)
            setAccountDetailOffset(0)
          }
        }}
      >
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle className="truncate">
              {accountDetail?.name || ''}
            </DialogTitle>
          </DialogHeader>
          {accountDetail ? (
            <div className="flex min-h-0 flex-1 flex-col">
              {/* 统计摘要（优先用 server 返回的 balance/income/expense，无则跳过） */}
              {(() => {
                const a = accountDetail as ReadAccount & {
                  tx_count?: number | null
                  income_total?: number | null
                  expense_total?: number | null
                  balance?: number | null
                }
                const hasServerStats = typeof a.balance === 'number'
                return (
                  <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('detail.stats.currentBalance')}
                      </div>
                      <div className={`mt-0.5 font-mono text-base font-bold tabular-nums ${
                        (hasServerStats ? a.balance! : a.initial_balance ?? 0) >= 0
                          ? 'text-foreground'
                          : 'text-expense'
                      }`}>
                        {(hasServerStats
                          ? a.balance!
                          : a.initial_balance ?? 0
                        ).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2
                        })}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('detail.stats.accumIncome')}
                      </div>
                      <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-income">
                        {(a.income_total ?? 0).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2
                        })}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        {t('detail.stats.accumExpense')}
                      </div>
                      <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-expense">
                        {(a.expense_total ?? 0).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2
                        })}
                      </div>
                    </div>
                  </div>
                )
              })()}

              <div className="min-h-0 flex-1 overflow-y-auto">
                <TransactionList
                  items={accountDetailTransactions}
                  tags={tags}
                  variant="compact"
                  loading={accountDetailLoading}
                  hasMore={accountDetailTransactions.length < accountDetailTotal}
                  onLoadMore={() => {
                    if (!accountDetailLoading && accountDetail) {
                      void loadAccountDetailPage(accountDetail.name, accountDetailOffset)
                    }
                  }}
                  onPreviewAttachment={onPreviewTxAttachment}
                  resolveAttachmentPreviewUrl={resolveTxAttachmentPreviewUrl}
                  emptyTitle={t('transactions.empty.forAccount.title')}
                />
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

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

      {/* 服务端日志弹窗:只有 admin 才能在 toolbar 看到入口按钮,对话框常驻 root。 */}
      <LogsDialog token={token} open={logsOpen} onOpenChange={setLogsOpen} />

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
