import { useCallback, useEffect, useState } from 'react'

import {
  createCategory,
  createDebt,
  createInstallmentPlan,
  createProject,
  createTag,
  createTransaction,
  fetchCardRecommendation,
  fetchCardRewardRules,
  fetchReadDebts,
  fetchReadProjects,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  updateCategory,
  updateRecurringOccurrence,
  updateRecurringRuleFrom,
  updateTransaction,
  uploadAttachment,
  type ReadCardRewardRule,
  type ReadDebt,
  type ReadProject,
  type RecurringOccurrenceUpdatePayload,
  type RecurringUpdateFromPayload,
  type SwipeSmartCardRecommendation,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
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
  CategoriesPanel,
  categoryDefaults,
  pickRandomTagColor,
  TransactionsPanel,
  txDefaults,
  type CategoryForm,
  type TxForm,
} from '@beecount/web-features'

import { useLedgerWrite } from '../app/useLedgerWrite'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAuth } from '../context/AuthContext'
import { useLedgers } from '../context/LedgersContext'
import { localizeError } from '../i18n/errors'
import { onOpenEditCategory, onOpenEditTx, onOpenNewTx } from '../lib/txDialogEvents'
import { buildRecurringUpdateFromPayload, type RecurringUpdateFromSource } from '../lib/recurringUpdateFrom'

/** Phase 24:週期性收支差異化編輯的上下文——由 onOpenEditTx 的
 * `recurringEditMode` option 帶入,存檔時決定分流到哪支 API(見
 * handleSaveTx 底部)。 */
type RecurringEditContext = { ruleId: string; mode: 'single' | 'future' }

/** 延後入帳(§2.10 Phase 5)回显/提交用:同 CardRewardRulesSection.tsx/
 *  DebtsPanel.tsx 的 isoToDateInput/dateInputToIso —— 后端这类"纯日期"字段
 *  可能回传不带时区位移的 naive datetime 字串,缺位移标记时补 "Z" 强制当
 *  UTC 解析,避免 UTC+8 使用者少算一天。 */
function isoToDateInputUtc(iso: string): string {
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  const d = new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

function dateInputToIsoUtc(value: string): string {
  return `${value}T00:00:00+00:00`
}

/**
 * 全局编辑容器 — 任何页都能触发交易/分类编辑弹窗,不需要 navigate 到对应
 * 管理页。复用 TransactionsPanel/CategoriesPanel 的 Dialog + picker
 * (传 dialogOnlyMode 跳过列表渲染)。
 *
 * 跟之前散在 *Page 各自管理 detail+edit dialog 不同,这里统一在 AppShell 顶层
 * 处理:
 *   - 监听 openEditTx / openEditCategory 全局事件
 *   - 按需 fetch 写权限的 ledger refs(accounts/categories/tags)
 *   - 调 create/updateTransaction API + 命中 useLedgerWrite 的 CAS 重试
 *   - 写成功后通过 SyncSocket bumpLedger 触发其它页 refresh
 *
 * 编辑分类暂时仍走 navigate 到 /app/categories(分类编辑表单依赖 inline form
 * 的 icon picker / parent picker,数据流复杂,后续单独再做全局化)。
 */
export function GlobalEditDialogs() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { ledgers, currency, activeLedgerId } = useLedgers()
  const { previewMap: iconPreviewByFileId } = useAttachmentCache()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const [editTxOpen, setEditTxOpen] = useState(false)
  const [editTxForm, setEditTxForm] = useState<TxForm>(txDefaults())
  // Phase 24:非 null 時,存檔要分流到 occurrence PATCH / update-from POST
  // 而不是一般的 updateTransaction——見 onOpenEditTx 監聽器與 handleSaveTx。
  const [editRecurringContext, setEditRecurringContext] = useState<RecurringEditContext | null>(null)
  const [editTxLedgerId, setEditTxLedgerId] = useState('')
  const [editTxAccounts, setEditTxAccounts] = useState<WorkspaceAccount[]>([])
  const [editTxCategories, setEditTxCategories] = useState<WorkspaceCategory[]>([])
  const [editTxTags, setEditTxTags] = useState<WorkspaceTag[]>([])
  // 借還款追蹤(§2.5 體驗補強):ledger-scoped,跟 account/category/tag
  // 一起按 ledgerId 拉,同 TransactionsPage 的 txDictionaryDebts 逻辑。
  const [editTxDebts, setEditTxDebts] = useState<ReadDebt[]>([])
  // 專案(Phase 13,docs/PH13_PROJECT_SD.md):同款语义,ledger-scoped,
  // 跟 debt 一起按 ledgerId 拉。
  const [editTxProjects, setEditTxProjects] = useState<ReadProject[]>([])
  // 信用卡紅利回饋(§2.9.5,2026-08-06 改版):跟 TransactionsPage 同款,
  // 按目前表单选中的信用卡帐户单独拉,不进 loadRefsForLedger 的批量字典。
  const [editTxRewardRules, setEditTxRewardRules] = useState<ReadCardRewardRule[]>([])
  const [refsLoading, setRefsLoading] = useState(false)
  // v30 多币种:编辑交易时币种弹窗展示各币种对账本主币种的汇率。
  const editTxBase = (
    ledgers.find((l) => l.ledger_id === editTxLedgerId)?.currency || 'CNY'
  )
    .trim()
    .toUpperCase()
  const [editTxRates, setEditTxRates] = useState<Record<string, number>>({})
  useEffect(() => {
    if (!token || !editTxOpen) return
    let cancelled = false
    loadRatesToBase(token, editTxBase)
      .then((m) => {
        if (!cancelled) setEditTxRates(m)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [token, editTxOpen, editTxBase])

  // 信用卡紅利回饋(§2.9.5,2026-08-06 改版):同 TransactionsPage 逻辑,
  // 只有 expense + 選中帳戶是 credit_card 时才拉规则列表,不 filter enabled
  // (已停用规则若曾被这笔交易勾选,仍要能显示出来给使用者取消勾选)。
  useEffect(() => {
    if (!editTxOpen || editTxForm.tx_type !== 'expense' || !editTxLedgerId) {
      setEditTxRewardRules([])
      return
    }
    const account = editTxAccounts.find(
      (row) => (row.name || '').trim().toLowerCase() === editTxForm.account_name.trim().toLowerCase(),
    )
    if (!account || account.account_type !== 'credit_card') {
      setEditTxRewardRules([])
      return
    }
    let cancelled = false
    fetchCardRewardRules(token, editTxLedgerId, account.id)
      .then((rows) => {
        if (!cancelled) setEditTxRewardRules(rows)
      })
      .catch(() => {
        if (!cancelled) setEditTxRewardRules([])
      })
    return () => {
      cancelled = true
    }
  }, [editTxOpen, editTxForm.tx_type, editTxForm.account_name, editTxLedgerId, editTxAccounts, token])

  // 分类编辑相关
  const [editCatOpen, setEditCatOpen] = useState(false)
  const [editCatForm, setEditCatForm] = useState<CategoryForm>(categoryDefaults())
  const [editCatLedgerId, setEditCatLedgerId] = useState('')
  const [editCatRows, setEditCatRows] = useState<WorkspaceCategory[]>([])

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  // 该 ledger 是否对当前用户可写 — 决定 panel 的 ledgerOptions 列表
  const writableLedgers = ledgers.filter(
    (l) => l.role === 'owner' || l.role === 'editor',
  )
  const ledgerOptions = writableLedgers.map((l) => ({
    ledger_id: l.ledger_id,
    ledger_name: l.ledger_name,
  }))
  // 借還款追蹤(體驗補強第二輪):「建立新欠款」owner-only,跟
  // TransactionsPage.tsx::txCanCreateDebt 同一判断口径。
  const editTxCanCreateDebt =
    ledgers.find((l) => l.ledger_id === editTxLedgerId)?.role === 'owner'
  // 專案(Phase 13):新建專案同样是 owner-only 的 `POST .../projects`。
  const editTxCanManageProjects =
    ledgers.find((l) => l.ledger_id === editTxLedgerId)?.role === 'owner'

  const loadRefsForLedger = useCallback(
    async (ledgerId: string) => {
      if (!ledgerId) return
      setRefsLoading(true)
      try {
        const [accts, cats, tagsList, debtsList, projectsList] = await Promise.all([
          fetchWorkspaceAccounts(token, { ledgerId, limit: 500 }),
          fetchWorkspaceCategories(token, { ledgerId, limit: 500 }),
          fetchWorkspaceTags(token, { ledgerId, limit: 500 }),
          fetchReadDebts(token, ledgerId).catch(() => []),
          fetchReadProjects(token, ledgerId).catch(() => []),
        ])
        setEditTxAccounts(accts)
        setEditTxCategories(cats)
        setEditTxTags(tagsList)
        setEditTxDebts(debtsList)
        setEditTxProjects(projectsList)
      } catch (err) {
        notifyError(err)
      } finally {
        setRefsLoading(false)
      }
    },
    [token, notifyError],
  )

  // SwipeSmart 刷卡建議(Phase 14 §3.3.5):同 TransactionsPage.tsx 的
  // handleFetchCardRecommendations,失敗/逾時 catch 成空陣列。
  const handleFetchCardRecommendations = useCallback(
    async (ledgerId: string, amount: number, merchant: string): Promise<SwipeSmartCardRecommendation[]> => {
      try {
        return await fetchCardRecommendation(token, ledgerId, { amount, merchant })
      } catch {
        return []
      }
    },
    [token],
  )

  // 表單內直接新增分類/標籤(需求 #10,Phase 11):同 TransactionsPage.tsx 的
  // onCreateTxCategory/onCreateTxTag ——建在目前編輯交易所在的帳本
  // (`editTxLedgerId`),成功後樂觀 append 進 `editTxCategories`/`editTxTags`,
  // 不必等下一輪 loadRefsForLedger 才能反查到剛建立的這筆。
  const onCreateTxCategory = useCallback(
    async (name: string, kind: 'expense' | 'income'): Promise<WorkspaceCategory | null> => {
      const ledgerId = editTxLedgerId.trim()
      if (!ledgerId) return null
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
            parent_name: null,
          }),
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
          created_by_email: null,
        }
        setEditTxCategories((prev) => [...prev, created])
        return created
      } catch (err) {
        notifyError(err)
        return null
      }
    },
    [editTxLedgerId, retryOnConflict, token, notifyError],
  )

  const onCreateTxTag = useCallback(
    async (name: string): Promise<{ id: string; name: string } | null> => {
      const ledgerId = editTxLedgerId.trim()
      if (!ledgerId) return null
      try {
        const res = await retryOnConflict(ledgerId, (base) =>
          createTag(token, ledgerId, base, { name, color: pickRandomTagColor() }),
        )
        const created = { id: res.entity_id || '', name }
        const createdTag: WorkspaceTag = {
          ...created,
          color: null,
          last_change_id: res.new_change_id,
          ledger_id: ledgerId,
          ledger_name: null,
          created_by_user_id: null,
          created_by_email: null,
        }
        setEditTxTags((prev) => [...prev, createdTag])
        return created
      } catch (err) {
        notifyError(err)
        return null
      }
    },
    [editTxLedgerId, retryOnConflict, token, notifyError],
  )

  // 專案(Phase 13):同 TransactionsPage.tsx::onCreateTxProject 邏輯。
  const onCreateTxProject = useCallback(
    async (name: string): Promise<ReadProject | null> => {
      const ledgerId = editTxLedgerId.trim()
      if (!ledgerId) return null
      try {
        const res = await retryOnConflict(ledgerId, (base) =>
          createProject(token, ledgerId, base, { name }),
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
          ledger_name: null,
        }
        setEditTxProjects((prev) => [...prev, created])
        return created
      } catch (err) {
        notifyError(err)
        return null
      }
    },
    [editTxLedgerId, retryOnConflict, token, notifyError],
  )

  // 监听全局 openEditTx 事件 — 任何页 dispatch 都会被这里接住
  // 先 await fetch refs 再 open dialog,避免下拉空数据闪现
  useEffect(() => {
    return onOpenEditTx(async (tx, options) => {
      const ledgerId =
        tx.ledger_id || writableLedgers[0]?.ledger_id || ''
      setEditTxLedgerId(ledgerId)
      // Phase 24:只有真的帶了 recurringEditMode 且這筆交易確實掛在某條
      // 規則下才記錄分流上下文——一般編輯(沒有 options)或防禦性場景
      // (recurring_rule_id 缺失)都當一般交易處理,走既有 updateTransaction。
      setEditRecurringContext(
        options?.recurringEditMode && tx.recurring_rule_id
          ? { ruleId: tx.recurring_rule_id, mode: options.recurringEditMode }
          : null,
      )
      setEditTxForm({
        ...txDefaults(),
        editingId: tx.id,
        editingOwnerUserId: tx.created_by_user_id || '',
        // §2.10 Phase 5:`tx.tx_type` 現在含 'adjustment'(餘額調整語意化端點
        // 產生,不是一般交易表單能建的類型)——同 category_kind 既有慣例用
        // cast 收窄,編輯這種交易時表單會落回 'expense' 顯示,不影響它
        // 已經存在的 tx_type(這個表單本來就不打算讓使用者手動改成
        // adjustment,payload 也不会送这个值)。
        tx_type: (tx.tx_type === 'adjustment' ? 'expense' : tx.tx_type) as TxForm['tx_type'],
        // 手續費/折扣(2026-08 使用者需求):金額欄位回填 base_amount(原始
        // 金額),沒用過這個功能(base_amount 為 null)時 fallback 回 amount,
        // 對既有交易的顯示完全沒有影響。
        amount: String(tx.base_amount ?? tx.amount),
        // 轉帳手續費/折損(2026-08-29):transfer 用兩個獨立開關(轉出側
        // fee_enabled、轉入側 discount_enabled);expense/income 維持既有的
        // 「兩個分量共用 fee_enabled 一個開關」行為。同 TransactionsPage.tsx
        // openTxEditForm 的同款寫法。
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
        // v30 多币种:回显该笔币种 + 原币种(提交时币种未变不发字段,金额
        // 变更折算由 server L14 隐含汇率联动,防快照漂移)
        currency: (tx.currency_code || '').toUpperCase(),
        original_currency: (tx.currency_code || '').toUpperCase(),
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
        // 留空让 resolveEffectiveRate 退回现查匯率。
        fx_amount_override:
          tx.tx_type === 'transfer' && tx.to_amount != null ? String(tx.to_amount) : '',
        tags:
          tx.tags_list && tx.tags_list.length > 0
            ? tx.tags_list
            : (tx.tags || '')
                .split(',')
                .map((s) => s.trim())
                .filter((s) => s.length > 0),
        attachments: Array.isArray(tx.attachments) ? tx.attachments : [],
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
              note: s.note || '',
            }))
          : [],
      })
      // 等 refs 拉完再打开 dialog,确保 category/account/tag 下拉有数据
      await loadRefsForLedger(ledgerId)
      setEditTxOpen(true)
    })
  }, [loadRefsForLedger, writableLedgers])

  // 监听全局 openNewTx 事件 — CmdK / CalendarPage / 任意页 dispatch 都接住,
  // 不再要求触发方先 navigate 到 transactions 再 dispatch。
  // prefill.happenedAt:CalendarPage 选中某日 + 点新增时填充;
  // prefill.ledgerId:CalendarPage 把当前账本带过来;CmdK 不传走 active ledger。
  useEffect(() => {
    return onOpenNewTx(async (prefill) => {
      // 建新交易(含複製)一律不是週期性差異化編輯上下文。
      setEditRecurringContext(null)
      const ledgerId =
        prefill?.ledgerId ||
        (activeLedgerId &&
        writableLedgers.some((l) => l.ledger_id === activeLedgerId)
          ? activeLedgerId
          : writableLedgers[0]?.ledger_id) ||
        ''
      setEditTxLedgerId(ledgerId)
      const defaults = txDefaults()
      const refundOf = prefill?.refundOf
      const duplicateOf = prefill?.duplicateOf
      // §2.6:退款交易的 tx_type 是原交易的反向类型 —— 退一笔支出用收入
      // 冲回来,退一笔收入用支出冲回去。
      const refundVehicleType: 'income' | 'expense' | undefined = refundOf
        ? refundOf.origTxType === 'income' ? 'expense' : 'income'
        : undefined
      setEditTxForm({
        ...defaults,
        happened_at: prefill?.happenedAt || defaults.happened_at,
        // §2.12.3:从原交易明细发起退款 —— 预填金额/备注/分类/账户,
        // tx_type 强制为原交易的反向类型,refund_of_id 指向原交易。用户
        // 仍可在表单里调整金额(支援部分退款)/改退款帐户。
        ...(refundOf && refundVehicleType
          ? {
              tx_type: refundVehicleType,
              category_kind: refundVehicleType,
              amount: String(refundOf.amount),
              note: refundOf.note || '',
              category_name: refundOf.categoryName || '',
              account_name: refundOf.accountName || '',
              refund_of_id: refundOf.id,
            }
          : {}),
        // 複製交易(2026-08 使用者回饋):比照 onOpenEditTx 的欄位映射,但
        // editingId 維持 null(建立新交易)、happened_at 不覆蓋(維持上面
        // 已經算好的「現在時間」),故意不帶 refund_of_id/debt_id/
        // attachments/deferred_posting_at ——複製出來的是全新獨立記錄,不
        // 該延續原交易綁定的退款/欠款/附件/延後入帳狀態。
        ...(duplicateOf
          ? {
              tx_type: (duplicateOf.tx_type === 'adjustment' ? 'expense' : duplicateOf.tx_type) as TxForm['tx_type'],
              amount: String(duplicateOf.base_amount ?? duplicateOf.amount),
              // 轉帳手續費/折損(2026-08-29):同 onOpenEditTx 的同款分流。
              fee_enabled:
                duplicateOf.tx_type === 'transfer'
                  ? duplicateOf.fee_amount != null
                  : duplicateOf.fee_amount != null || duplicateOf.discount_amount != null,
              fee_amount: duplicateOf.fee_amount != null ? String(duplicateOf.fee_amount) : '',
              fee_label: duplicateOf.fee_label || '',
              discount_amount: duplicateOf.discount_amount != null ? String(duplicateOf.discount_amount) : '',
              discount_label: duplicateOf.discount_label || '',
              discount_enabled:
                duplicateOf.tx_type === 'transfer' ? duplicateOf.discount_amount != null : false,
              currency: (duplicateOf.currency_code || '').toUpperCase(),
              original_currency: (duplicateOf.currency_code || '').toUpperCase(),
              note: duplicateOf.note || '',
              merchant: duplicateOf.merchant || '',
              category_name: duplicateOf.category_name || '',
              category_kind: (duplicateOf.category_kind as TxForm['category_kind']) || 'expense',
              account_name: duplicateOf.account_name || '',
              from_account_name: duplicateOf.from_account_name || '',
              to_account_name: duplicateOf.to_account_name || '',
              tags:
                duplicateOf.tags_list && duplicateOf.tags_list.length > 0
                  ? duplicateOf.tags_list
                  : (duplicateOf.tags || '')
                      .split(',')
                      .map((s) => s.trim())
                      .filter((s) => s.length > 0),
              project_id: duplicateOf.project_id || '',
              project_name: duplicateOf.project_name || '',
              reward_rule_ids: duplicateOf.reward_rule_ids || [],
              split_enabled: Boolean(duplicateOf.has_splits) && (duplicateOf.splits?.length || 0) >= 2,
              splits: Boolean(duplicateOf.has_splits)
                ? (duplicateOf.splits || []).map((s) => ({
                    category_id: s.category_id || '',
                    category_name: s.category_name || '',
                    amount: String(s.amount),
                    note: s.note || '',
                  }))
                : [],
            }
          : {}),
      })
      await loadRefsForLedger(ledgerId)
      setEditTxOpen(true)
    })
  }, [loadRefsForLedger, writableLedgers, activeLedgerId])

  // ledger 切换(理论上编辑模式下 ledger 不可改,新建模式下才能切)
  const handleLedgerChange = useCallback(
    (ledgerId: string) => {
      setEditTxLedgerId(ledgerId)
      void loadRefsForLedger(ledgerId)
    },
    [loadRefsForLedger],
  )

  const handleSaveTx = useCallback(async (): Promise<boolean> => {
    const ledgerId = editTxLedgerId.trim()
    if (!ledgerId) {
      notifyError(new Error(t('transactions.error.ledgerRequired')))
      return false
    }
    const amountNum = Number((editTxForm.amount || '').toString().trim())
    if (!Number.isFinite(amountNum) || amountNum <= 0) {
      notifyError(new Error(t('transactions.error.amountInvalid')))
      return false
    }
    // 手續費/折扣(2026-08 使用者需求):前端先算好即時預覽/離線送出用的
    // 總額,server 端仍會依 base_amount/fee_amount/discount_amount 重新算
    // 一次當最終權威(見 write/_shared.py::_normalize_fee_discount_amount)。
    // 轉帳手續費/折損(2026-08-29):同 TransactionsPage.tsx 的同款分流——
    // discount_amount 對 transfer 是獨立的 discount_enabled 開關,amount
    // 不套用 base±fee∓discount 公式。
    const isTransferForFeeDiscount = editTxForm.tx_type === 'transfer'
    const feeNum = editTxForm.fee_enabled ? Number(editTxForm.fee_amount) || 0 : 0
    const discountNum = isTransferForFeeDiscount
      ? (editTxForm.discount_enabled ? Number(editTxForm.discount_amount) || 0 : 0)
      : (editTxForm.fee_enabled ? Number(editTxForm.discount_amount) || 0 : 0)
    const totalAmountNum = isTransferForFeeDiscount
      ? amountNum
      : computeTxTotalAmount(editTxForm.tx_type, amountNum, feeNum, discountNum)
    // 退款交易(refund_of_id 非空)留空分类时 server 会自动归到自建的「退款」
    // 分类(`ensure_refund_category`),跟主表单(TransactionsPage.tsx)同款
    // 放宽,不强制用户手动选。
    if (
      editTxForm.tx_type !== 'transfer' &&
      !editTxForm.split_enabled &&
      !editTxForm.refund_of_id.trim() &&
      !editTxForm.category_name.trim()
    ) {
      notifyError(new Error(t('transactions.error.categoryRequired')))
      return false
    }
    // 拆帳(§2.4):跟 server 端 _validate_tx_splits 同一套规则(至少 2 笔/
    // 每笔金额 > 0/加总等于交易 amount),前端先挡一遍减少来回请求。手續費/
    // 折扣開啟時,server 端驗證的 amount 是換算後的總額,這裡要用
    // totalAmountNum 對齊,不能用調整前的 amountNum。
    const splitError = validateTxSplits(editTxForm, totalAmountNum)
    if (splitError) {
      notifyError(new Error(t(splitError)))
      return false
    }
    if (editTxForm.tx_type === 'transfer') {
      if (
        !editTxForm.from_account_name.trim() ||
        !editTxForm.to_account_name.trim()
      ) {
        notifyError(new Error(t('transactions.error.transferAccountsRequired')))
        return false
      }
      if (
        editTxForm.from_account_name.trim() ===
        editTxForm.to_account_name.trim()
      ) {
        notifyError(new Error(t('transactions.error.transferAccountsDifferent')))
        return false
      }
    }
    // Phase 1.5(§2.12.1):分期付款期数校验,跟 InstallmentPlansPage 同规则。
    if (!editTxForm.editingId && editTxForm.installment_enabled) {
      const periodsNum = Number(editTxForm.installment_periods)
      if (!Number.isInteger(periodsNum) || periodsNum < 1 || periodsNum > 600) {
        notifyError(new Error(t('installmentPlans.error.periodsInvalid')))
        return false
      }
    }
    // 拆帳(§2.4)跟週期性收支/分期付款組合尚未支援(server 端也会拒绝
    // splits+recurring;installment 本身走独立的 createInstallmentPlan,不
    // 会带 splits 字段,这里只挡 recurring 那一种 UI 上就能同时勾选的组合)。
    if (editTxForm.split_enabled && (editTxForm.recurring_enabled || editTxForm.installment_enabled)) {
      notifyError(new Error(t('transactions.error.splitRecurringNotSupported')))
      return false
    }
    // 拆帳(§2.4)跟退款(§2.6)互斥:server 端 _validate_tx_splits 挡"退款
    // 交易不能拆帳",这里同步在前端挡,避免用户填完一堆 split 行才被拒。
    if (editTxForm.split_enabled && editTxForm.refund_of_id.trim()) {
      notifyError(new Error(t('transactions.error.splitRefundNotSupported')))
      return false
    }
    // 帳戶必選(需求 #9,Phase 11):同 TransactionsPage.onSaveTransaction ——
    // 新建 或 使用者主動變更過帳戶欄位時才強制必選,既有 mobile 匯入的無
    // 帳戶交易只改其它欄位時放行。
    if (
      editTxForm.tx_type !== 'transfer' &&
      !editTxForm.account_name.trim() &&
      (!editTxForm.editingId ||
        editTxForm.account_name.trim() !== editTxForm.original_account_name.trim())
    ) {
      notifyError(new Error(t('transactions.error.accountRequired')))
      return false
    }

    // v30 多币种 + 跨幣別自動換算(2026-08):跟 TransactionsPage.onSaveTransaction
    // 同一實現——把「入帳幣別」金額換算成使用者選的帳戶自己的幣別(不是帳本
    // 本位幣),帳戶餘額口徑永遠是帳戶自身幣別。手續費/折扣、拆帳的每個分量
    // 都要跟著同一個匯率縮放,否則 server 端會用「未換算的分量」重新算出
    // 權威 amount(見 write/_shared.py::_normalize_fee_discount_amount)。
    const ledgerBase = (
      ledgers.find((l) => l.ledger_id === ledgerId)?.currency || 'CNY'
    )
      .trim()
      .toUpperCase()
    const entryCurrency = (editTxForm.currency || ledgerBase).toUpperCase()
    const accountCurrencyOfForm = (
      editTxAccounts
        .find((a) => (a.name || '').trim() === editTxForm.account_name.trim())
        ?.currency || ledgerBase
    ).toUpperCase()
    let finalAmountNum = totalAmountNum
    let finalBaseAmountNum = amountNum
    let finalFeeNum = feeNum
    let finalDiscountNum = discountNum
    let finalSplitsPayload = buildTxSplitsPayload(editTxForm)
    if (editTxForm.tx_type !== 'transfer' && accountCurrencyOfForm !== entryCurrency) {
      // baseAmountNum 用 amountNum,跟 TransactionsPanel.tsx::entryFxPreview
      // 显示给使用者看的换算基准一致(见 TransactionsPage.tsx 同款注释)。
      const effectiveRate = resolveEffectiveRate(
        editTxForm.fx_rate_override,
        editTxForm.fx_amount_override,
        amountNum,
        entryCurrency,
        accountCurrencyOfForm,
        editTxRates,
        ledgerBase
      )
      if (effectiveRate == null) {
        notifyError(new Error(t('transactions.error.rateMissing')))
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
    const effCurrency = editTxForm.tx_type !== 'transfer' ? accountCurrencyOfForm : entryCurrency
    let currencyFields: { currency_code?: string; native_amount?: number } = {}
    if (editTxForm.tx_type !== 'transfer') {
      try {
        const resolved = await resolveCurrencyFields({
          token,
          ledgerBase,
          currency: effCurrency,
          amount: finalAmountNum,
          originalCurrency: editTxForm.editingId
            ? editTxForm.original_currency
            : undefined
        })
        if (resolved) currencyFields = resolved
      } catch {
        notifyError(new Error(t('transactions.error.rateMissing')))
        return false
      }
    }

    // 跨幣別轉帳(2026-08):同 TransactionsPage.onSaveTransaction——轉出/
    // 轉入帳戶幣別不同時要算出 to_amount,否則後端會擋下這筆;幣別相同不送
    // 這個欄位。
    let transferToAmount: number | undefined
    if (editTxForm.tx_type === 'transfer') {
      const fromAccountRow = editTxAccounts.find(
        (a) => (a.name || '').trim().toLowerCase() === editTxForm.from_account_name.trim().toLowerCase(),
      )
      const toAccountRow = editTxAccounts.find(
        (a) => (a.name || '').trim().toLowerCase() === editTxForm.to_account_name.trim().toLowerCase(),
      )
      const fromCurrency = (fromAccountRow?.currency || ledgerBase).trim().toUpperCase()
      const toCurrency = (toAccountRow?.currency || ledgerBase).trim().toUpperCase()
      if (fromAccountRow && toAccountRow && fromCurrency !== toCurrency) {
        const effectiveRate = resolveEffectiveRate(
          editTxForm.fx_rate_override,
          editTxForm.fx_amount_override,
          totalAmountNum,
          fromCurrency,
          toCurrency,
          editTxRates,
          ledgerBase
        )
        if (effectiveRate == null) {
          notifyError(new Error(t('transactions.fx.rateMissing')))
          return false
        }
        transferToAmount = totalAmountNum * effectiveRate
      }
      // 跨幣別轉帳(2026-09-06 使用者反饋):同 TransactionsPage.onSaveTransaction
      // ——`native_amount`(轉出方折算帳本本位幣)是信用卡群組合併帳單「已繳
      // 金額」的幣別基準,跟 `to_amount`(轉入帳戶自身幣別)是兩個獨立概念,
      // 只看轉出帳戶幣別是否跟帳本本位幣一致,不吃 fx_rate_override/
      // fx_amount_override(那組覆蓋的是另一段換算)。
      if (fromAccountRow && fromCurrency !== ledgerBase.trim().toUpperCase()) {
        const native = convertBetween(totalAmountNum, fromCurrency, ledgerBase, editTxRates, ledgerBase)
        if (native != null) {
          currencyFields = { ...currencyFields, native_amount: native }
        }
      }
    }

    // 同 TransactionsPage.onSaveTransaction:account_id 必须跟 account_name
    // 一起送,否则后端只更新 accountName 显示字符串、不动真正的 accountId
    // 外键,造成名字/外键悄悄脱钩(这个 dialog 之前一直漏了这段,2026-08-02
    // 被使用者选中"主帳戶(account_group)"当帐户改这条时才暴露出来——create
    // 时后端 _assert_account_not_group 靠 account_id 才拦得住,update 若没
    // 送 account_id 就完全绕过校验)。
    const resolvedAccountId =
      editTxForm.tx_type === 'transfer'
        ? null
        : editTxAccounts.find(
            (a) => (a.name || '').trim().toLowerCase() === editTxForm.account_name.trim().toLowerCase(),
          )?.id || null
    const resolvedFromAccountId =
      editTxForm.tx_type === 'transfer'
        ? editTxAccounts.find(
            (a) => (a.name || '').trim().toLowerCase() === editTxForm.from_account_name.trim().toLowerCase(),
          )?.id || null
        : null
    const resolvedToAccountId =
      editTxForm.tx_type === 'transfer'
        ? editTxAccounts.find(
            (a) => (a.name || '').trim().toLowerCase() === editTxForm.to_account_name.trim().toLowerCase(),
          )?.id || null
        : null
    const resolvedCategoryId =
      editTxForm.tx_type === 'transfer'
        ? null
        : editTxCategories.find(
            (c) =>
              c.kind === editTxForm.tx_type &&
              (c.name || '').trim().toLowerCase() === editTxForm.category_name.trim().toLowerCase(),
          )?.id || null

    // Phase 1.5(§2.12.1):分期付款走独立的 createInstallmentPlan。
    const installmentPayload = !editTxForm.editingId
      ? buildInstallmentPlanPayload(editTxForm, { account_id: resolvedAccountId, category_id: resolvedCategoryId })
      : null

    const payload = {
      tx_type: editTxForm.tx_type,
      amount: finalAmountNum,
      // 手續費/折扣(2026-08 使用者需求):只在使用者有開啟這個功能時才送,
      // 沒開啟(一般交易)完全不影響 payload,server 端維持既有行為。
      // 轉帳手續費/折損(2026-08-29):同 TransactionsPage.tsx 的同款分流。
      ...(isTransferForFeeDiscount
        ? {
            ...(editTxForm.fee_enabled
              ? { fee_amount: finalFeeNum, fee_label: editTxForm.fee_label.trim() || null }
              : {}),
            ...(editTxForm.discount_enabled
              ? { discount_amount: finalDiscountNum, discount_label: editTxForm.discount_label.trim() || null }
              : {}),
          }
        : editTxForm.fee_enabled
          ? {
              base_amount: finalBaseAmountNum,
              fee_amount: finalFeeNum,
              fee_label: editTxForm.fee_label.trim() || null,
              discount_amount: finalDiscountNum,
              discount_label: editTxForm.discount_label.trim() || null,
            }
          : {}),
      happened_at: editTxForm.happened_at,
      // 延後入帳(§2.10 Phase 5):同 TransactionsPage.onSaveTransaction。
      deferred_posting_at: editTxForm.deferred_posting_at
        ? dateInputToIsoUtc(editTxForm.deferred_posting_at)
        : null,
      note: editTxForm.note.trim() || null,
      merchant: editTxForm.merchant.trim() || null,
      category_name:
        editTxForm.tx_type === 'transfer' || editTxForm.split_enabled
          ? null
          : editTxForm.category_name.trim() || null,
      category_kind:
        editTxForm.tx_type === 'transfer' ? null : editTxForm.tx_type,
      account_name:
        editTxForm.tx_type === 'transfer'
          ? null
          : editTxForm.account_name.trim() || null,
      account_id: editTxForm.tx_type === 'transfer' ? null : resolvedAccountId,
      from_account_name:
        editTxForm.tx_type === 'transfer'
          ? editTxForm.from_account_name.trim()
          : null,
      from_account_id: editTxForm.tx_type === 'transfer' ? resolvedFromAccountId : null,
      to_account_name:
        editTxForm.tx_type === 'transfer'
          ? editTxForm.to_account_name.trim()
          : null,
      to_account_id: editTxForm.tx_type === 'transfer' ? resolvedToAccountId : null,
      // 跨幣別轉帳(2026-08):同幣種轉帳(transferToAmount undefined)不送
      // 這個欄位,server 端 COALESCE(to_amount, amount) 回退,行為不變。
      ...(editTxForm.tx_type === 'transfer' && transferToAmount !== undefined
        ? { to_amount: transferToAmount }
        : {}),
      tags: editTxForm.tags.filter((s) => s.length > 0),
      attachments: editTxForm.attachments,
      // §三 标记按 type 条件落库:转账两者都置 false;收入只允许 stats;支出两者都允许。
      exclude_from_stats:
        editTxForm.tx_type === 'transfer' ? false : editTxForm.exclude_from_stats,
      exclude_from_budget:
        editTxForm.tx_type === 'expense' ? editTxForm.exclude_from_budget : false,
      // 退款(§2.6):同 TransactionsPage.onSaveTransaction —— income/expense
      // 都能是退款交易(income 也能被退款后,退款载体可以是 expense),只有
      // transfer 没有退款语意。
      refund_of_id:
        editTxForm.tx_type !== 'transfer' ? editTxForm.refund_of_id.trim() || null : null,
      debt_id:
        editTxForm.tx_type !== 'transfer' && editTxForm.debt_id !== '__new__'
          ? editTxForm.debt_id.trim() || null
          : null,
      // 專案(Phase 13):同 TransactionsPage.onSaveTransaction。
      project_id: editTxForm.tx_type !== 'transfer' ? editTxForm.project_id.trim() || null : null,
      // 信用卡紅利回饋(§2.9.5,2026-08-06 改版):同 TransactionsPage.onSaveTransaction。
      reward_rule_ids: editTxForm.tx_type === 'expense' ? editTxForm.reward_rule_ids : [],
      // Phase 1.5(§2.12.2):建交易当下顺便设成週期性收支起点,只在新建生效。
      recurring: !editTxForm.editingId ? buildRecurringInlinePayload(editTxForm) : null,
      // 拆帳(§2.4):disabled → 空数组(create 场景等同"没有 splits";update
      // 场景显式清空既有 splits,回到单一 category)。
      splits: finalSplitsPayload,
      ...currencyFields
    }

    try {
      if (installmentPayload) {
        await retryOnConflict(ledgerId, (base) =>
          createInstallmentPlan(token, ledgerId, base, installmentPayload),
        )
        notifySuccess(t('notice.txCreated'))
      } else if (editTxForm.editingId && editRecurringContext && editRecurringContext.mode === 'single') {
        // Phase 24「修改此記錄」:呼叫既有的 occurrence PATCH,只送這支端點
        // 支援的窄欄位子集(見 WriteRecurringOccurrenceUpdateRequest)——表
        // 單裡其它欄位(回饋項目/標籤/拆帳等)這個端點本來就不接受,不會被
        // 存到,是既有的已知限制,不在本次擴充範圍。
        const occurrencePayload: RecurringOccurrenceUpdatePayload = {
          amount: finalAmountNum,
          note: payload.note,
          category_id: resolvedCategoryId || undefined,
          account_id: resolvedAccountId || undefined,
          happened_at: payload.happened_at,
        }
        await retryOnConflict(ledgerId, (base) =>
          updateRecurringOccurrence(
            token, ledgerId, editRecurringContext.ruleId, editTxForm.editingId!, base, occurrencePayload,
          ),
        )
        notifySuccess(t('notice.txUpdated'))
      } else if (editTxForm.editingId && editRecurringContext && editRecurringContext.mode === 'future') {
        // Phase 24「修改連同未來週期」:呼叫 update-from,同樣只送這支端點
        // 支援的欄位子集(見 WriteRecurringUpdateFromRequest)——套用到規則
        // 本身 + 這期以後所有未 overridden 的已生成交易。
        const resolvedTagIds = editTxForm.tags
          .map(
            (name) =>
              editTxTags.find(
                (tag) => (tag.name || '').trim().toLowerCase() === name.trim().toLowerCase(),
              )?.id,
          )
          .filter((id): id is string => Boolean(id))
        const updateFromPayload: RecurringUpdateFromPayload = buildRecurringUpdateFromPayload({
          txPayload: payload as RecurringUpdateFromSource,
          categoryId: resolvedCategoryId,
          accountId: resolvedAccountId,
          fromAccountId: resolvedFromAccountId,
          toAccountId: resolvedToAccountId,
          tagIds: resolvedTagIds,
        })
        await retryOnConflict(ledgerId, (base) =>
          updateRecurringRuleFrom(
            token, ledgerId, editRecurringContext.ruleId, editTxForm.editingId!, base, updateFromPayload,
          ),
        )
        notifySuccess(t('notice.txUpdated'))
      } else if (editTxForm.editingId) {
        await retryOnConflict(ledgerId, (base) =>
          updateTransaction(token, ledgerId, editTxForm.editingId!, base, payload),
        )
        notifySuccess(t('notice.txUpdated'))
      } else {
        await retryOnConflict(ledgerId, (base) =>
          createTransaction(token, ledgerId, base, payload),
        )
        notifySuccess(t('notice.txCreated'))
      }
      // 借還款追蹤(體驗補強第二輪):同 TransactionsPage.tsx 的 onSaveTransaction
      // —— 这笔交易顺便建一笔新欠款,是旁支效果,交易已保存成功后单独发一次
      // createDebt,失败只提示、不影响已保存的交易。
      if (
        !installmentPayload &&
        editTxForm.tx_type !== 'transfer' &&
        editTxForm.debt_id === '__new__'
      ) {
        const newDebtCounterparty = editTxForm.new_debt_counterparty_name.trim()
        if (newDebtCounterparty) {
          try {
            await retryOnConflict(ledgerId, (base) =>
              createDebt(token, ledgerId, base, {
                direction: editTxForm.tx_type === 'income' ? 'payable' : 'receivable',
                counterparty_name: newDebtCounterparty,
                principal_amount: amountNum,
                due_at: editTxForm.new_debt_due_at
                  ? new Date(`${editTxForm.new_debt_due_at}T00:00:00Z`).toISOString()
                  : null,
                note: null,
              }),
            )
          } catch {
            toast.error(t('transactions.error.debtCreateFailed'), t('notice.error'))
          }
        }
      }
      return true
    } catch (err) {
      if (isWriteConflict(err)) {
        // 让其它页 refresh,这里只是关掉
      }
      notifyError(err)
      return false
    }
  }, [
    editTxLedgerId,
    editTxForm,
    editTxAccounts,
    editTxTags,
    editRecurringContext,
    editTxRates,
    token,
    retryOnConflict,
    isWriteConflict,
    t,
    toast,
    notifyError,
    notifySuccess,
  ])

  void currency

  // ==================== 分类编辑 ====================

  // 监听 openEditCategory:cat 自带 ledger_id,把 form 填上 + 拉一遍 ledger 内
  // 所有 categories(给 parent picker 用) + 打开
  // 同样 await 后再 open,parent picker 要的数据先到位
  useEffect(() => {
    return onOpenEditCategory(async (cat) => {
      const ledgerId = cat.ledger_id || writableLedgers[0]?.ledger_id || ''
      setEditCatLedgerId(ledgerId)
      setEditCatForm({
        editingId: cat.id,
        editingOwnerUserId: cat.created_by_user_id || '',
        name: cat.name,
        kind: cat.kind,
        level: String(cat.level ?? ''),
        sort_order: String(cat.sort_order ?? ''),
        icon: cat.icon || '',
        icon_type: cat.icon_type || 'material',
        custom_icon_path: cat.custom_icon_path || '',
        icon_cloud_file_id: cat.icon_cloud_file_id || '',
        icon_cloud_sha256: cat.icon_cloud_sha256 || '',
        parent_name: cat.parent_name || '',
        color: cat.color || '',
      })
      try {
        const cats = await fetchWorkspaceCategories(token, { ledgerId, limit: 500 })
        setEditCatRows(cats)
      } catch {
        setEditCatRows([])
      }
      setEditCatOpen(true)
    })
  }, [token, writableLedgers])

  const handleSaveCat = useCallback(async (): Promise<boolean> => {
    const ledgerId = editCatLedgerId.trim()
    if (!ledgerId) {
      notifyError(new Error(t('shell.selectLedgerFirst')))
      return false
    }
    try {
      const payload = {
        name: editCatForm.name,
        kind: editCatForm.kind,
        level: editCatForm.level ? Number(editCatForm.level) : null,
        sort_order: editCatForm.sort_order ? Number(editCatForm.sort_order) : null,
        icon: editCatForm.icon || null,
        icon_type: editCatForm.icon_type || null,
        custom_icon_path: editCatForm.custom_icon_path || null,
        icon_cloud_file_id: editCatForm.icon_cloud_file_id || null,
        icon_cloud_sha256: editCatForm.icon_cloud_sha256 || null,
        parent_name: editCatForm.parent_name || null,
        color: editCatForm.level === '2' ? null : editCatForm.color || null,
      }
      await retryOnConflict(ledgerId, (base) =>
        editCatForm.editingId
          ? updateCategory(token, ledgerId, editCatForm.editingId, base, payload)
          : createCategory(token, ledgerId, base, payload),
      )
      notifySuccess(
        editCatForm.editingId
          ? t('notice.categoryUpdated')
          : t('notice.categoryCreated'),
      )
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }, [
    editCatLedgerId,
    editCatForm,
    token,
    retryOnConflict,
    t,
    notifyError,
    notifySuccess,
  ])

  return (
    <>
      <TransactionsPanel
      dialogOnlyMode
      baseCurrency={editTxBase}
      currencyRates={editTxRates}
      form={editTxForm}
      rows={[]}
      total={0}
      page={1}
      pageSize={20}
      accounts={editTxAccounts}
      categories={editTxCategories}
      tags={editTxTags}
      debts={editTxDebts}
      canCreateDebt={editTxCanCreateDebt}
      projects={editTxProjects}
      onCreateProject={editTxCanManageProjects ? onCreateTxProject : undefined}
      rewardRules={editTxRewardRules}
      onCreateCategory={onCreateTxCategory}
      onCreateTag={onCreateTxTag}
      fetchCardRecommendations={handleFetchCardRecommendations}
      ledgerOptions={ledgerOptions}
      writeLedgerId={editTxLedgerId}
      onWriteLedgerIdChange={handleLedgerChange}
      onPageChange={() => undefined}
      onPageSizeChange={() => undefined}
      canWrite
      dictionariesLoading={refsLoading}
      onFormChange={setEditTxForm}
      dialogOpen={editTxOpen}
      onDialogOpenChange={setEditTxOpen}
      onSave={handleSaveTx}
      onReset={() => setEditTxForm(txDefaults())}
      onReload={() => undefined}
      onPreviewAttachment={async () => undefined}
      resolveAttachmentPreviewUrl={async () => null}
      iconPreviewUrlByFileId={iconPreviewByFileId}
      onEdit={() => undefined}
      onDelete={() => undefined}
    />
    <CategoriesPanel
      dialogOnlyMode
      form={editCatForm}
      rows={editCatRows}
      iconPreviewUrlByFileId={iconPreviewByFileId}
      canManage
      dialogOpen={editCatOpen}
      onDialogOpenChange={setEditCatOpen}
      onFormChange={setEditCatForm}
      onSave={handleSaveCat}
      onReset={() => setEditCatForm(categoryDefaults())}
      onEdit={() => undefined}
      onDelete={() => undefined}
      onUploadIcon={async (file) => {
        const ledgerId = editCatLedgerId.trim()
        if (!ledgerId) return null
        try {
          const out = await uploadAttachment(token, { ledger_id: ledgerId, file })
          return { fileId: out.file_id, sha256: out.sha256 }
        } catch (err) {
          notifyError(err)
          return null
        }
      }}
    />
    </>
  )
}
