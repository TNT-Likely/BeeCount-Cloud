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
import { useSharedLedgerResources } from '../../context/SharedLedgerResourcesContext'
import { bundleToReadResources } from '../../lib/shared-ledger-mappers'

import { CheckSquare, Download, SlidersHorizontal } from 'lucide-react'

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
  batchDeleteTransactions,
  downloadAttachment,
  uploadAttachment,
  type AttachmentRef,
  type ReadAccount,
  type ReadCardRewardRule,
  type ReadCategory,
  type ReadDebt,
  type ReadLedger,
  type ReadProject,
  type ReadTag,
  type ReadTransaction,
  type RecurringOccurrenceUpdatePayload,
  type RecurringUpdateFromPayload,
  type SwipeSmartCardRecommendation,
  type WorkspaceTag,
  type ProfileMe,
  deleteLedger,
  createCategory,
  createDebt,
  createProject,
  createTag,
  createTransaction,
  deleteTransaction,
  downloadWorkspaceTransactionsCsv,
  fetchAccountSuggestions,
  fetchAdminUsers,
  fetchCardRecommendation,
  fetchCardRewardRules,
  fetchCategorySuggestions,
  fetchReadDebts,
  fetchReadLedgerDetail,
  fetchReadLedgers,
  fetchReadProjects,
  type Holding,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTransaction,
  createInstallmentPlan,
  fetchProfileMe,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceHoldings,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  patchProfileMe,
  updateLedgerMeta,
  updateRecurringOccurrence,
  updateRecurringRuleFrom,
  updateTransaction
} from '@beecount/api-client'

import {
  resolveCurrencyFields,
  loadRatesToBase,
  resolveEffectiveRate,
  convertBetween,
  buildInstallmentPlanPayload,
  buildRecurringInlinePayload,
  buildTxSplitsPayload,
  validateTxSplits,
  computeTxTotalAmount,
  CategoryPickerDialog,
  ConfirmDialog,
  DatePicker,
  TagPickerDialog,
  TransactionsPanel,
  canManageLedger,
  canWriteTransactions,
  findAccountBySwipesmartCardId,
  matchCategoryByName,
  pickRandomTagColor,
  txDefaults,
  type TxForm
} from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { RecurringEditChoiceDialog } from '../../components/dialogs/RecurringEditChoiceDialog'
import { BatchDeleteDialog } from '../../components/tx-batch/BatchDeleteDialog'
import { SelectionToolbar } from '../../components/tx-batch/SelectionToolbar'
import { localizeError } from '../../i18n/errors'
import { StockTradeDialog, type TradeDialogState } from './InvestmentsPage'
import { consumePendingShareText } from '../../lib/pwa-intake'
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

/** `YYYY-MM-DD`(本地时区),对齐既有 `range=today` shortcut 的算法。 */
function formatDateInput(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 延後入帳(§2.10 Phase 5)回显用:同 CardRewardRulesSection.tsx/
 *  DebtsPanel.tsx 的 isoToDateInput —— 后端这类"纯日期"字段可能回传不带
 *  时区位移的 naive datetime 字串,缺位移标记时补 "Z" 强制当 UTC 解析,
 *  避免 UTC+8 使用者少算一天。跟上面 formatDateInput(本地时区,给"今天"
 *  这类相对日期筛选用)是两种不同语义,不能混用。 */
function isoToDateInputUtc(iso: string): string {
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  const d = new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

function dateInputToIsoUtc(value: string): string {
  return `${value}T00:00:00+00:00`
}

function todayRange(): { dateFrom: string; dateTo: string } {
  const today = formatDateInput(new Date())
  return { dateFrom: today, dateTo: today }
}

function last7DaysRange(): { dateFrom: string; dateTo: string } {
  const now = new Date()
  const from = new Date(now)
  from.setDate(from.getDate() - 6) // 含今天共 7 天
  return { dateFrom: formatDateInput(from), dateTo: formatDateInput(now) }
}

function last1MonthRange(): { dateFrom: string; dateTo: string } {
  const now = new Date()
  const from = new Date(now)
  from.setMonth(from.getMonth() - 1)
  return { dateFrom: formatDateInput(from), dateTo: formatDateInput(now) }
}

// 2026-08-04 使用者反饋:交易列表预设显示「全部」看得眼花,改成预设只看
// 「今日」(跟头部新增的快速日期筛选一致)。只影响：①从没设过筛选的全新
// 用户/账本组合;②使用者点「重设筛选」时回到的默认值——已经存过筛选(哪怕
// 是空日期)的既有用户不受影响,尊重已经保存下来的偏好。
function defaultTxFilter(): TxFilter {
  const today = todayRange()
  return {
    q: '',
    txType: '',
    accountName: '',
    amountMin: '',
    amountMax: '',
    dateFrom: today.dateFrom,
    dateTo: today.dateTo,
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

/**
 * SwipeSmart 一鍵記帳(Phase 15 §3.4/§3.5):§3.4 信用卡帳戶反查沒對照到時的
 * 降級備註文字 —— 只在帳戶沒對照到時呼叫(對照到就沒有特殊 UI 狀態,不加
 * 備註),把分類/預估回饋/回饋率能講的資訊都併進同一行,呼應 SD §3.5「原始
 * matchedCategoryName 一樣併入 §3.4 那行備註文字」。
 */
function buildSwipesmartQuickAddNote(
  t: (key: string, params?: Record<string, string | number>) => string,
  opts: { bankName: string; cardName: string; unmatchedCategoryName: string; reward: string; rate: string }
): string {
  const clauses: string[] = []
  if (opts.unmatchedCategoryName) {
    clauses.push(t('swipesmart.quickAdd.noteCategoryClause', { category: opts.unmatchedCategoryName }))
  }
  const rewardNum = Number(opts.reward)
  if (Number.isFinite(rewardNum) && rewardNum > 0) {
    clauses.push(t('swipesmart.quickAdd.noteRewardClause', { reward: rewardNum.toFixed(0) }))
  }
  const rateNum = Number(opts.rate)
  if (Number.isFinite(rateNum) && rateNum > 0) {
    clauses.push(t('swipesmart.quickAdd.noteRateClause', { rate: (rateNum * 100).toFixed(1) }))
  }
  clauses.push(t('swipesmart.quickAdd.noteUnboundClause'))
  return t('swipesmart.quickAdd.noteBankCard', {
    bank: opts.bankName,
    card: opts.cardName,
    detail: clauses.join('，')
  })
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
  // 交易搜尋日期預設改「全部」(需求 #13,Phase 12):記錄「目前是否有搜尋
  // 關鍵字」這個布林狀態,只在它真的發生「無→有」/「有→無」轉換的那一刻
  // 才自動切換日期範圍(見下方 effect),不會每個 keystroke 都重算——這樣
  // 使用者在同一次搜尋期間手動選的日期(quick chip / 進階篩選)會被保留,
  // 直到這次搜尋的關鍵字被清空為止,自然滿足「尊重使用者最後手動操作」的
  // 需求,不需要額外的「手動覆蓋」旗標。
  const txSearchHasQueryRef = useRef(false)
  const txAttachmentPreviewUrlByFileIdRef = useRef<Record<string, string>>({})
  // 进交易页时初始 state 是 defaultTxFilter()(今日),localStorage 里持久化
  // 的筛选(比如「全部」)由 restore effect 异步覆盖回来——这中间会短暂触发
  // 两次交易列表 fetch(先今日、后恢复值)。两个请求谁先返回不保证:如果
  // 「今日」那个请求恰好较晚 resolve,会用旧数据覆盖掉已经显示正确的「恢复值」
  // 结果,造成"筛选标签显示全部,但列表只有今日数据"的不一致。用递增序号只让
  // 最后发起的那次请求的结果生效。
  const txFetchSeqRef = useRef(0)
  // loadTxDictionaries 現在會在同一時間窗口內被多處並發呼叫(onSaveTransaction/
  // onDeleteTransaction/批量刪除/WS sync_change 觸發的 refreshAllSections 都各
  // 自呼叫一次),沒有排序保護的話,較早發出但較晚 resolve 的那次請求會用舊
  // 資料蓋掉後面已經刷新成功的新資料——跟上面 txFetchSeqRef 同一個理由,只讓
  // 最後發起的那次請求的結果生效。
  const txDictFetchSeqRef = useRef(0)

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
  // SwipeSmart 一鍵記帳(Phase 15 §3.3):quick-add effect 要等這份字典至少
  // 載入過一次才能做 §3.4/§3.5 反查,否則首次進頁面時字典還沒拉回來就會誤判
  // 成「沒對照到」而降級 —— `txDictionaryLoading` 初始值是 false,不足以區分
  // 「還沒開始拉」跟「已經拉完」,所以另外開一个標記。
  const [txDictionariesLoadedOnce, setTxDictionariesLoadedOnce] = useState(false)
  // 轉帳「代入餘額」(2026-08-29):寬成 WorkspaceAccount[] 才能暴露
  // `balance`/`balance_fx_incomplete`——`fetchWorkspaceAccounts` 本來就回傳
  // WorkspaceAccount[],之前這裡收窄成 ReadAccount[] 只是沒用到 balance,
  // 見 AccountListRow.tsx 的 AccountStats 同款寫法。
  const [txDictionaryAccounts, setTxDictionaryAccounts] = useState<WorkspaceAccount[]>([])
  const [txDictionaryCategories, setTxDictionaryCategories] = useState<ReadCategory[]>([])
  const [txDictionaryTags, setTxDictionaryTags] = useState<ReadTag[]>([])
  // 轉帳導向股票交易(docs/STOCK_HOLDINGS_SD.md §9):
  // 轉入/轉出帳戶選到投資理財帳戶時,彈出跟「投資」頁同一個買進/賣出
  // dialog,不留在一般轉帳表單裡送出裸轉帳。`txStockHoldings` 只在打開賣出
  // 時才拉(client 端賣超檢查用,server 端 `stock_trades.py` 還會再驗一次)。
  const [txStockTrade, setTxStockTrade] = useState<TradeDialogState | null>(null)
  const [txStockHoldings, setTxStockHoldings] = useState<Holding[]>([])
  // 對話框剛打開那一刻的轉帳兩側帳戶名稱基準,判斷「使用者這次是不是真的
  // 改過帳戶」用,見下面的轉帳導向股票交易 effect。
  const txTransferAccountsAtOpen = useRef<{ from: string; to: string } | null>(null)
  // 借還款追蹤(§2.5 體驗補強):跟 account/category/tag 不同,debt 是
  // ledger-scoped 实体,按「当前写入账本」单独拉,不进 loadTxDictionaries
  // 那套 user-global 字典逻辑。
  const [txDictionaryDebts, setTxDictionaryDebts] = useState<ReadDebt[]>([])
  // 專案(Phase 13,docs/PH13_PROJECT_SD.md):跟 debt 同款语意,ledger-scoped
  // 实体,按「当前写入账本」单独拉。
  const [txDictionaryProjects, setTxDictionaryProjects] = useState<ReadProject[]>([])
  // 信用卡紅利回饋(§2.9.5,2026-08-06 改版):主表單「勾選要走哪幾條規則」
  // 下拉的選項,按目前表單選中的信用卡帳戶單獨拉(規則綁在具體帳戶上,不是
  // ledger-scoped,也不进 loadTxDictionaries 的 user-global 字典)。
  const [txFormRewardRules, setTxFormRewardRules] = useState<ReadCardRewardRule[]>([])
  // 分類智慧推薦(Phase 21,docs/PH17_USER_FEEDBACK_2026-08_SD.md):比照上面
  // 信用卡紅利規則同款「按目前表單狀態單獨拉,不进 loadTxDictionaries」的
  // 惯例。
  const [txFormCategorySuggestions, setTxFormCategorySuggestions] = useState<string[]>([])
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
  // URL → state 同步:CmdK / 其它入口 navigate 到 `?q=xxx` 时,如果当前页
  // 已挂载(useState 初值不会重跑),需要在这里把 URL 的 q 写入 listQuery,
  // 否则搜索结果对了但输入框还是空的(用户感知就是"搜索框没填上")。
  // 只在 URL 有非空 q 时同步 — URL 被清掉时不要把输入框也清空(用户可能
  // 正在打字)。
  useEffect(() => {
    const urlQ = searchParams.get('q')
    if (urlQ && urlQ !== listQuery) {
      setListQueryRaw(urlQ)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  // 用户手动输入 -> 清掉 URL 上的 q 参数,避免刷新后又自动回到旧预填值。
  // 只在 URL 里当前有 q 且跟 state 不一致时触发一次删除。
  useEffect(() => {
    if (searchParams.has('q') && searchParams.get('q') !== listQuery) {
      const next = new URLSearchParams(searchParams)
      next.delete('q')
      setSearchParams(next, { replace: true })
    }
  }, [listQuery, searchParams, setSearchParams])
  // 批量选择模式 —— 桌面端独占。entry 按钮 + toolbar 都用 hidden md:flex
  // CSS gating,小屏既不暴露入口也无法切换。Esc 退出 + 当前页 ledger 切换
  // 时自动清空。lastClickIndex 用于 ⇧ + Click 范围选(Gmail 风格)。
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedTxIds, setSelectedTxIds] = useState<Set<string>>(new Set())
  const lastClickIndexRef = useRef<number | null>(null)
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false)
  const [batchSaving, setBatchSaving] = useState(false)

  const [adminUserStatusFilter, setAdminUserStatusFilter] = useState<'enabled' | 'disabled' | 'all'>('enabled')
  // devicesWindowDays state 已迁到 SettingsDevicesPage。
  // activeLedgerId / setActiveLedgerId 由 AppShell 提供(useLedgers 已在
  // 顶部解构),含 localStorage 持久化 + 跨账本变更自动 reconcile。

  const [txWriteLedgerId, setTxWriteLedgerId] = useState('')

  const [txFilterApplied, setTxFilterApplied] = useState<TxFilter>(defaultTxFilter)
  // refreshCurrent/refreshAllSections(挂载引导 / 下拉刷新 / WS 重连兜底)会
  // 调用 refreshSectionData,但它们各自的闭包可能定格在很早的 render(比如
  // 挂载引导 effect 的 deps 是 route.section/route.ledgerId,不含
  // txFilterApplied,restore effect 把筛选从「今日」改成持久化值后它也不会
  // 重新捕获)。这些调用点普遍还要先 await loadLedgers/loadLedgerBase(比
  // 主 data-loading effect 的路径慢),经常在 restore 早就生效之后才真正
  // 发出交易请求,却带着挂载那一刻的旧筛选——用 ref 保证不管哪个闭包发起
  // 调用,实际发出去的请求永远用当下最新的筛选,不会用旧值覆盖已经正确显示
  // 的结果。
  const txFilterAppliedRef = useRef(txFilterApplied)
  txFilterAppliedRef.current = txFilterApplied
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
  // Phase 24(需求 #5):列表行內「編輯」按鈕(不經過 GlobalEntityDialogs 的
  // 詳情彈窗)點到週期性收支產生的交易時,先問「修改此記錄」還是「修改連同
  // 未來週期」——recurringEditChoice 是待選擇的目標交易,recurringEditContext
  // 是選完之後記錄的 { ruleId, mode },存檔時 onSaveTransaction 依此分流。
  const [recurringEditChoice, setRecurringEditChoice] = useState<WorkspaceTransaction | null>(null)
  const [recurringEditContext, setRecurringEditContext] = useState<
    { ruleId: string; mode: 'single' | 'future' } | null
  >(null)
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
  // v30 多币种:币种选择弹窗展示各币种对账本主币种的汇率(弹窗开时拉,5min 缓存)。
  const [txCurrencyRates, setTxCurrencyRates] = useState<Record<string, number>>({})
  useEffect(() => {
    if (!token || !txDialogOpen) return
    let cancelled = false
    loadRatesToBase(token, txWriteLedgerCurrency)
      .then((m) => {
        if (!cancelled) setTxCurrencyRates(m)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [token, txDialogOpen, txWriteLedgerCurrency])
  // §7 共享账本:当前编辑/查看的账本若是共享账本(Editor 视角),走独立
  // SharedLedgerResources state(Owner 的 user-global 资源镜像),否则走
  // 用户自己的 user-global 字典。逻辑对齐 mobile picker filter。
  const txContextLedgerId = txWriteLedgerId || activeLedgerId
  const txContextLedger = useMemo(
    () => ledgers.find((l) => l.ledger_id === txContextLedgerId) || null,
    [ledgers, txContextLedgerId],
  )
  const txIsSharedEditor = Boolean(
    txContextLedger?.is_shared && txContextLedger.role !== 'owner',
  )
  // 借還款追蹤(體驗補強第二輪):「建立新欠款」走 owner-only 的
  // POST .../debts(跟 DebtsPage.tsx::canManage 同一权限判断),不是一般
  // 记交易的写权限,所以单独算好传给 TransactionsPanel。
  const txCanCreateDebt = txContextLedger?.role === 'owner'
  // 專案(Phase 13):新建專案同样是 owner-only 的 `POST .../projects`。
  const txCanManageProjects = txContextLedger?.role === 'owner'
  const { bundle: sharedBundle } = useSharedLedgerResources(
    txIsSharedEditor ? txContextLedgerId : null,
  )
  const sharedAsRead = useMemo(
    () => bundleToReadResources(sharedBundle),
    [sharedBundle],
  )

  // 借還款追蹤(§2.5 體驗補強):主表單掛欠款的下拉選項,按目前寫入的
  // 账本单独拉(debt 是 ledger-scoped,不像 account/category/tag 是
  // user-global,不能复用 loadTxDictionaries 那套逻辑)。read 端点对共享
  // 账本的 editor 角色也开放(不是 owner-only),不需要走 sharedBundle 分支。
  useEffect(() => {
    if (route.section !== 'transactions' || !txContextLedgerId) {
      setTxDictionaryDebts([])
      return
    }
    let cancelled = false
    fetchReadDebts(token, txContextLedgerId)
      .then((rows) => {
        if (!cancelled) setTxDictionaryDebts(rows)
      })
      .catch(() => {
        if (!cancelled) setTxDictionaryDebts([])
      })
    return () => {
      cancelled = true
    }
  }, [route.section, token, txContextLedgerId])

  // 專案(Phase 13):跟 debt 同款语意,主表單掛專案的下拉選項,按目前寫入
  // 的账本单独拉。
  useEffect(() => {
    if (route.section !== 'transactions' || !txContextLedgerId) {
      setTxDictionaryProjects([])
      return
    }
    let cancelled = false
    fetchReadProjects(token, txContextLedgerId)
      .then((rows) => {
        if (!cancelled) setTxDictionaryProjects(rows)
      })
      .catch(() => {
        if (!cancelled) setTxDictionaryProjects([])
      })
    return () => {
      cancelled = true
    }
  }, [route.section, token, txContextLedgerId])

  // 跨幣別自動換算:帳戶下拉不再按表單所選幣別過濾(任何幣別的帳戶都能選,
  // 選到跟入帳幣別不同的帳戶時由 TransactionsPanel 顯示換算列 + 送出前換算,
  // 見 forms.ts::fx_rate_override / currencies.ts::convertBetween)。
  const txFormCurrency = (txForm.currency || txWriteLedgerCurrency).toUpperCase()
  const txWriteAccounts = useMemo(() => {
    const source =
      txIsSharedEditor && sharedBundle ? sharedAsRead.accounts : txDictionaryAccounts
    // 轉帳可以選投資理財帳戶:選中後由下面的 stock trade 轉導邏輯接手,變成
    // 買進/賣出介面,不會真的送出一筆「股數對不上」的裸轉帳(見
    // docs/STOCK_HOLDINGS_SD.md §9)。
    return source.filter((row) => {
      const type = row.account_type || ''
      if (txForm.tx_type === 'transfer' && type === 'investment') return true
      return !VALUATION_ACCOUNT_TYPES.has(type)
    })
  }, [
    txDictionaryAccounts,
    VALUATION_ACCOUNT_TYPES,
    txIsSharedEditor,
    sharedBundle,
    sharedAsRead.accounts,
    txForm.tx_type,
  ])
  const txWriteCategories =
    txIsSharedEditor && sharedBundle ? sharedAsRead.categories : txDictionaryCategories
  const txWriteTags =
    txIsSharedEditor && sharedBundle ? sharedAsRead.tags : txDictionaryTags

  // 轉帳導向股票交易:轉入帳戶是投資理財帳戶 → 買進,轉出帳戶是投資理財
  // 帳戶 → 賣出,另一側已選的帳戶帶過去當交割戶(對齊 mobile
  // transfer_form.dart::_redirectToStockTrade)。表單帳戶欄位存的是名稱
  // (`from_account_name`/`to_account_name`,id 由 `onSaveTransaction` 送出前
  // 才查表解析),所以這裡也按名稱比對;只查 `txDictionaryAccounts`(使用者
  // 自己的 user-global 字典)——共享帳本 Editor 視角(`sharedAsRead.accounts`)
  // 是 Owner 資源鏡像,不會有這個使用者自己的投資理財帳戶,略過。
  //
  // 編輯既有交易時只在「使用者這次真的改了帳戶」才導向:mobile 端只在
  // `_pickAccount`(使用者主動選)裡判斷,打開既有轉帳的編輯頁本身(單純
  // 反查帳戶顯示)不會觸發——這裡用 `txTransferAccountsAtOpen` 記住對話框
  // 剛打開那一刻的兩個帳戶名稱當基準,跟基準一樣就當作「還沒被使用者改過」
  // 略過,避免打開一筆舊資料裡本來就存在(這個功能上線前的裸轉帳)的投資
  // 理財帳戶就被強制彈買賣dialog。
  useEffect(() => {
    txTransferAccountsAtOpen.current = txDialogOpen
      ? { from: txForm.from_account_name, to: txForm.to_account_name }
      : null
    // 只在對話框開/關那一刻拍照,不必每次表單內容變動都跟著跑。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [txDialogOpen])

  useEffect(() => {
    if (!txDialogOpen || txForm.tx_type !== 'transfer' || txStockTrade || txIsSharedEditor) return
    const baseline = txTransferAccountsAtOpen.current
    if (
      baseline &&
      baseline.from === txForm.from_account_name &&
      baseline.to === txForm.to_account_name
    ) {
      return
    }
    const byName = (name: string) =>
      txDictionaryAccounts.find((a) => a.name.trim().toLowerCase() === name.trim().toLowerCase())
    const toAccount = byName(txForm.to_account_name)
    const fromAccount = byName(txForm.from_account_name)
    const isBuy = toAccount?.account_type === 'investment'
    const isSell = !isBuy && fromAccount?.account_type === 'investment'
    if (!isBuy && !isSell) return
    const account = isBuy ? toAccount! : fromAccount!
    const settlement = isBuy ? fromAccount : toAccount
    let cancelled = false
    void fetchWorkspaceHoldings(token, { accountId: account.id, refresh: false })
      .then((summary) => {
        if (cancelled) return
        const holdings = summary.accounts.find((a) => a.account_id === account.id)?.holdings ?? []
        setTxStockHoldings(holdings)
        setTxStockTrade({
          account,
          initial: { type: isBuy ? 'buy' : 'sell' },
          initialSettlementAccountId: settlement?.id,
        })
      })
      .catch(() => {
        if (cancelled) return
        setTxStockHoldings([])
        setTxStockTrade({
          account,
          initial: { type: isBuy ? 'buy' : 'sell' },
          initialSettlementAccountId: settlement?.id,
        })
      })
    return () => {
      cancelled = true
    }
  }, [
    txDialogOpen,
    txForm.tx_type,
    txForm.to_account_name,
    txForm.from_account_name,
    txDictionaryAccounts,
    txIsSharedEditor,
    txStockTrade,
    token,
  ])

  // 取消買進/賣出 dialog:把剛選的投資理財帳戶退回未選狀態,轉帳表單其它
  // 欄位(金額/備註/日期…)維持不動,不強迫使用者重填一次。
  const closeTxStockTrade = (opts: { clearAccount: 'to' | 'from' }) => {
    setTxStockTrade(null)
    setTxStockHoldings([])
    setTxForm((prev) =>
      opts.clearAccount === 'to' ? { ...prev, to_account_name: '' } : { ...prev, from_account_name: '' },
    )
  }

  // 信用卡紅利回饋(§2.9.5,2026-08-06 改版):只有 expense + 選中帳戶是
  // credit_card 时才需要拉规则列表——不 filter enabled,已停用的规则如果
  // 曾被这笔交易勾选过,仍要能在列表里显示(打勾状态 + 停用徽章),让使用者
  // 能取消勾选。
  useEffect(() => {
    if (!txDialogOpen || txForm.tx_type !== 'expense' || !txWriteLedgerId) {
      setTxFormRewardRules([])
      return
    }
    const account = txWriteAccounts.find(
      (row) => (row.name || '').trim().toLowerCase() === txForm.account_name.trim().toLowerCase(),
    )
    if (!account || account.account_type !== 'credit_card') {
      setTxFormRewardRules([])
      return
    }
    let cancelled = false
    fetchCardRewardRules(token, txWriteLedgerId, account.id)
      .then((rows) => {
        if (!cancelled) setTxFormRewardRules(rows)
      })
      .catch(() => {
        if (!cancelled) setTxFormRewardRules([])
      })
    return () => {
      cancelled = true
    }
  }, [txDialogOpen, txForm.tx_type, txForm.account_name, txWriteLedgerId, txWriteAccounts, token])

  // 分類智慧推薦(Phase 21):開啟表單、切換交易類型、切換帳戶都要重新拉一次
  // ——依「整體頻率＋同時段＋同帳戶」加權排序。transfer 沒有分類概念,不拉。
  useEffect(() => {
    if (!txDialogOpen || txForm.tx_type === 'transfer' || !txWriteLedgerId) {
      setTxFormCategorySuggestions([])
      return
    }
    const account = txWriteAccounts.find(
      (row) => (row.name || '').trim().toLowerCase() === txForm.account_name.trim().toLowerCase(),
    )
    let cancelled = false
    fetchCategorySuggestions(token, txWriteLedgerId, {
      txType: txForm.tx_type,
      accountId: account?.id,
      hour: new Date().getHours(),
      tzOffsetMinutes: -new Date().getTimezoneOffset(),
    })
      .then((res) => {
        if (!cancelled) setTxFormCategorySuggestions(res.category_ids)
      })
      .catch(() => {
        if (!cancelled) setTxFormCategorySuggestions([])
      })
    return () => {
      cancelled = true
    }
  }, [txDialogOpen, txForm.tx_type, txForm.account_name, txWriteLedgerId, txWriteAccounts, token])

  // 依分類帶入常用帳戶(Phase 21):TransactionsPanel 選定分類後呼叫這支,
  // 只回傳 account_id 清單,實際「要不要自動帶入/會不會覆蓋使用者已選值」
  // 的判斷留給 panel 自己做(panel 才知道 form.account_name 目前是否為空)。
  const txFetchAccountSuggestions = useCallback(
    (ledgerId: string, categoryId: string) =>
      fetchAccountSuggestions(token, ledgerId, categoryId).then((res) => res.account_ids),
    [token]
  )

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

  const refreshSectionData = async (ledgerId: string, section: AppSection, preAssignedSeq?: number) => {
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
      // 读 ref 不读闭包参数 txFilterApplied —— refreshCurrent/refreshAllSections
      // 等调用点的闭包可能定格在 restore effect 生效之前的旧 render(它们的
      // 触发 effect 不依赖 txFilterApplied,不会因为筛选恢复而重新捕获),
      // 且这些调用点普遍还要先 await loadLedgers/loadLedgerBase,经常在
      // restore 早就生效之后才真正发出请求,却带着挂载那一刻的旧筛选。ref
      // 保证不管哪个闭包发起调用,实际发出去的请求永远用当下最新筛选。
      const activeFilter = txFilterAppliedRef.current
      // 把 filter draft / 应用值里 stringy 的字段转成 server 期望的形式:
      //   - amountMin/Max: '' → undefined,'12.5' → 12.5
      //   - dateFrom: 'YYYY-MM-DD' → ISO datetime 当天 00:00 (UTC)
      //   - dateTo:   'YYYY-MM-DD' → ISO datetime **次日** 00:00 (UTC),
      //     server 端用 happened_at < date_to,正好覆盖整个 dateTo 当天
      const minNum = Number(activeFilter.amountMin || '')
      const maxNum = Number(activeFilter.amountMax || '')
      const dateFromIso = activeFilter.dateFrom
        ? new Date(`${activeFilter.dateFrom}T00:00:00`).toISOString()
        : undefined
      const dateToIso = activeFilter.dateTo
        ? (() => {
            const d = new Date(`${activeFilter.dateTo}T00:00:00`)
            d.setDate(d.getDate() + 1)
            return d.toISOString()
          })()
        : undefined

      // 进页面/恢复筛选那一小段时间可能连续触发两次这个分支(先用初始默认值
      // 「今日」,restore effect 恢复持久化筛选后再来一次)。两次请求谁先返回
      // 不保证,只让最后发起的这次结果落地,避免旧请求(比如「今日」)较晚
      // resolve 时用过期数据覆盖掉已经正确显示的新筛选结果。preAssignedSeq
      // 由调用方(data-loading effect)在 await loadLedgerBase 之前同步分配,
      // 反映 effect 真实触发顺序;没有传入时(其它零散调用点)退回这里自己
      // 分配,仍然单调递增。
      const fetchSeq = preAssignedSeq ?? ++txFetchSeqRef.current

      const [txPageResult, accountRows, categoryRows, tagRows] = await Promise.all([
        fetchWorkspaceTransactions(token, {
          ledgerId: ledgerId || undefined,
          userId: txUserId,
          q: listQuery || undefined,
          txType: activeFilter.txType || undefined,
          accountName: activeFilter.accountName || undefined,
          categorySyncId: activeFilter.categorySyncId || undefined,
          tagSyncId: activeFilter.tagSyncId || undefined,
          amountMin: Number.isFinite(minNum) && activeFilter.amountMin ? minNum : undefined,
          amountMax: Number.isFinite(maxNum) && activeFilter.amountMax ? maxNum : undefined,
          dateFrom: dateFromIso,
          dateTo: dateToIso,
          limit: txPageSize,
          offset: (txPage - 1) * txPageSize
        }),
        fetchWorkspaceAccounts(token, { userId: txUserId, limit: 500 }),
        fetchWorkspaceCategories(token, { userId: txUserId, limit: 500 }),
        fetchWorkspaceTags(token, { userId: txUserId, limit: 500 })
      ])
      if (fetchSeq !== txFetchSeqRef.current) return
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
      // GlobalEntityDialogs 詳情彈窗的刪除入口(2026-09-18 補強)不持有這個
      // page 的 baseChangeId,純靠這裡的 sync_change WS 事件觸發刷新——上面
      // 幾行刷的 accounts 狀態跟表單帳戶選單吃的 txDictionaryAccounts 是
      // 兩份不同字典(理由同 onDeleteTransaction),漏了這行的話透過詳情彈窗
      // 刪除交易後,交易表單的帳戶選單餘額不會反映刪除結果。
      loadTxDictionaries(),
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
    // URL `?q=xxx` 优先 —— 从 CmdK / Overview TopCategory 跳过来时 URL 带的
    // 搜索词必须落到输入框,不能被 localStorage 里的旧值盖掉。
    const urlQ = searchParams.get('q')
    const effectiveQ = urlQ && urlQ.trim().length > 0 ? urlQ : nextFilter.q
    const hasQuery = effectiveQ.trim() !== ''
    // 需求 #13(Phase 12):快取還原邏輯修正 —— 若還原出來的關鍵字非空,強制
    // 套用「全部」日期範圍,不管快取裡存的 dateFrom/dateTo 是什麼(舊版存量
    // 使用者可能存過「有關鍵字 + 今日」這種在新規則下不該共存的組合)。無
    // 關鍵字則完全維持原本還原邏輯(不強制回「今日」,尊重使用者已保存的
    // 其它篩選)。
    const restoredFilter = hasQuery
      ? { ...nextFilter, q: effectiveQ, dateFrom: '', dateTo: '' }
      : { ...nextFilter, q: effectiveQ }
    setListQuery(effectiveQ)
    setTxFilterApplied(restoredFilter)
    setTxFilterDraft(restoredFilter)
    setTxPage(1)
    // 同步「目前是否有關鍵字」的轉換偵測基準,避免還原完成後下面那個 effect
    // 誤判成一次新的「無→有」轉換,又跑一次自動切換邏輯(此時已經按上面的
    // 規則處理過了,不需要重複套用)。
    txSearchHasQueryRef.current = hasQuery
    queueMicrotask(() => {
      txFilterRestoreInProgressRef.current = false
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.section, txFilterPersistKey])

  // 交易搜尋日期預設改「全部」(需求 #13,Phase 12):有搜尋關鍵字時自動把
  // 日期篩選切成「全部」(dateFrom/dateTo 清空);關鍵字被清空時自動回復
  // 「今日」。只在「有沒有關鍵字」的轉換那一刻套用一次(見上方
  // `txSearchHasQueryRef` 註解),重新打 API 交給下面既有的 fetch effect
  // (依賴 `txFilterApplied`)自動處理,這裡不用額外呼叫。
  useEffect(() => {
    if (route.section !== 'transactions') return
    if (txFilterRestoreInProgressRef.current) return
    const hasQuery = listQuery.trim() !== ''
    if (hasQuery === txSearchHasQueryRef.current) return
    txSearchHasQueryRef.current = hasQuery
    if (hasQuery) {
      setTxFilterApplied((prev) =>
        prev.dateFrom === '' && prev.dateTo === '' ? prev : { ...prev, dateFrom: '', dateTo: '' }
      )
      setTxFilterDraft((prev) =>
        prev.dateFrom === '' && prev.dateTo === '' ? prev : { ...prev, dateFrom: '', dateTo: '' }
      )
    } else {
      const today = todayRange()
      setTxFilterApplied((prev) => ({ ...prev, dateFrom: today.dateFrom, dateTo: today.dateTo }))
      setTxFilterDraft((prev) => ({ ...prev, dateFrom: today.dateFrom, dateTo: today.dateTo }))
    }
    setTxPage(1)
  }, [listQuery, route.section])

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

  // PWA shortcut / share target / file handler 触发的 URL action 消费:
  //   action=quick-add  → 自动弹「新建交易」对话框,且若 pwa-intake 单例里
  //                       有 share target 投递的文本(标题/正文/URL),按
  //                       「标题 - 正文 url」拼成 note 预填(≤60 字符截断)
  //   range=today       → 把日期筛选预设为今天(「今日账单」shortcut)
  // source=swipesmart(Phase 15,docs/PH15_SWIPESMART_QUICKADD_SD.md §3.3):
  //   SwipeSmart 推荐结果「一键记账」deep link,额外带 merchant/amount/
  //   category/cardId/bankName/cardName/reward/rate,§3.4 用 cardId 反查信用
  //   卡帐户、§3.5 用 category 反查分类,对不到就降级成备注文字,不擋流程。
  // 消费后一次性 strip URL 参数,避免刷新或下次 effect 重触发。canWriteTx
  // 还没就绪(ledger 还在加载)时 quick-add 留着不消费,deps 变化时再次跑
  // 直到 ledger 加载完 —— 这样用户不会因为时序差就丢掉 shortcut intent。
  // swipesmart 分支还要等 txDictionariesLoadedOnce(帐户/分类字典至少载入
  // 过一次)才消费,否则会在字典还没拉回来的第一个 render 就误判成「没对照
  // 到」而降级 —— 因为 txDictionaryLoading 的初始值是 false,不足以区分
  // 「还没开始拉」跟「已经拉完」。
  useEffect(() => {
    if (route.section !== 'transactions') return
    const action = searchParams.get('action')
    const range = searchParams.get('range')
    if (!action && !range) return

    const source = searchParams.get('source')
    if (action === 'quick-add' && source === 'swipesmart' && canWriteTx && !txDictionariesLoadedOnce) {
      return
    }

    const consumed: string[] = []

    if (action === 'quick-add' && canWriteTx) {
      if (source === 'swipesmart') {
        const merchant = (searchParams.get('merchant') || '').trim().slice(0, 200)
        const amountRaw = (searchParams.get('amount') || '').trim()
        const amountNum = Number(amountRaw)
        const amount = Number.isFinite(amountNum) && amountNum > 0 ? amountRaw : ''
        const categoryRaw = (searchParams.get('category') || '').trim().slice(0, 100)
        const cardId = (searchParams.get('cardId') || '').trim()
        const bankName = (searchParams.get('bankName') || '').trim().slice(0, 100)
        const cardName = (searchParams.get('cardName') || '').trim().slice(0, 100)
        const reward = searchParams.get('reward') || ''
        const rate = searchParams.get('rate') || ''

        const account = findAccountBySwipesmartCardId(txDictionaryAccounts, cardId)
        // 分類跟帳戶/標籤一樣是使用者級字典(`loadTxDictionaries` 的既有註解:
        // 「不像 debt/project 是 ledger-scoped」),`/read/workspace/categories`
        // 回的 `ledger_id` 固定是空字串,不能拿來做帳本過濾 —— 直接比對
        // `txDictionaryCategories`,跟 `txWriteCategories`(分類選擇器實際顯示
        // 的候選清單)同一個範圍,只窄化到 expense(這筆一鍵記帳固定是支出)。
        const expenseCategories = txDictionaryCategories.filter((c) => c.kind === 'expense')
        const category = matchCategoryByName(expenseCategories, categoryRaw || null)

        const noteLine = account
          ? ''
          : buildSwipesmartQuickAddNote(t, {
              bankName,
              cardName,
              unmatchedCategoryName: category ? '' : categoryRaw,
              reward,
              rate
            })

        setTxForm((prev) => ({
          ...prev,
          merchant: merchant || prev.merchant,
          amount: amount || prev.amount,
          account_name: account ? account.name : prev.account_name,
          category_name: category ? category.name : prev.category_name,
          category_kind: category ? prev.tx_type : prev.category_kind,
          note: noteLine ? [prev.note, noteLine].filter(Boolean).join('\n') : prev.note
        }))
        consumed.push('merchant', 'amount', 'category', 'cardId', 'bankName', 'cardName', 'reward', 'rate')
      } else {
        // 优先消费 share target 投递的文本,拼成 note 预填(≤60 chr 截断,
        // 避免长 URL 撑爆 textarea)
        const shareText = consumePendingShareText()
        if (shareText) {
          const composed = [shareText.title, shareText.text, shareText.url]
            .map((s) => (s || '').trim())
            .filter(Boolean)
            .join(' ')
            .slice(0, 60)
          if (composed) {
            setTxForm((prev) => ({ ...prev, note: composed }))
          }
        }
      }
      setTxDialogOpen(true)
      consumed.push('action')
    }

    if (range === 'today') {
      const now = new Date()
      const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
      setTxFilterApplied((prev) => ({ ...prev, dateFrom: todayStr, dateTo: todayStr }))
      setTxFilterDraft((prev) => ({ ...prev, dateFrom: todayStr, dateTo: todayStr }))
      consumed.push('range')
    }

    if (consumed.length === 0) return

    const next = new URLSearchParams(searchParams)
    consumed.forEach((k) => next.delete(k))
    next.delete('source')
    setSearchParams(next, { replace: true })
  }, [
    route.section,
    searchParams,
    canWriteTx,
    setSearchParams,
    txDictionariesLoadedOnce,
    txDictionaryAccounts,
    txDictionaryCategories,
    t
  ])

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
    // 进交易页那一刻,restore effect(见上面)可能还没把 localStorage 里持久化
    // 的筛选(比如「全部」)写回 txFilterApplied —— 此时 state 仍是初始默认值
    // 「今日」。如果这里照样用当前 state 发一次 fetch,就是拿着马上要被覆盖掉
    // 的「今日」筛选去查,等 restore 的 setState 真正生效、这个 effect 因为
    // deps 变化重新跑一次「全部」查询时,两次请求谁的响应先回来不保证,验证时
    // 会看到"筛选标签显示全部,但列表其实是今日那次请求的结果"。restore
    // 阶段直接跳过这次 fetch,只等 restore 完成后 state 变化触发的那次为准。
    if (section === 'transactions' && txFilterRestoreInProgressRef.current) return

    let cancelled = false
    // seq 必须在这里(await loadLedgerBase 之前)同步分配,不能留给
    // refreshSectionData 内部在 Promise.all 前才分配 —— loadLedgerBase 本身
    // 是一次网络请求,耗时不定,如果先后触发了两次 effect,各自的 loadLedgerBase
    // 谁先 resolve 不保证,后触发的那次可能因为 loadLedgerBase 更快返回而先跑到
    // refreshSectionData 内部去分配 seq,导致先触发但 loadLedgerBase 较慢的那次
    // 反而拿到更大的 seq,把正确结果覆盖掉。同步分配保证 seq 顺序 = effect 真实
    // 触发顺序,不受后续 await 时长影响。
    const mySeq = section === 'transactions' ? ++txFetchSeqRef.current : 0
    const run = async () => {
      try {
        // 切账本 / 进页面时要更新 base_change_id 给写操作用。section 不需要
        // ledger 时(admin-users 等)不调,避免无意义请求。
        if (sectionNeedsLedger(section) && activeLedgerId) {
          await loadLedgerBase(activeLedgerId)
        }
        if (cancelled) return
        await refreshSectionData(activeLedgerId || '', section, mySeq || undefined)
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
    const fetchSeq = ++txDictFetchSeqRef.current
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
      // 較早發出但較晚 resolve 的請求到這裡時已經不是最新一次呼叫,丟棄它的
      // 結果——不然會用舊資料蓋掉後面已經 apply 成功的新資料(理由同上)。
      if (fetchSeq !== txDictFetchSeqRef.current) return
      setTxDictionaryAccounts(accountRows)
      setTxDictionaryCategories(categoryRows)
      setTxDictionaryTags(tagRows)
    } finally {
      setTxDictionaryLoading(false)
      setTxDictionariesLoadedOnce(true)
    }
  }

  // SwipeSmart 刷卡建議(Phase 14 §3.3.5):失敗/逾時已經在 api-client 的
  // fetchCardRecommendation 這層之外沒有額外包裝,這裡 catch 成空陣列 ——
  // 呼應「外部依賴不能擋記帳」原則,面板拿到空陣列就是「不顯示建議區塊」。
  const handleFetchCardRecommendations = useCallback(
    async (ledgerId: string, amount: number, merchant: string): Promise<SwipeSmartCardRecommendation[]> => {
      try {
        return await fetchCardRecommendation(token, ledgerId, { amount, merchant })
      } catch {
        return []
      }
    },
    [token]
  )

  // 表單內直接新增分類/標籤(需求 #10,Phase 11):建在目前寫入帳本
  // (`txWriteLedgerId`,跟這筆交易本身走同一條 ledger-scoped 寫入路徑)。
  // 成功後樂觀地把新行 append 進 `txDictionaryCategories`/`txDictionaryTags`
  // (不等下一輪全量 fetch),讓 CategorySelector/TagSelector 立刻能反查到
  // 剛建立的這筆、也讓其它交易接下來能直接選用。
  const onCreateTxCategory = async (
    name: string,
    kind: 'expense' | 'income'
  ): Promise<WorkspaceCategory | null> => {
    const ledgerId = txWriteLedgerId.trim()
    if (!ledgerId) {
      setErrorNotice(t('shell.selectLedgerFirst'))
      return null
    }
    try {
      const res = await retryOnConflict(ledgerId, (base) =>
        createCategory(token, ledgerId, base, {
          name,
          kind,
          level: 1,
          sort_order: null,
          icon: null,
          icon_type: null,
          custom_icon_path: null,
          icon_cloud_file_id: null,
          icon_cloud_sha256: null,
          parent_name: null
        })
      )
      const created: WorkspaceCategory = {
        id: res.entity_id || '',
        name,
        kind,
        level: 1,
        sort_order: null,
        icon: null,
        icon_type: null,
        parent_name: null,
        last_change_id: res.new_change_id,
        ledger_id: ledgerId,
        ledger_name: null,
        created_by_user_id: null,
        created_by_email: null
      }
      setTxDictionaryCategories((prev) => [...prev, created])
      return created
    } catch (err) {
      setErrorNotice(renderError(err))
      return null
    }
  }

  const onCreateTxTag = async (name: string): Promise<{ id: string; name: string } | null> => {
    const ledgerId = txWriteLedgerId.trim()
    if (!ledgerId) {
      setErrorNotice(t('shell.selectLedgerFirst'))
      return null
    }
    try {
      const res = await retryOnConflict(ledgerId, (base) =>
        createTag(token, ledgerId, base, { name, color: pickRandomTagColor() })
      )
      const created = { id: res.entity_id || '', name }
      setTxDictionaryTags((prev) => [
        ...prev,
        { ...created, color: null, last_change_id: res.new_change_id }
      ])
      return created
    } catch (err) {
      setErrorNotice(renderError(err))
      return null
    }
  }

  // 專案(Phase 13,docs/PH13_PROJECT_SD.md):跟 onCreateTxTag 同款模式,
  // owner-only 寫入(`_OWNER_ONLY_ROLES`),只在 `txCanManageProjects` 時
  // 由 ProjectSelector 顯示「新增」入口(見下方 TransactionsPanel props)。
  const onCreateTxProject = async (name: string): Promise<ReadProject | null> => {
    const ledgerId = txWriteLedgerId.trim()
    if (!ledgerId) {
      setErrorNotice(t('shell.selectLedgerFirst'))
      return null
    }
    try {
      const res = await retryOnConflict(ledgerId, (base) =>
        createProject(token, ledgerId, base, { name })
      )
      const created: ReadProject = {
        id: res.entity_id || '',
        name,
        period_type: 'monthly',
        carryover_enabled: false,
        visible_on_home: true,
        enabled: true,
        sort_order: 0,
        income_included_in_budget: false,
        daily_budget_enabled: false,
        spent: 0,
        status: 'ok',
        last_change_id: res.new_change_id,
        ledger_id: ledgerId,
        ledger_name: null
      }
      setTxDictionaryProjects((prev) => [...prev, created])
      return created
    } catch (err) {
      setErrorNotice(renderError(err))
      return null
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

  // 快速日期筛选(2026-08-04 使用者反饋):今日(默认)/七天内/一个月内/全部,
  // 免得每次都要开 filter dialog 手动选日期。跟 dialog 里的日期字段共用同一份
  // txFilterApplied/txFilterDraft 状态,点其中一个会互相同步。
  const txQuickDatePresets = [
    { key: 'today' as const, label: t('transactions.quickDate.today'), range: todayRange() },
    { key: 'last7' as const, label: t('transactions.quickDate.last7Days'), range: last7DaysRange() },
    { key: 'last1m' as const, label: t('transactions.quickDate.last1Month'), range: last1MonthRange() },
  ]
  const applyTxQuickDateRange = (range: { dateFrom: string; dateTo: string } | null) => {
    const nextDateFrom = range?.dateFrom ?? ''
    const nextDateTo = range?.dateTo ?? ''
    setTxFilterApplied((prev) => ({ ...prev, dateFrom: nextDateFrom, dateTo: nextDateTo }))
    setTxFilterDraft((prev) => ({ ...prev, dateFrom: nextDateFrom, dateTo: nextDateTo }))
    setTxPage(1)
  }

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
    // 手續費/折扣(2026-08 使用者需求):前端先算好即時預覽/離線送出用的
    // 總額,server 端仍會依 base_amount/fee_amount/discount_amount 重新算
    // 一次當最終權威(見 write/_shared.py::_normalize_fee_discount_amount)。
    // 轉帳手續費/折損(2026-08-29):discount_amount 對 transfer 是獨立的
    // `discount_enabled` 開關(轉入側折損),跟 expense/income 共用
    // `fee_enabled` 同時控制手續費+折扣兩個輸入的既有行為不同。
    const isTransferForFeeDiscount = txForm.tx_type === 'transfer'
    const feeNum = txForm.fee_enabled ? Number(txForm.fee_amount) || 0 : 0
    const discountNum = isTransferForFeeDiscount
      ? (txForm.discount_enabled ? Number(txForm.discount_amount) || 0 : 0)
      : (txForm.fee_enabled ? Number(txForm.discount_amount) || 0 : 0)
    // transfer 不套用 base±fee∓discount 公式(amount 維持使用者輸入的原始
    // 轉出金額),這裡只給 expense/income 算「總額」用,transfer 場景下面
    // 组装 finalAmountNum 时不會用到 totalAmountNum。
    const totalAmountNum = isTransferForFeeDiscount
      ? amountNum
      : computeTxTotalAmount(txForm.tx_type, amountNum, feeNum, discountNum)
    // 非转账交易必须选分类(transfer 自动归虚拟"转账"分类,server 处理)。
    // mobile 端 transaction_editor_page 也强制必选,跨端一致避免 ungrouped tx
    // 污染分类统计。拆帳(§2.4)时不看单一 category,改看 splits。退款交易
    // (refund_of_id 非空)留空分类时 server 会自动归到自建的「退款」分类
    // (`ensure_refund_category`,同 `ensure_reward_category` 的既有模式),
    // 不强制用户手动选。
    if (
      txForm.tx_type !== 'transfer' &&
      !txForm.split_enabled &&
      !txForm.refund_of_id.trim() &&
      !txForm.category_name.trim()
    ) {
      setErrorNotice(t('transactions.error.categoryRequired'))
      return false
    }
    // 拆帳(§2.4):跟 server 端 _validate_tx_splits 同一套规则,前端先挡一遍。
    // 手續費/折扣開啟時,server 端驗證的 amount 是換算後的總額,這裡要用
    // totalAmountNum 對齊,不能用調整前的 amountNum。
    const splitError = validateTxSplits(txForm, totalAmountNum)
    if (splitError) {
      setErrorNotice(t(splitError))
      return false
    }
    if (txForm.split_enabled && (txForm.recurring_enabled || txForm.installment_enabled)) {
      setErrorNotice(t('transactions.error.splitRecurringNotSupported'))
      return false
    }
    if (txForm.split_enabled && txForm.refund_of_id.trim()) {
      setErrorNotice(t('transactions.error.splitRefundNotSupported'))
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
    // Phase 1.5(§2.12.1):分期付款期数校验,跟 InstallmentPlansPage 同规则。
    if (!txForm.editingId && txForm.installment_enabled) {
      const periodsNum = Number(txForm.installment_periods)
      if (!Number.isInteger(periodsNum) || periodsNum < 1 || periodsNum > 600) {
        setErrorNotice(t('installmentPlans.error.periodsInvalid'))
        return false
      }
    }
    // 帳戶必選(需求 #9,Phase 11):新建交易 或 使用者主動變更過帳戶欄位時
    // 強制必選;既有 mobile 匯入的無帳戶交易只改其它欄位(帳戶欄位維持
    // 打開表單當下的原樣)時放行,避免被迫補選帳戶才能存檔其它欄位。
    if (
      txForm.tx_type !== 'transfer' &&
      !txForm.account_name.trim() &&
      (!txForm.editingId || txForm.account_name.trim() !== txForm.original_account_name.trim())
    ) {
      setErrorNotice(t('transactions.error.accountRequired'))
      return false
    }

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

      // 跨幣別自動換算(2026-08):expense/income 把「入帳幣別」金額換算成
      // 使用者選的帳戶自己的幣別(不是帳本本位幣)——帳戶餘額口徑永遠是帳戶
      // 自身幣別。手續費/折扣、拆帳的每個分量都要跟著同一個匯率縮放,否則
      // server 端會用「未換算的分量」重新算出權威 amount,把這裡的換算悄悄
      // 蓋掉(見 write/_shared.py::_normalize_fee_discount_amount)。
      let finalAmountNum = totalAmountNum
      let finalBaseAmountNum = amountNum
      let finalFeeNum = feeNum
      let finalDiscountNum = discountNum
      let finalSplitsPayload = buildTxSplitsPayload(txForm)
      let accountCurrencyForFields = txFormCurrency
      if (!isTransfer) {
        const accountRow = txWriteAccounts.find(
          (row) => row.name.trim().toLowerCase() === accountName.toLowerCase()
        )
        const accountCurrency = (accountRow?.currency || txWriteLedgerCurrency).trim().toUpperCase()
        accountCurrencyForFields = accountCurrency
        if (accountCurrency !== txFormCurrency) {
          // baseAmountNum 用 amountNum(不含手續費/折扣的原始金額)——跟
          // TransactionsPanel.tsx::entryFxPreview 顯示給使用者看的換算基準
          // 一致(手續費/折扣是另外各自乘同一個 effectiveRate,不能反过来
          // 拿 totalAmountNum 当基准,否则手動輸入的換算後金額会跟畫面上
          // 預覽的數字對不上)。
          const effectiveRate = resolveEffectiveRate(
            txForm.fx_rate_override,
            txForm.fx_amount_override,
            amountNum,
            txFormCurrency,
            accountCurrency,
            txCurrencyRates,
            txWriteLedgerCurrency
          )
          if (effectiveRate == null) {
            setErrorNotice(t('transactions.error.rateMissing'))
            return false
          }
          finalAmountNum = totalAmountNum * effectiveRate
          finalBaseAmountNum = amountNum * effectiveRate
          finalFeeNum = feeNum * effectiveRate
          finalDiscountNum = discountNum * effectiveRate
          finalSplitsPayload = finalSplitsPayload.map((row) => ({
            ...row,
            amount: (row.amount || 0) * effectiveRate,
          }))
        }
      }

      // v30 多币种:共享 helper(手动 override > 自动源;编辑模式币种未变
      // 返回 null 不发字段 —— 金额变化由 server L14 按隐含汇率联动,避免
      // 「只改备注被今日汇率重算」的快照漂移;改回本位币显式发 base+amount)。
      // 拉不到汇率阻断保存(绝不静默 1:1,与 App L8 一致)。transfer 不带。
      // 注意:餵進去的 currency 是「所選帳戶的幣別」,不是入帳幣別(見上方
      // accountCurrencyForFields)——resolveCurrencyFields 的語意本來就是
      // 「amount 現在是哪個幣別,要不要折算成帳本本位幣」。
      let currencyFields: { currency_code?: string; native_amount?: number } = {}
      if (!isTransfer) {
        try {
          const resolved = await resolveCurrencyFields({
            token,
            ledgerBase: txWriteLedgerCurrency,
            currency: accountCurrencyForFields,
            amount: finalAmountNum,
            originalCurrency: txForm.editingId ? txForm.original_currency : undefined
          })
          if (resolved) currencyFields = resolved
        } catch {
          setErrorNotice(t('transactions.error.rateMissing'))
          return false
        }
      }

      // 跨幣別轉帳(2026-08):轉出/轉入帳戶幣別不同時要算出 to_amount(轉入
      // 帳戶自身幣別的金額),否則後端 write/_shared.py::
      // _assert_transfer_to_amount_valid 會擋下這筆。幣別相同就不送這個
      // 欄位(維持既有「同幣種轉帳只有一個 amount」行為)。
      let transferToAmount: number | undefined
      if (isTransfer) {
        const fromAccountRow = txWriteAccounts.find(
          (row) => row.name.trim().toLowerCase() === fromAccountName.toLowerCase()
        )
        const toAccountRow = txWriteAccounts.find(
          (row) => row.name.trim().toLowerCase() === toAccountName.toLowerCase()
        )
        const fromCurrency = (fromAccountRow?.currency || txWriteLedgerCurrency).trim().toUpperCase()
        const toCurrency = (toAccountRow?.currency || txWriteLedgerCurrency).trim().toUpperCase()
        if (fromAccountRow && toAccountRow && fromCurrency !== toCurrency) {
          const effectiveRate = resolveEffectiveRate(
            txForm.fx_rate_override,
            txForm.fx_amount_override,
            totalAmountNum,
            fromCurrency,
            toCurrency,
            txCurrencyRates,
            txWriteLedgerCurrency
          )
          if (effectiveRate == null) {
            setErrorNotice(t('transactions.fx.rateMissing'))
            return false
          }
          transferToAmount = totalAmountNum * effectiveRate
        }
        // 跨幣別轉帳(2026-09-06 使用者反饋):`native_amount` 是「轉出方金額
        // 折算帳本本位幣」的快照,跟 `to_amount`(轉入帳戶自身幣別)是兩個
        // 獨立概念——信用卡群組合併帳單(`compute_group_billing`)拿它當
        // 「已繳金額」的幣別基準(子卡幣別可能跟帳本本位幣不同,不能直接用
        // `to_amount` 加總,見 src/services/credit_card_billing.py 同批修正)。
        // 只看轉出帳戶幣別跟帳本本位幣是否一致,跟轉入帳戶自己的幣別無關
        // (就算轉入帳戶也是外幣,這裡仍是折「轉出方」到帳本本位幣)。故意用
        // 市場自動匯率(不吃 fx_rate_override/fx_amount_override 這組使用者
        // 手動覆蓋——那組覆蓋的是轉出/轉入之間的匯率,語意上是另一段換算)。
        if (fromAccountRow && fromCurrency !== txWriteLedgerCurrency.trim().toUpperCase()) {
          const native = convertBetween(
            totalAmountNum,
            fromCurrency,
            txWriteLedgerCurrency,
            txCurrencyRates,
            txWriteLedgerCurrency
          )
          if (native != null) {
            currencyFields = { ...currencyFields, native_amount: native }
          }
        }
      }

      const resolvedAccountId = isTransfer ? null : accountByName.get(accountName.toLowerCase()) || null
      const resolvedCategoryId = isTransfer
        ? null
        : categoryByKey.get(`${categoryKind}:${categoryName.toLowerCase()}`) || null

      // Phase 1.5(§2.12.1):分期付款走独立的 createInstallmentPlan,不是
      // TxPayload 的一个字段 —— 欄位差異太大(期数/攤還方式等),沿用既有
      // 「分期计划管理页」用的同一个 API。
      const installmentPayload = !txForm.editingId
        ? buildInstallmentPlanPayload(txForm, { account_id: resolvedAccountId, category_id: resolvedCategoryId })
        : null

      const payload = {
        tx_type: txForm.tx_type,
        amount: finalAmountNum,
        // 手續費/折扣(2026-08 使用者需求):只在使用者有開啟這個功能時才送,
        // 沒開啟(一般交易)完全不影響 payload,server 端維持既有行為。
        // 轉帳手續費/折損(2026-08-29):transfer 的 fee_enabled/
        // discount_enabled 各自獨立控制轉出/轉入兩側,不像 expense/income
        // 那樣綁在一起送;base_amount 對 transfer 不使用,一律不送(留給
        // server 端固定為 null,見 write/_shared.py::
        // _normalize_fee_discount_amount 的 transfer 分支)。
        ...(isTransferForFeeDiscount
          ? {
              ...(txForm.fee_enabled
                ? { fee_amount: finalFeeNum, fee_label: txForm.fee_label.trim() || null }
                : {}),
              ...(txForm.discount_enabled
                ? { discount_amount: finalDiscountNum, discount_label: txForm.discount_label.trim() || null }
                : {}),
            }
          : txForm.fee_enabled
            ? {
                base_amount: finalBaseAmountNum,
                fee_amount: finalFeeNum,
                fee_label: txForm.fee_label.trim() || null,
                discount_amount: finalDiscountNum,
                discount_label: txForm.discount_label.trim() || null,
              }
            : {}),
        happened_at: txForm.happened_at || new Date().toISOString(),
        // 延後入帳(§2.10 Phase 5):進階/選填欄位,''=未設定 → 顯式傳 null
        // (create 場景等同不傳;update 場景顯式清空既有值,對齊 debt_id/
        // refund_of_id 同款"總是送計算後的值"既有慣例,不做額外 diff)。
        deferred_posting_at: txForm.deferred_posting_at
          ? dateInputToIsoUtc(txForm.deferred_posting_at)
          : null,
        note: txForm.note || null,
        merchant: txForm.merchant || null,
        category_name: isTransfer || txForm.split_enabled ? null : categoryName || null,
        category_kind: isTransfer ? null : categoryKind || null,
        category_id: isTransfer || txForm.split_enabled ? null : resolvedCategoryId,
        account_name: isTransfer ? null : accountName || null,
        account_id: resolvedAccountId,
        from_account_name: isTransfer ? fromAccountName || null : null,
        from_account_id: isTransfer ? accountByName.get(fromAccountName.toLowerCase()) || null : null,
        to_account_name: isTransfer ? toAccountName || null : null,
        to_account_id: isTransfer ? accountByName.get(toAccountName.toLowerCase()) || null : null,
        // 跨幣別轉帳(2026-08):同幣種轉帳(transferToAmount undefined)不送
        // 這個欄位,server 端 COALESCE(to_amount, amount) 回退,行為不變。
        ...(isTransfer && transferToAmount !== undefined ? { to_amount: transferToAmount } : {}),
        tags: txForm.tags.length > 0 ? txForm.tags : null,
        tag_ids: txTagIds.length > 0 ? txTagIds : null,
        attachments: txForm.attachments.length > 0 ? txForm.attachments : null,
        // §三 标记按 type 条件落库:转账两者都 false;收入只允许 stats;支出两者都允许。
        exclude_from_stats: isTransfer ? false : txForm.exclude_from_stats,
        exclude_from_budget: txForm.tx_type === 'expense' ? txForm.exclude_from_budget : false,
        // 退款(§2.6):income/expense 都能是退款交易(income 也能被退款),
        // 只有 transfer 没有退款语意;切到 transfer 时发 null 显式清掉关联
        // (server:值为 null/'' 时 pop 掉 refundOfId)。
        refund_of_id: txForm.tx_type !== 'transfer' ? txForm.refund_of_id.trim() || null : null,
        // 借還款追蹤(§2.5 體驗補強):跟 refund_of_id 同款语意,expense/income
        // 都能掛欠款,transfer 没有语意,切到 transfer 时发 null 显式清掉关联。
        // '__new__'(體驗補強第二輪:順便建新欠款)不是真的 debt syncId,
        // 这笔交易是欠款起点、不是还款,不设 debt_id(新欠款单独在下面
        // createDebt 建,见 shouldCreateNewDebt)。
        debt_id:
          txForm.tx_type !== 'transfer' && txForm.debt_id !== '__new__'
            ? txForm.debt_id.trim() || null
            : null,
        // 專案(Phase 13,docs/PH13_PROJECT_SD.md):跟 debt_id 同款语意,
        // 只支援 expense/income,transfer 时发 null 显式清掉关联。
        project_id: txForm.tx_type !== 'transfer' ? txForm.project_id.trim() || null : null,
        // 信用卡紅利回饋(§2.9.5,2026-08-06 改版):跟 debt_id 同款语意,
        // 只有 expense 有意义(回饋是消費行为),transfer/income 一律清空。
        reward_rule_ids: txForm.tx_type === 'expense' ? txForm.reward_rule_ids : [],
        // Phase 1.5(§2.12.2):建交易当下顺便设成週期性收支起点,只在新建
        // 时生效(server 也只认 create 路径的这个字段)。
        recurring: !txForm.editingId ? buildRecurringInlinePayload(txForm) : null,
        // 拆帳(§2.4):disabled → 空数组(create 场景等同"没有 splits";update
        // 场景显式清空既有 splits,回到单一 category)。
        splits: finalSplitsPayload,
        ...currencyFields
      }
      // eslint-disable-next-line no-console
      console.info('[tx-save] request', {
        editingId: txForm.editingId,
        ledgerId,
        payload_tags: payload.tags,
        payload_account_name: payload.account_name,
        payload_account_id: payload.account_id
      })
      const res = await retryOnConflict(ledgerId, (base) => {
        if (installmentPayload) {
          return createInstallmentPlan(token, ledgerId, base, installmentPayload)
        }
        // Phase 24(需求 #5):週期性收支差異化編輯——只送目標端點支援的窄
        // 欄位子集(見 WriteRecurringOccurrenceUpdateRequest /
        // WriteRecurringUpdateFromRequest),不是整個 payload。表單裡其它
        // 這兩支端點不接受的欄位(回饋項目/標籤單筆編輯等)不會被存到,是
        // 既有的已知限制,同 GlobalEditDialogs.tsx::handleSaveTx。
        if (txForm.editingId && recurringEditContext && recurringEditContext.mode === 'single') {
          const occurrencePayload: RecurringOccurrenceUpdatePayload = {
            amount: finalAmountNum,
            note: payload.note ?? undefined,
            category_id: payload.category_id ?? undefined,
            account_id: payload.account_id ?? undefined,
            happened_at: payload.happened_at,
          }
          return updateRecurringOccurrence(
            token, ledgerId, recurringEditContext.ruleId, txForm.editingId, base, occurrencePayload,
          )
        }
        if (txForm.editingId && recurringEditContext && recurringEditContext.mode === 'future') {
          const updateFromPayload: RecurringUpdateFromPayload = {
            tx_type: txForm.tx_type,
            amount: finalAmountNum,
            note: payload.note ?? undefined,
            category_id: isTransfer ? undefined : payload.category_id ?? undefined,
            account_id: isTransfer ? undefined : payload.account_id ?? undefined,
            from_account_id: isTransfer ? payload.from_account_id ?? undefined : undefined,
            to_account_id: isTransfer ? payload.to_account_id ?? undefined : undefined,
            merchant: payload.merchant ?? undefined,
            project_id: payload.project_id ?? undefined,
            tag_ids: txTagIds,
          }
          return updateRecurringRuleFrom(
            token, ledgerId, recurringEditContext.ruleId, txForm.editingId, base, updateFromPayload,
          )
        }
        if (txForm.editingId) {
          return updateTransaction(token, ledgerId, txForm.editingId, base, payload)
        }
        return createTransaction(token, ledgerId, base, payload)
      })
      // eslint-disable-next-line no-console
      console.info('[tx-save] response', {
        entity_id: res.entity_id,
        new_change_id: res.new_change_id,
        server_timestamp: res.server_timestamp
      })
      if (activeLedgerId === ledgerId) {
        setBaseChangeId(res.new_change_id)
      }
      // 借還款追蹤(體驗補強第二輪):这笔交易顺便建一笔新欠款。是这笔交易
      // 的旁支效果,不是它本身的一个字段,所以交易保存成功后单独发一次
      // createDebt;失败只提示、不影响已经保存成功的交易(没有整体回滚)。
      if (!isTransfer && !installmentPayload && txForm.debt_id === '__new__') {
        const newDebtCounterparty = txForm.new_debt_counterparty_name.trim()
        if (newDebtCounterparty) {
          try {
            await retryOnConflict(ledgerId, (base) =>
              createDebt(token, ledgerId, base, {
                direction: txForm.tx_type === 'income' ? 'payable' : 'receivable',
                counterparty_name: newDebtCounterparty,
                principal_amount: totalAmountNum,
                due_at: txForm.new_debt_due_at
                  ? new Date(`${txForm.new_debt_due_at}T00:00:00Z`).toISOString()
                  : null,
                note: null
              })
            )
            fetchReadDebts(token, ledgerId)
              .then(setTxDictionaryDebts)
              .catch(() => undefined)
          } catch {
            setErrorNotice(t('transactions.error.debtCreateFailed'))
          }
        }
      }
      const editingTxId = txForm.editingId
      setTxForm(txDefaults())
      setRecurringEditContext(null)
      const refreshLedger = activeLedgerId || ledgerId
      await refreshSectionData(refreshLedger, 'transactions')
      // 新增/編輯交易同樣會改變帳戶餘額,理由同 onDeleteTransaction——
      // txDictionaryAccounts 是表單帳戶選單自己那份字典,refreshSectionData
      // 刷的是另一份 accounts 狀態,不刷這行的話選單餘額不會反映剛存的交易。
      await loadTxDictionaries()
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

  // Phase 24(需求 #5):把「打開編輯表單、把 tx 欄位塞進 txForm」這段邏輯
  // 抽成獨立函式——原本是 <TransactionsPanel onEdit> 的 inline callback,
  // 現在週期性收支的「修改此記錄」/「修改連同未來週期」選完之後也要能呼叫
  // 同一段邏輯(見下面 onEdit callback 與 handleRecurringEditChoice)。
  const openTxEditForm = (tx: WorkspaceTransaction) => {
    setTxWriteLedgerId(tx.ledger_id || txWriteLedgerOptions[0]?.ledger_id || '')
    setTxDialogOpen(true)
    setTxForm({
      ...txDefaults(),
      editingId: tx.id,
      editingOwnerUserId: tx.created_by_user_id || '',
      // §2.10 Phase 5:同 GlobalEditDialogs.tsx —— 'adjustment'
      // 只由餘額調整語意化端點產生,一般編輯表單收窄成 'expense'
      // 顯示(行內編輯按鈕已經對這種交易灰掉,這裡是防禦性 cast,
      // 不會真的被使用者從這條路径觸發)。
      tx_type: (tx.tx_type === 'adjustment' ? 'expense' : tx.tx_type) as TxForm['tx_type'],
      // 手續費/折扣(2026-08 使用者需求):金額欄位回填
      // base_amount(原始金額),沒用過這個功能時 fallback 回
      // amount,對既有交易的顯示完全沒有影響。transfer 的 base_amount
      // 一律是 null,這裡自然 fallback 回 amount,行為正確不需要特判。
      amount: String(tx.base_amount ?? tx.amount),
      // 轉帳手續費/折損(2026-08-29):transfer 用兩個獨立開關(轉出側
      // fee_enabled、轉入側 discount_enabled);expense/income 維持既有的
      // 「兩個分量共用 fee_enabled 一個開關」行為。
      fee_enabled:
        tx.tx_type === 'transfer'
          ? tx.fee_amount != null
          : tx.fee_amount != null || tx.discount_amount != null,
      fee_amount: tx.fee_amount != null ? String(tx.fee_amount) : '',
      fee_label: tx.fee_label || '',
      discount_amount: tx.discount_amount != null ? String(tx.discount_amount) : '',
      discount_label: tx.discount_label || '',
      discount_enabled: tx.tx_type === 'transfer' ? tx.discount_amount != null : false,
      happened_at: tx.happened_at,
      deferred_posting_at: tx.deferred_posting_at ? isoToDateInputUtc(tx.deferred_posting_at) : '',
      note: tx.note || '',
      merchant: tx.merchant || '',
      category_name: tx.category_name || '',
      category_kind: (tx.category_kind as TxForm['category_kind']) || 'expense',
      account_name: tx.account_name || '',
      original_account_name: tx.account_name || '',
      from_account_name: tx.from_account_name || '',
      to_account_name: tx.to_account_name || '',
      // 转帐「转入金额」(2026-09-18 使用者反馈:重开编辑表单会用当下市场
      // 汇率现算一个跟原存值不同的数字,使用者没注意到就按更新会静默把
      // to_amount 覆盖掉):必须显式回填这笔交易实际存的 to_amount,不能
      // 留空让 resolveEffectiveRate 退回现查匯率。同 GlobalEditDialogs.tsx。
      fx_amount_override:
        tx.tx_type === 'transfer' && tx.to_amount != null ? String(tx.to_amount) : '',
      currency: (tx.currency_code || '').toUpperCase() === txWriteLedgerCurrency
        ? ''
        : (tx.currency_code || '').toUpperCase(),
      original_currency: (tx.currency_code || '').toUpperCase() === txWriteLedgerCurrency
        ? ''
        : (tx.currency_code || '').toUpperCase(),
      tags:
        tx.tags_list && tx.tags_list.length > 0
          ? tx.tags_list
          : (tx.tags || '')
              .split(',')
              .map((value) => value.trim())
              .filter((value) => value.length > 0),
      attachments: normalizeAttachmentRefs(tx.attachments),
      exclude_from_stats: Boolean(tx.exclude_from_stats),
      exclude_from_budget: Boolean(tx.exclude_from_budget),
      refund_of_id: tx.refund_of_id || '',
      debt_id: tx.debt_id || '',
      project_id: tx.project_id || '',
      project_name: tx.project_name || '',
      reward_rule_ids: tx.reward_rule_ids || [],
      // 拆帳(§2.4):回显既有 splits,让用户能直接在明细页编辑分类拆分。
      split_enabled: Boolean(tx.has_splits) && (tx.splits?.length || 0) >= 2,
      splits: Boolean(tx.has_splits)
        ? (tx.splits || []).map((s) => ({
            category_id: s.category_id || '',
            category_name: s.category_name || '',
            amount: String(s.amount),
            note: s.note || ''
          }))
        : []
    })
  }

  // Phase 24:兩選一之後打開同一個編輯表單(openTxEditForm),但先記住要
  // 分流到哪支 API——onSaveTransaction 存檔時依 recurringEditContext 分流。
  const handleRecurringEditChoice = (mode: 'single' | 'future') => {
    const target = recurringEditChoice
    setRecurringEditChoice(null)
    if (!target || !target.recurring_rule_id) return
    setRecurringEditContext({ ruleId: target.recurring_rule_id, mode })
    openTxEditForm(target)
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
    // 刪除交易會改變帳戶餘額,但表單帳戶選單吃的是 txDictionaryAccounts
    // (跟上面 refreshSectionData 刷的 accounts 狀態是兩份不同的字典),
    // 沒有這行的話立刻新增/編輯交易時帳戶選單看到的餘額還是刪除前的舊值。
    await loadTxDictionaries()
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

  // ──────────────── 批量选择 ────────────────
  // 切账本 / 离开交易页 / 修改 filter 时清空 selection,避免选中态横跨上下文
  // 后用户操作错对象。dataset 变了再保留 selection 没意义。
  useEffect(() => {
    setSelectionMode(false)
    setSelectedTxIds(new Set())
    lastClickIndexRef.current = null
  }, [route.section, activeLedgerId])

  // 当前可见行(分页 / 当前 page),用于「全选当前页」+ 范围选 ⇧ + Click
  const visibleTxIds = useMemo(() => transactions.map((t) => t.id), [transactions])
  const allVisibleSelected =
    selectionMode &&
    visibleTxIds.length > 0 &&
    visibleTxIds.every((id) => selectedTxIds.has(id))

  const enterSelection = useCallback((seedId?: string) => {
    setSelectionMode(true)
    if (seedId) setSelectedTxIds(new Set([seedId]))
    else setSelectedTxIds(new Set())
    lastClickIndexRef.current = null
  }, [])

  const exitSelection = useCallback(() => {
    setSelectionMode(false)
    setSelectedTxIds(new Set())
    lastClickIndexRef.current = null
  }, [])

  const handleToggleSelect = useCallback(
    (row: ReadTransaction, event: React.MouseEvent) => {
      const id = row.id
      const idx = visibleTxIds.indexOf(id)
      const isShift = event.shiftKey && lastClickIndexRef.current !== null && idx >= 0
      const isMeta = event.metaKey || event.ctrlKey

      setSelectedTxIds((prev) => {
        const next = new Set(prev)
        if (isShift) {
          // 范围选:从 lastClickIndex 到 idx,全部加入(GitHub / Gmail 风格)
          const start = Math.min(lastClickIndexRef.current!, idx)
          const end = Math.max(lastClickIndexRef.current!, idx)
          for (let i = start; i <= end; i++) {
            const vid = visibleTxIds[i]
            if (vid) next.add(vid)
          }
        } else if (isMeta) {
          // 增量切换 —— 仅当前行
          if (next.has(id)) next.delete(id)
          else next.add(id)
        } else {
          // 普通点击 → 切换当前行
          if (next.has(id)) next.delete(id)
          else next.add(id)
        }
        return next
      })
      if (idx >= 0 && !isShift) lastClickIndexRef.current = idx
    },
    [visibleTxIds],
  )

  const toggleSelectAllVisible = useCallback(() => {
    if (allVisibleSelected) {
      setSelectedTxIds((prev) => {
        const next = new Set(prev)
        for (const id of visibleTxIds) next.delete(id)
        return next
      })
    } else {
      setSelectedTxIds((prev) => {
        const next = new Set(prev)
        for (const id of visibleTxIds) next.add(id)
        return next
      })
    }
  }, [allVisibleSelected, visibleTxIds])

  // Esc 退出选择模式
  useEffect(() => {
    if (!selectionMode) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') exitSelection()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [selectionMode, exitSelection])

  const selectedTxList = useMemo(
    () => transactions.filter((t) => selectedTxIds.has(t.id)),
    [transactions, selectedTxIds],
  )
  // 已选合计金额:支出 - 收入(转账中性,不计入展示)。
  // 跨账户合计属账本维度 → 折本位币口径:native_amount ?? amount(多币种账本
  // 裸加原币会错;单币种 native===amount 结果不变)。
  const selectedTotalAmount = useMemo(() => {
    let sum = 0
    for (const t of selectedTxList) {
      const amt = Number(t.native_amount ?? t.amount) || 0
      if (t.tx_type === 'expense') sum -= amt
      else if (t.tx_type === 'income') sum += amt
    }
    return sum
  }, [selectedTxList])

  const handleBatchExport = useCallback(async () => {
    if (!activeLedgerId) return
    if (selectedTxIds.size === 0) return
    setBatchSaving(true)
    try {
      await downloadWorkspaceTransactionsCsv(token, {
        ledgerId: activeLedgerId,
        txIds: Array.from(selectedTxIds),
        lang: locale,
      })
      toast.success(t('export.csv.success'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setBatchSaving(false)
    }
  }, [activeLedgerId, selectedTxIds, token, locale, t, toast])

  const handleBatchDeleteConfirm = useCallback(async () => {
    if (!activeLedgerId) return
    if (selectedTxIds.size === 0) return
    setBatchSaving(true)
    try {
      const result = await batchDeleteTransactions(token, {
        ledgerId: activeLedgerId,
        txIds: Array.from(selectedTxIds),
      })
      const deleted = result.deleted_tx_ids.length
      const installmentLinked = result.failed.filter(
        (f) => f.reason === 'installment_linked',
      ).length
      const otherFailed = result.failed.length - installmentLinked
      if (installmentLinked > 0) {
        toast.error(
          t('txBatch.deleteResult.installmentLinked', {
            deleted,
            count: installmentLinked,
          }) as string,
        )
      } else if (otherFailed > 0) {
        toast.error(
          t('txBatch.deleteResult.partial', {
            deleted,
            failed: otherFailed,
          }) as string,
        )
      } else {
        toast.success(t('txBatch.deleteResult.ok', { count: deleted }))
      }
      setBatchDeleteOpen(false)
      exitSelection()
      void onRefresh()
      // 同 onDeleteTransaction 理由:onRefresh 刷的是另一份 accounts 狀態,
      // 不會動到表單帳戶選單吃的 txDictionaryAccounts,漏刷的話批量刪除
      // 後立刻新增交易,選單餘額還是刪除前的舊值。
      void loadTxDictionaries()
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setBatchSaving(false)
    }
  }, [activeLedgerId, selectedTxIds, token, t, toast, exitSelection])
  // ──────────────────────────────────────────

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
                {/* 「批量选择」入口 — 桌面端独占,小屏完全不渲染。点击进选择
                    模式,toolbar 出现,行首加 checkbox。设计:.docs/web-tx-batch-actions.md */}
                {canWriteTx && !selectionMode ? (
                  <Tooltip content={t('txBatch.entryTooltip')}>
                    <Button
                      aria-label={t('txBatch.entryTooltip') as string}
                      className="hidden h-9 md:inline-flex"
                      size="icon"
                      variant="outline"
                      onClick={() => enterSelection()}
                    >
                      <CheckSquare className="h-4 w-4" />
                    </Button>
                  </Tooltip>
                ) : null}
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
                      setRecurringEditContext(null)
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
              {/* 快速日期筛选(2026-08-04 使用者反饋):今日(默认)/七天内/
                  一个月内/全部,免得每次都要开 filter dialog 手动选日期。 */}
              <div className="flex flex-wrap items-center gap-1.5">
                {txQuickDatePresets.map((preset) => {
                  const active =
                    txFilterApplied.dateFrom === preset.range.dateFrom &&
                    txFilterApplied.dateTo === preset.range.dateTo
                  return (
                    <button
                      key={preset.key}
                      type="button"
                      aria-pressed={active}
                      onClick={() => applyTxQuickDateRange(preset.range)}
                      className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                        active
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-muted text-muted-foreground hover:bg-muted/70'
                      }`}
                    >
                      {preset.label}
                    </button>
                  )
                })}
                <button
                  type="button"
                  aria-pressed={!txFilterApplied.dateFrom && !txFilterApplied.dateTo}
                  onClick={() => applyTxQuickDateRange(null)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    !txFilterApplied.dateFrom && !txFilterApplied.dateTo
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-muted/70'
                  }`}
                >
                  {t('transactions.quickDate.all')}
                </button>
              </div>
              {selectionMode ? (
                <SelectionToolbar
                  selectedCount={selectedTxIds.size}
                  totalCount={txTotal}
                  allVisibleSelected={allVisibleSelected}
                  saving={batchSaving}
                  onToggleAllVisible={toggleSelectAllVisible}
                  onDelete={() => setBatchDeleteOpen(true)}
                  onExport={handleBatchExport}
                  onExit={exitSelection}
                />
              ) : null}
              <TransactionsPanel
                baseCurrency={txWriteLedgerCurrency}
                currencyRates={txCurrencyRates}
                noteDisplayMode={profileMe?.appearance?.note_display_mode ?? 'category'}
                selectionMode={selectionMode}
                selectedIds={selectedTxIds}
                onToggleSelect={handleToggleSelect}
                form={txForm}
                rows={transactions}
                total={txTotal}
                page={txPage}
                pageSize={txPageSize}
                accounts={txWriteAccounts}
                categories={txWriteCategories}
                iconPreviewUrlByFileId={categoryIconPreviewByFileId}
                tags={txWriteTags}
                debts={txDictionaryDebts}
                canCreateDebt={txCanCreateDebt}
                projects={txDictionaryProjects}
                onCreateProject={txCanManageProjects ? onCreateTxProject : undefined}
                rewardRules={txFormRewardRules}
                categorySuggestions={txFormCategorySuggestions}
                fetchAccountSuggestions={txFetchAccountSuggestions}
                onCreateCategory={onCreateTxCategory}
                onCreateTag={onCreateTxTag}
                fetchCardRecommendations={handleFetchCardRecommendations}
                ledgerOptions={txWriteLedgerOptions}
                writeLedgerId={txWriteLedgerId}
                onWriteLedgerIdChange={setTxWriteLedgerId}
                onPageChange={setTxPage}
                onPageSizeChange={(size) => {
                  setTxPageSize(size)
                  setTxPage(1)
                }}
                canWrite={Boolean(canWriteTx)}
                // §7 共享账本:tx 列表当前 ledger 若是共享账本,每行尾巴
                // 显示"XX 创建 · YY 编辑"chip(server 已注入 created_by_* +
                // last_edited_by_* 字段)。currentUserId 用来过滤"全是自己"
                // 的 tx 不显示 chip。
                showCreator={Boolean(txContextLedger?.is_shared)}
                currentUserId={profileMe?.user_id || null}
                dictionariesLoading={txDictionaryLoading}
                onFormChange={setTxForm}
                dialogOpen={txDialogOpen}
                onDialogOpenChange={setTxDialogOpen}
                onSave={onSaveTransaction}
                onReset={() => {
                  setTxForm(txDefaults())
                  setRecurringEditContext(null)
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
                  // Phase 24(需求 #5):週期性收支產生的交易——先問「修改此
                  // 記錄」還是「修改連同未來週期」,而不是直接開一般編輯
                  // 表單(比照 GlobalEntityDialogs.handleEditTx 的既有模式)。
                  if (tx.recurring_rule_id) {
                    setRecurringEditChoice(tx as WorkspaceTransaction)
                    return
                  }
                  setRecurringEditContext(null)
                  openTxEditForm(tx as WorkspaceTransaction)
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
              <BatchDeleteDialog
                open={batchDeleteOpen}
                count={selectedTxIds.size}
                totalAmount={selectedTotalAmount}
                saving={batchSaving}
                onConfirm={handleBatchDeleteConfirm}
                onClose={() => setBatchDeleteOpen(false)}
              />
              <RecurringEditChoiceDialog
                open={recurringEditChoice !== null}
                onCancel={() => setRecurringEditChoice(null)}
                onChooseEditThis={() => handleRecurringEditChoice('single')}
                onChooseEditFuture={() => handleRecurringEditChoice('future')}
              />
              {txStockTrade && (
                <StockTradeDialog
                  state={txStockTrade}
                  accounts={txDictionaryAccounts}
                  holdings={txStockHoldings}
                  activeLedgerId={txContextLedgerId}
                  onClose={() =>
                    closeTxStockTrade({
                      clearAccount: txStockTrade.initial?.type === 'buy' ? 'to' : 'from',
                    })
                  }
                  onSaved={async () => {
                    const clearAccount = txStockTrade.initial?.type === 'buy' ? 'to' : 'from'
                    closeTxStockTrade({ clearAccount })
                    setTxDialogOpen(false)
                    await onRefresh()
                  }}
                />
              )}
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
                <DatePicker
                  value={txFilterDraft.dateFrom}
                  onChange={(next) =>
                    setTxFilterDraft((prev) => ({ ...prev, dateFrom: next }))
                  }
                  clearable
                />
                {txFilterDraft.dateFrom && !txFilterDraft.dateTo ? (
                  <p className="text-xs text-muted-foreground">
                    {t('shell.filter.dateFromOnlyHint')}
                  </p>
                ) : null}
              </div>
              <div className="space-y-1">
                <Label>{t('shell.filter.dateTo')}</Label>
                <DatePicker
                  value={txFilterDraft.dateTo}
                  onChange={(next) =>
                    setTxFilterDraft((prev) => ({ ...prev, dateTo: next }))
                  }
                  clearable
                />
                {txFilterDraft.dateTo && !txFilterDraft.dateFrom ? (
                  <p className="text-xs text-muted-foreground">
                    {t('shell.filter.dateToOnlyHint')}
                  </p>
                ) : null}
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
