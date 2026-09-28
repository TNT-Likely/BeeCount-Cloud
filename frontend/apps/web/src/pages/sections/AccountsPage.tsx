import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  createAccount,
  deleteAccount,
  fetchExchangeRateOverrides,
  fetchExchangeRates,
  fetchNetWorthHistory,
  fetchWorkspaceAccounts,
  fetchWorkspaceDebtTotals,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  reorderAccounts,
  updateAccount,
  uploadAccountAvatar,
  type ExchangeRateOverride,
  type ExchangeRatesResponse,
  type NetWorthHistory,
  type ReadAccount,
  type WorkspaceAccount,
  type WorkspaceDebtCurrencyTotal,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT,
  useToast,
} from '@beecount/ui'
import {
  AccountsPanel,
  Amount,
  AssetsCompositionMini,
  ConfirmDialog,
  CurrencyAssetCard,
  accountDefaults,
  computeCurrencySummary,
  computeTypeGroups,
  effectiveRateToBase,
  mergeGroupsToBase,
  splitByCurrency,
  type AccountForm,
  type CurrencyBucket,
} from '@beecount/web-features'

import { NetWorthTrend } from '../../components/dashboard/NetWorthTrend'
import { InvestmentValueCard } from '../../components/dashboard/InvestmentValueCard'
import { ASSET_VIEW_KEY, type AssetView } from '../../lib/assetViewPrefs'
import { routePath } from '../../state/router'
import { dispatchOpenDetailAccount, onOpenEditAccount } from '../../lib/txDialogEvents'
import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

const ACCOUNT_DETAIL_PAGE_SIZE = 20

// 资产汇总卡内「构成 / 走势」tab 的设备级持久化(key/类型见 assetViewPrefs)。
// 默认 'composition'(资产页更看重当下构成,走势放第二个 tab)。
function readTrendOrComposition(): AssetView {
  try {
    return localStorage.getItem(ASSET_VIEW_KEY) === 'trend' ? 'trend' : 'composition'
  } catch {
    return 'composition'
  }
}

/**
 * 账户 / 资产页 —— 账户列表 + CRUD(无 delete,web 只支持创建/编辑)
 * + 账户详情 dialog(点卡片弹出该账户的交易列表,无限滚动)。
 *
 * tags 独立 fetch 一份,只为 AccountDetailDialog 里 TransactionList 渲染
 * tag chip 用 —— 不跟其它 page 共享,每次进入该页现拉。
 *
 * 已知回归:AccountDetailDialog 的附件预览(resolveAttachmentPreviewUrl /
 * onPreviewAttachment)本轮留空,预览功能待 "附件预览共享 hook" 独立 task。
 */
export function AccountsPage() {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const { token, profileMe } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const { previewMap: avatarPreviewByFileId, ensureLoadedMany } = useAttachmentCache()

  const base = profileMe?.primary_currency || ''

  // 主要数据走 PageDataCache —— 切走再切回来立刻显示上次的值,不闪烁。
  // rows 用 WorkspaceAccount(包含 tx_count / balance 等聚合字段),删除前需要
  // 看 tx_count 决定是否提示用户(对齐 mobile account_edit_page._delete)。
  const [rows, setRows] = usePageCache<WorkspaceAccount[]>('accounts:rows', [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('accounts:tags', [])
  // 欠款/應收(§淨資產卡同口徑,見 WorkspaceDebtCurrencyTotal 註解):跟
  // netWorthHistory 一样是全局资产(debt 为 ledger-scoped 但净资产卡不按
  // 当前账本过滤),缓存键不带 activeLedgerId。任一失败置 []，不阻塞账户列表。
  const [debtTotals, setDebtTotals] = usePageCache<WorkspaceDebtCurrencyTotal[]>(
    'accounts:debtTotals',
    [],
  )
  const [form, setForm] = useState<AccountForm>(accountDefaults())
  // 删除前的待确认账户。null = 无 pending。WorkspaceAccount 带 tx_count 字段,
  // confirm dialog 直接读它,不再发额外请求。
  const [pendingDelete, setPendingDelete] = useState<WorkspaceAccount | null>(null)
  // 帳戶級聯刪除(2026-08 新增):tx_count > 0 時需要兩段式確認,這個旗標
  // 標記是否已經按過第一段「繼續」。false = 顯示第一段警示;true = 顯示
  // 第二段更強烈的最終確認。tx_count === 0 時完全不使用這個旗標(維持原本
  // 單步確認)。
  const [cascadeConfirming, setCascadeConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // 分币种明细 dialog(折算汇总卡的「详情」入口;单币种时该卡不出详情按钮)。
  const [detailOpen, setDetailOpen] = useState(false)

  // 资产汇总卡内「构成 / 走势」tab(见 ASSET_VIEW_KEY)。设备级持久化。
  const [trendOrComposition, setTrendOrComposition] = useState<AssetView>(
    () => readTrendOrComposition(),
  )
  useEffect(() => {
    try {
      localStorage.setItem(ASSET_VIEW_KEY, trendOrComposition)
    } catch {
      // private mode / 超配额忽略
    }
  }, [trendOrComposition])

  // 多币种折算(只读卡)。主币种存在且账户币种 ≥2 种时,并行拉汇率 + 手动 override,
  // 任一失败置 null 不阻塞账户列表。单币种 / 无主币种则不渲染卡(零变化)。
  // key 带 base 维度:切换主币种后不会复用旧 base 的汇率缓存。
  const [rates, setRates] = usePageCache<ExchangeRatesResponse | null>(
    base ? `accounts:rates:${base}` : 'accounts:rates:',
    null,
  )
  const [rateOverrides, setRateOverrides] = usePageCache<ExchangeRateOverride[]>(
    base ? `accounts:rateOverrides:${base}` : 'accounts:rateOverrides:',
    [],
  )

  // 净资产趋势(回算每月累积净值,对齐 App 资产页净资产卡的 sparkline)。
  // 按当前账本分桶:切账本读对应桶,失败置 null 不阻塞账户列表(同 rates/overrides 容错)。
  const [netWorthHistory, setNetWorthHistory] = usePageCache<NetWorthHistory | null>(
    // 净值趋势是全局资产(所有账本所有账户),与账本无关 —— 缓存键不带 activeLedgerId。
    'accounts:netWorthHistory',
    null,
  )

  // detail 弹窗已迁到 GlobalEntityDialogs(AppShell 顶层),本页只负责
  // dispatch openDetailAccount 事件,弹窗在全局渲染。

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    try {
      const tzOffsetMinutes = -new Date().getTimezoneOffset()
      const [accountRows, tagRows, history, debtRows] = await Promise.all([
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceTags(token, { limit: 500 }),
        // 净值趋势是全局资产(账户为 user-global、跨所有账本),绝不按当前账本过滤 ——
        // 否则切到无交易的账本时趋势会空,而净资产卡(全局账户余额)仍有数据,口径不一致。
        fetchNetWorthHistory(token, { tzOffsetMinutes }).catch(() => null),
        // 欠款/應收同样跨所有账本(不按 activeLedgerId 过滤),失败置 [] 不阻塞。
        fetchWorkspaceDebtTotals(token).catch(() => [] as WorkspaceDebtCurrencyTotal[]),
      ])
      setRows(accountRows)
      setTags(tagRows)
      setNetWorthHistory(history)
      setDebtTotals(debtRows)

      // 只有"主币种存在 + 账户/欠款涉及 ≥2 种币种"才需要折算卡。其余情况清空
      // 缓存,让卡不渲染。汇率请求任一失败置 null,不影响账户列表正常展示。
      // 欠款币种也要算进 distinct——否则「只有一笔外币欠款、没有对应币种账户」
      // 时不会去拉汇率,债务折算永远因缺汇率被剔除(见上方 converted 里的
      // debtTotals 折算段落)。
      const distinct = new Set<string>()
      for (const a of accountRows) {
        const cur = (a.currency || '').toUpperCase()
        if (cur) distinct.add(cur)
      }
      for (const d of debtRows) {
        const cur = (d.currency || '').toUpperCase()
        if (cur) distinct.add(cur)
      }
      if (base && distinct.size >= 2) {
        const [r, o] = await Promise.all([
          fetchExchangeRates(token, base).catch(() => null),
          fetchExchangeRateOverrides(token).catch(() => [] as ExchangeRateOverride[]),
        ])
        setRates(r)
        setRateOverrides(o)
      } else {
        setRates(null)
        setRateOverrides([])
      }
    } catch (err) {
      notifyError(err)
    }
  }, [token, base, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  // 帳戶頭像(2026-08-02 補強):通过共享 AttachmentCache 惰性加载,对齐
  // CategoriesPage 的既有模式 —— rows 更新时把所有 avatar_cloud_file_id
  // push 给 context,context 内部自己去重 + dedupe inflight。
  useEffect(() => {
    const ids = rows
      .map((row) => row.avatar_cloud_file_id || '')
      .filter((value) => value.trim().length > 0)
    if (ids.length > 0) ensureLoadedMany(ids)
  }, [rows, ensureLoadedMany])

  // AccountsPanel 的编辑弹窗 open 状态本来只在点行内「编辑」时才会被
  // 内部逻辑 setOpen(true);全局事件触发时(见 openSignal prop 注释)靠这个
  // 计数器的变化强制弹窗打开一次。
  const [editOpenSignal, setEditOpenSignal] = useState(0)

  // 把某个账户装进编辑表单 —— 行内「编辑」按钮 + 全局 openEditAccount 事件
  // (信用卡帐单卡片的「前往帳戶設定」)共用同一份逻辑,后者不在本页时先
  // navigate 过来再 dispatch,这里只管把 row 转成表单字段 + 强制打开弹窗。
  const handleEditAccount = useCallback((row: ReadAccount) => {
    setEditOpenSignal((s) => s + 1)
    setForm({
      editingId: row.id,
      editingOwnerUserId: row.created_by_user_id || '',
      name: row.name,
      account_type: row.account_type || '',
      currency: row.currency || '',
      initial_balance: String(row.initial_balance ?? 0),
      note: row.note ?? '',
      credit_limit: row.credit_limit !== null && row.credit_limit !== undefined
        ? String(row.credit_limit)
        : '',
      billing_day: row.billing_day !== null && row.billing_day !== undefined
        ? String(row.billing_day)
        : '',
      payment_due_day: row.payment_due_day !== null && row.payment_due_day !== undefined
        ? String(row.payment_due_day)
        : '',
      bank_name: row.bank_name ?? '',
      card_last_four: row.card_last_four ?? '',
      parent_account_id: row.parent_account_id ?? '',
      hidden: row.hidden ?? false,
      auto_pay_enabled: row.auto_pay_enabled ?? false,
      auto_pay_from_account_id: row.auto_pay_from_account_id ?? '',
      avatar_cloud_file_id: row.avatar_cloud_file_id ?? '',
      avatar_cloud_sha256: row.avatar_cloud_sha256 ?? '',
      include_in_total: row.include_in_total !== false,
    })
  }, [])

  useEffect(() => onOpenEditAccount(handleEditAccount), [handleEditAccount])

  const onSave = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      toast.error(t('accounts.error.nameRequired'), t('notice.error'))
      return false
    }
    // mobile account_edit_page 也禁止重名,跨端一致。编辑自己时跳过。
    const duplicate = rows.find(
      (row) =>
        (row.name || '').trim().toLowerCase() === trimmedName.toLowerCase() &&
        row.id !== form.editingId,
    )
    if (duplicate) {
      toast.error(t('accounts.error.nameDuplicate'), t('notice.error'))
      return false
    }
    const initialBalanceNum = Number(form.initial_balance || 0)
    if (!Number.isFinite(initialBalanceNum)) {
      toast.error(t('accounts.error.balanceInvalid'), t('notice.error'))
      return false
    }
    // 主帳戶(§2.9 Phase 4,2026-08-02 改版,同日第二輪放寬到單卡):信用
    // 額度/帳單日/還款日設在 account_group 群組自己身上,或沒有掛靠任何
    // 群組的獨立信用卡自己身上(§2.9 is_billing_root——單卡也該有群組的
    // 全部功能)。1-31,空字符串视作未填(null)。其他情形不要这些字段,
    // 走 onFormChange 切换类型/掛靠时已经清空,这里再 guard 一次。
    const isAccountGroup = form.account_type === 'account_group'
    const isStandaloneCreditCard =
      form.account_type === 'credit_card' && !form.parent_account_id.trim()
    const hasBillingFields = isAccountGroup || isStandaloneCreditCard
    const billingDayNum =
      hasBillingFields && form.billing_day.trim() ? Math.round(Number(form.billing_day)) : null
    if (billingDayNum !== null && (!Number.isFinite(billingDayNum) || billingDayNum < 1 || billingDayNum > 31)) {
      toast.error(t('accounts.error.billingDayInvalid'), t('notice.error'))
      return false
    }
    const paymentDueDayNum =
      hasBillingFields && form.payment_due_day.trim() ? Math.round(Number(form.payment_due_day)) : null
    if (paymentDueDayNum !== null && (!Number.isFinite(paymentDueDayNum) || paymentDueDayNum < 1 || paymentDueDayNum > 31)) {
      toast.error(t('accounts.error.paymentDueDayInvalid'), t('notice.error'))
      return false
    }
    const creditLimitRaw = form.credit_limit.trim()
    const creditLimitNum = hasBillingFields && creditLimitRaw ? Number(creditLimitRaw) : null
    if (creditLimitNum !== null && (!Number.isFinite(creditLimitNum) || creditLimitNum < 0)) {
      toast.error(t('accounts.error.creditLimitInvalid'), t('notice.error'))
      return false
    }
    // 自動扣繳(§2.9,2026-08-04 改版):只在 billing-root 帳戶上生效,開啟時
    // 必須選一個來源帳戶。
    const autoPayEnabled = hasBillingFields && form.auto_pay_enabled
    if (autoPayEnabled && !form.auto_pay_from_account_id.trim()) {
      toast.error(t('accounts.error.autoPaySourceRequired'), t('notice.error'))
      return false
    }
    try {
      const isCreditCard = form.account_type === 'credit_card'
      const isBankOrCredit = isCreditCard || form.account_type === 'bank_card' || isAccountGroup
      const payload = {
        name: trimmedName,
        account_type: form.account_type || null,
        currency: form.currency || null,
        initial_balance: initialBalanceNum,
        // 扩展字段:非群组/非独立信用卡类型显式传 null 清空 server 上残留的
        // 值;bank_card / credit_card / account_group 才有 bank_name /
        // card_last_four。
        note: form.note.trim() || null,
        credit_limit: hasBillingFields ? creditLimitNum : null,
        billing_day: hasBillingFields ? billingDayNum : null,
        payment_due_day: hasBillingFields ? paymentDueDayNum : null,
        bank_name: isBankOrCredit ? form.bank_name.trim() || null : null,
        card_last_four: isBankOrCredit ? form.card_last_four.trim() || null : null,
        // 掛靠主帳戶(§2.9 Phase 4,前端擴充至 bank_card):credit_card /
        // bank_card 类型才有意义,离开这两种类型时(onFormChange 已清空
        // form.parent_account_id)显式传 null 解除掛靠。
        parent_account_id:
          isCreditCard || form.account_type === 'bank_card'
            ? form.parent_account_id.trim() || null
            : null,
        // 账户隐藏(issue #240):新建默认 false;编辑时带当前切换状态。
        hidden: form.hidden,
        // 自動扣繳(§2.9,2026-08-04 改版):非 billing-root 类型显式传
        // false/null 清空 server 上残留的值(对齐上面 credit_limit 等字段
        // 同款处理:切换类型/掛靠后不留死值)。
        auto_pay_enabled: autoPayEnabled,
        auto_pay_from_account_id: autoPayEnabled ? form.auto_pay_from_account_id.trim() || null : null,
        // 帳戶頭像(2026-08-02 補強):任何帳戶類型都可以設,不像
        // credit_limit 等只对 billing-root 生效那样要清空。
        avatar_cloud_file_id: form.avatar_cloud_file_id.trim() || null,
        avatar_cloud_sha256: form.avatar_cloud_sha256.trim() || null,
        // 納入總餘額(Phase 18):新建默认 true;编辑时带当前切换状态。
        include_in_total: form.include_in_total,
      }
      await retryOnConflict(activeLedgerId, (base) =>
        form.editingId
          ? updateAccount(token, activeLedgerId, form.editingId, base, payload)
          : createAccount(token, activeLedgerId, base, payload)
      )
      setForm(accountDefaults())
      await refresh()
      notifySuccess(form.editingId ? t('notice.accountUpdated') : t('notice.accountCreated'))
      return true
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
        notifyError(err)
        return false
      }
      notifyError(err)
      return false
    }
  }


  // 「已隐藏」分区卡片上的快捷「恢复」按钮 —— 不经编辑弹窗,直接 PATCH
  // hidden=false(对齐产品设计:恢复"即时生效,卡片回到在用分区")。
  // 入参用 ReadAccount(跟 AccountsPanel.onRestore 的回调签名对齐,只用得到 id)。
  const onRestore = async (row: ReadAccount) => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateAccount(token, activeLedgerId, row.id, base, { hidden: false }),
      )
      await refresh()
      notifySuccess(t('notice.accountRestored'))
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
      }
      notifyError(err)
    }
  }

  // 帳戶清單拖曳排序(2026-09-05):外層(頂層帳戶區塊)跟內層(子帳戶)拖曳
  // 完的持久化邏輯完全一樣,只是傳進來的 orderedRows 集合不同——單一
  // sort_order 整數軸,頂層/子帳戶各自是獨立的相對順序空間,互不干擾(見
  // reorderAccounts 註解)。樂觀更新先把本地 rows 灌成新順序,畫面立刻反映
  // 拖曳結果,不等 API 回來。
  const persistAccountReorder = async (orderedRows: ReadAccount[]) => {
    if (!activeLedgerId) return
    const items = orderedRows.map((row, index) => ({ account_id: row.id, sort_order: index }))
    const nextSortOrder = new Map(items.map((i) => [i.account_id, i.sort_order]))
    setRows((prev) =>
      prev.map((r) => (nextSortOrder.has(r.id) ? { ...r, sort_order: nextSortOrder.get(r.id)! } : r))
    )
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        reorderAccounts(token, activeLedgerId, base, items)
      )
    } catch (err) {
      await refresh()
      notifyError(err)
    }
  }

  // 删除流程(2026-08 改版为级联删除):点删除按钮 → tx_count === 0 时跟原本
  // 一样单步确认;tx_count > 0 时两段式确认 —— 第一段只是把 cascadeConfirming
  // 翻真、不发请求,第二段才真正带 cascade:true 调用 deleteAccount。结构性
  // 引用(周期性收支/分期/范本/回馈规则/自动扣缴来源)不论 cascade 与否一律
  // 被后端拒绝,这里维持原本的 notifyError 展示(跟其它 write validation 报错
  // 同一套文案机制)。
  const pendingTxCount = pendingDelete?.tx_count ?? 0
  const needsCascadeConfirm = pendingTxCount > 0
  const onConfirmDelete = async () => {
    if (!pendingDelete) return
    if (needsCascadeConfirm && !cascadeConfirming) {
      setCascadeConfirming(true)
      return
    }
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    setDeleting(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteAccount(token, activeLedgerId, pendingDelete.id, base, {
          cascade: needsCascadeConfirm,
        }),
      )
      setPendingDelete(null)
      setCascadeConfirming(false)
      await refresh()
      notifySuccess(
        needsCascadeConfirm
          ? t('notice.accountDeletedCascade', { count: pendingTxCount })
          : t('notice.accountDeleted'),
      )
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
      }
      notifyError(err)
    } finally {
      setDeleting(false)
    }
  }

  // 资产汇总(统一折算视图):按币种分组 → 每币种 summary(netWorth/assetTotal/
  // liabilityTotal)× 汇率累加到主币种。复用 assetAggregation 铁律原语确保负债符号
  // 契约单点。缺失汇率的币种进 missing 列表、不计入任何总额 / donut(整币种剔除),
  // **绝不按 1 折算**。
  //   - null:无账户(byCur 为空),不出汇总卡(空态由 AccountsPanel 引导)。
  //   - { needsBase: true }:多币种但未设主币种 —— 渲染处出「设置主币种」引导卡。
  //   - 否则:有效主币种 effectiveBase(已设则用之;单币种且未设则取该唯一币种,
  //     此时折算率恒为 1、missing 必空、rateDate 置 undefined 不显脚注/≈)。
  //   - buckets:每币种 summary + 分组,既给合并 donut(mergeGroupsToBase),也给详情 dialog 的分币种卡复用。
  //   - mergedGroups:各币种 groups × 汇率折算后按 type 聚合成一份主币种构成,喂 donut(currency=effectiveBase)。
  // 納入總餘額(Phase 18):这个过滤独立于 hidden 切分——一个账户可以「隱藏
  // 但仍計入總額」，也可以「顯示但不計入總額」，两者互不耦合。只影响这张
  // 折算匯總卡（淨資產/資產/負債/構成 donut），不影响 AccountsPanel 内部
  // 的 listGroups 底部分組列表。
  const totalIncludedRows = useMemo(
    () => rows.filter((row) => row.include_in_total !== false),
    [rows]
  )

  const converted = useMemo(() => {
    const byCur = splitByCurrency(totalIncludedRows)
    // 欠款/應收(§淨資產卡同口徑,見 WorkspaceDebtCurrencyTotal 註解):跟
    // accounts 一样参与「有几种币种」的判断——只有欠款没有账户时也该出卡,
    // 单币种(账户 0 种 + 欠款 1 种,或两边同一种)也不该误判成多币种。
    const debtCurrencies = new Set(debtTotals.map((d) => d.currency.toUpperCase()))
    if (byCur.size === 0 && debtCurrencies.size === 0) return null
    const allCurrencies = new Set<string>([...byCur.keys(), ...debtCurrencies])
    // 主币种未设时:单币种回退到该唯一币种(折算率 1,零误差);多币种则无从折算。
    const effectiveBase = base || (allCurrencies.size === 1 ? [...allCurrencies][0] : '')
    if (!effectiveBase) return { needsBase: true } as const
    // 单币种(effectiveBase 即本币 且 仅 1 种)不显汇率脚注 / ≈ 前缀。
    const singleCurrency = allCurrencies.size === 1

    const buckets: CurrencyBucket[] = []
    let netWorth = 0
    let assetTotal = 0
    let liabilityTotal = 0
    const missing = new Set<string>()
    for (const [cur, curRows] of byCur) {
      const summary = computeCurrencySummary(curRows)
      // 详情 dialog 复用 CurrencyAssetCard,需要全部币种(含缺失汇率的)原样展示。
      buckets.push({ currency: cur, summary, groups: computeTypeGroups(curRows, t) })
      const eff = effectiveRateToBase(cur, effectiveBase, rates, rateOverrides)
      if (!eff) {
        missing.add(cur)
        continue
      }
      netWorth += summary.netWorth * eff.rate
      assetTotal += summary.assetTotal * eff.rate
      liabilityTotal += summary.liabilityTotal * eff.rate
    }
    // 欠款/應收:receivable(對方欠我)算資產、payable(我欠對方)算負債,跟
    // accounts 同口径缺汇率整币种剔除、绝不按 1 折入。此前這張卡完全沒查過
    // debts 表,导致「應收」沒被算進資產,跟 app 端(LocalRepository.
    // getNetWorthBreakdown 把 debt 併進 accounts 淨資產)對不上帳。
    // 已知取舍:不併入 buckets/mergedGroups——跟 app 端「資產構成」donut 一样
    // 本就不含欠款分類,只影响這裡的頭部三個數字,詳情 dialog 分幣種卡片
    // 暫不reflect 欠款(留待有需要時再擴充 CurrencyAssetCard)。
    for (const debt of debtTotals) {
      const cur = debt.currency.toUpperCase()
      const eff = effectiveRateToBase(cur, effectiveBase, rates, rateOverrides)
      if (!eff) {
        missing.add(cur)
        continue
      }
      const receivableBase = debt.receivable_total * eff.rate
      const payableBase = debt.payable_total * eff.rate
      netWorth += receivableBase - payableBase
      assetTotal += receivableBase
      liabilityTotal -= payableBase
    }
    // donut 与上面总额同口径:mergeGroupsToBase 内部对缺失汇率币种同样剔除
    // (欠款不进 donut,见上方註解)。
    const mergedGroups = mergeGroupsToBase(buckets, effectiveBase, rates, rateOverrides)
    return {
      needsBase: false as const,
      base: effectiveBase,
      // 单币种不带「按汇率折算」语义:不显 ≈ / 脚注(rateDate 置 undefined)。
      singleCurrency,
      netWorth,
      assetTotal,
      liabilityTotal,
      mergedGroups,
      buckets: buckets.sort(
        (a, b) =>
          b.summary.assetTotal +
          Math.abs(b.summary.liabilityTotal) -
          (a.summary.assetTotal + Math.abs(a.summary.liabilityTotal)),
      ),
      missing: [...missing].sort(),
      rateDate: singleCurrency ? undefined : rates?.rate_date,
    }
  }, [base, totalIncludedRows, debtTotals, rates, rateOverrides, t])

  return (
    <>
      {/* 资产汇总卡(统一折算视图)—— 四态:
          - converted===null(无账户):不出卡,账户空态由 AccountsPanel 内部引导。
          - converted.needsBase(多币种未设主币种):出「设置主币种」引导卡,按钮跳设置页。
          - 否则:折算汇总卡(净资产 + 资产/负债 + 构成/走势 tab)。多币种带 ≈ + 汇率脚注;
            单币种(rateDate 为空)不显 ≈ / 脚注 / 详情按钮(只有一种币种,无需明细)。
          分币种「每币种一张卡」整块已下线,统一并入本卡的折算口径。 */}
      {converted === null ? null : converted.needsBase ? (
        <Card className="bc-panel mb-4">
          <CardContent className="flex flex-col items-start gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium">{t('accounts.needBaseCurrency.title')}</p>
              <p className="text-xs text-muted-foreground">
                {t('accounts.needBaseCurrency.desc')}
              </p>
            </div>
            <Button
              size="sm"
              className="shrink-0"
              onClick={() =>
                navigate(routePath({ kind: 'app', ledgerId: '', section: 'settings-profile' }))
              }
            >
              {t('accounts.needBaseCurrency.action')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card className="bc-panel mb-4">
          <CardContent className="space-y-3 p-5">
            <div className="min-w-0 space-y-1">
              <p className="text-xs text-muted-foreground">
                {converted.rateDate
                  ? t('accounts.converted.netWorth', { currency: converted.base })
                  : t('accounts.netWorth')}
              </p>
              <div className="flex items-baseline gap-1">
                {converted.rateDate ? (
                  <span className="font-mono text-sm text-muted-foreground">≈</span>
                ) : null}
                <Amount
                  value={converted.netWorth}
                  currency={converted.base}
                  showCurrency
                  size="2xl"
                  bold
                  tone={converted.netWorth >= 0 ? 'positive' : 'negative'}
                />
              </div>
            </div>

            {/* 资产 / 负债两个小项(与净资产同口径:缺失汇率币种已剔除)。
                多币种带 ≈,单币种不带。 */}
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-emerald-600/80 dark:text-emerald-400/80">
                  {t('accounts.assets')}
                </div>
                <div className="mt-0.5 flex items-baseline gap-1">
                  {converted.rateDate ? (
                    <span className="font-mono text-xs text-muted-foreground">≈</span>
                  ) : null}
                  <Amount
                    value={converted.assetTotal}
                    currency={converted.base}
                    size="lg"
                    bold
                    showCurrency
                    tone="positive"
                  />
                </div>
              </div>
              <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-rose-600/80 dark:text-rose-400/80">
                  {t('accounts.liabilities')}
                </div>
                <div className="mt-0.5 flex items-baseline gap-1">
                  {converted.rateDate ? (
                    <span className="font-mono text-xs text-muted-foreground">≈</span>
                  ) : null}
                  <Amount
                    value={Math.abs(converted.liabilityTotal)}
                    currency={converted.base}
                    size="lg"
                    bold
                    showCurrency
                    tone="negative"
                  />
                </div>
              </div>
            </div>

            {/* 「构成 / 走势」tab —— 构成=各币种分组折算到主币种后按类型聚合
                (currency=base)的 donut(单币种即本币原值);走势=嵌入式净值走势图。
                选择设备级持久化(见 ASSET_VIEW_KEY)。无构成数据时构成 tab 退化为空。
                单币种 approx=false(不显 ≈),多币种 approx=true。 */}
            <div>
              <div className="mb-2 flex justify-end gap-1">
                {(['composition', 'trend'] as AssetView[]).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setTrendOrComposition(v)}
                    className={`rounded-full px-2 py-0.5 text-[11px] ${
                      trendOrComposition === v
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground'
                    }`}
                  >
                    {t(`accounts.trendOrComposition.${v}`)}
                  </button>
                ))}
              </div>
              {trendOrComposition === 'trend' ? (
                <NetWorthTrend data={netWorthHistory} embedded />
              ) : converted.mergedGroups.length > 0 ? (
                <AssetsCompositionMini
                  groups={converted.mergedGroups}
                  currency={converted.base}
                  showCurrency
                  embedded
                  approx={Boolean(converted.rateDate)}
                />
              ) : null}
            </div>

            {/* 脚注 + 详情:仅多币种折算态出现(单币种 rateDate 为空,
                既无汇率脚注也无分币种明细可看)。 */}
            {converted.rateDate ? (
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 pt-1">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                  <span className="text-[11px] text-muted-foreground">
                    {t('accounts.converted.footnote', { date: converted.rateDate })}
                  </span>
                  {converted.missing.length > 0 ? (
                    <span className="text-[11px] text-amber-600 dark:text-amber-500">
                      {t('accounts.converted.missing', {
                        currencies: converted.missing.join(', '),
                      })}
                    </span>
                  ) : null}
                </div>
                <Button variant="outline" size="sm" onClick={() => setDetailOpen(true)}>
                  {t('accounts.converted.detail')}
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      )}
      {/* 股票持股(2026-09-28):投資市值另外顯示,不計入淨資產。 */}
      <InvestmentValueCard />
      <AccountsPanel
        form={form}
        rows={rows}
        canManage
        hideCurrencyCards
        onFormChange={setForm}
        onSave={onSave}
        onReset={() => setForm(accountDefaults())}
        onEdit={handleEditAccount}
        openSignal={editOpenSignal}
        avatarPreviewUrlByFileId={avatarPreviewByFileId}
        baseCurrency={base}
        fxRates={rates}
        fxOverrides={rateOverrides}
        onUploadAvatar={async (file) => {
          try {
            const out = await uploadAccountAvatar(token, { file })
            // 新上传的 fileId 还不在 rows 里,不能等那个 effect 去拉预览 —— 这里
            // 立即触发一次,避免裁剪确认后头像缩略图暂时不显示。
            ensureLoadedMany([out.file_id])
            return { fileId: out.file_id, sha256: out.sha256 }
          } catch (err) {
            notifyError(err)
            return null
          }
        }}
        onRestore={(row) => void onRestore(row)}
        onClickAccount={(row) =>
          dispatchOpenDetailAccount(row as WorkspaceAccount, { defaultScope: 'all' })
        }
        onDelete={(row) => {
          // 帳戶級聯刪除(2026-08 改版):不再直接拒絕有交易的帳戶,改成打開
          // 兩段式確認對話框(見下方 ConfirmDialog,依 tx_count 決定顯示第
          // 一段警示還是原本的單步確認)。結構性引用(週期性收支/分期/範本/
          // 回饋規則/自動扣繳來源)仍由後端 snapshot_mutator.delete_account
          // 一律擋下,錯誤走 onConfirmDelete 的 notifyError 分支呈現。
          const ws = rows.find((r) => r.id === row.id) || (row as WorkspaceAccount)
          setCascadeConfirming(false)
          setPendingDelete(ws)
        }}
        onReorderBlocks={(_type, orderedRows) => void persistAccountReorder(orderedRows)}
        onReorderChildren={(_parentId, orderedRows) => void persistAccountReorder(orderedRows)}
      />
      {/* AccountDetailDialog 已迁到 GlobalEntityDialogs */}
      {/* 删除确认(2026-08 改版为级联删除):
          - tx_count === 0:维持原本单步确认,行为不变。
          - tx_count > 0:两段式 —— 先警示会连带删除的交易笔数(cascadeConfirming
            = false),按继续才进入第二段更强烈的最终确认(cascadeConfirming =
            true),第二段按下才真正调用带 cascade:true 的 deleteAccount。 */}
      {pendingDelete && needsCascadeConfirm && !cascadeConfirming ? (
        <ConfirmDialog
          open
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void onConfirmDelete()}
          title={t('accounts.delete.cascadeWarningTitle')}
          description={t('accounts.delete.cascadeWarningMessage', {
            name: pendingDelete.name,
            count: pendingTxCount,
          })}
          confirmText={t('common.next')}
          confirmVariant="destructive"
        />
      ) : (
        <ConfirmDialog
          open={pendingDelete !== null}
          onCancel={() => {
            if (!deleting) {
              setPendingDelete(null)
              setCascadeConfirming(false)
            }
          }}
          onConfirm={() => void onConfirmDelete()}
          loading={deleting}
          title={t('dialog.confirm')}
          description={
            needsCascadeConfirm
              ? t('accounts.delete.cascadeFinalMessage', {
                  name: pendingDelete?.name || '',
                  count: pendingTxCount,
                })
              : t('accounts.delete.confirmMessage', { name: pendingDelete?.name || '' })
          }
          confirmText={t('common.delete')}
          confirmVariant="destructive"
        />
      )}
      {/* 分币种明细 dialog —— 折算汇总卡(多币种态)的「详情」入口,复用
          CurrencyAssetCard 按网格逐币种渲染(含缺失汇率币种,原样不折算)。
          needsBase / 单币种态不会打开此 dialog。 */}
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-h-[88vh] w-[92vw] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('accounts.converted.detailTitle')}</DialogTitle>
          </DialogHeader>
          {converted && !converted.needsBase ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {converted.buckets.map((entry) => (
                <CurrencyAssetCard key={entry.currency} entry={entry} />
              ))}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}
