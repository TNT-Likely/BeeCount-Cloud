import { useCallback, useEffect, useState } from 'react'

import {
  createCategory,
  createReimbursementItem,
  createTransaction,
  deleteReimbursementItem,
  fetchCompanies,
  fetchItems,
  fetchFlowLinks,
  fetchReadMembers,
  fetchReimbursementItemByTx,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  updateCategory,
  updateTransaction,
  uploadAttachment,
  type AttachmentRef,
  type Company,
  type Item,
  type ReadMember,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  resolveCurrencyFields,
  loadRatesToBase,
  CategoriesPanel,
  categoryDefaults,
  TransactionsPanel,
  uploadTransactionImage,
  txDefaults,
  buildMemberSplits,
  memberSplitsValid,
  buildPaymentSplits,
  paymentSplitsValid,
  type CategoryForm,
  type TxForm,
} from '@beecount/web-features'

import { useLedgerWrite } from '../app/useLedgerWrite'
import { useAttachmentPreview } from '../context/AttachmentPreviewContext'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAuth } from '../context/AuthContext'
import { useLock } from '../context/LockContext'
import { useLedgers } from '../context/LedgersContext'
import { localizeError } from '../i18n/errors'
import { onOpenEditCategory, onOpenEditTx, onOpenNewTx } from '../lib/txDialogEvents'

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
  const { isTxLocked } = useLock()
  const { ledgers, currency, activeLedgerId } = useLedgers()
  const { previewMap: iconPreviewByFileId, resolvePreview } = useAttachmentCache()
  const previewAttachment = useAttachmentPreview()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const [editTxOpen, setEditTxOpen] = useState(false)
  const [editTxForm, setEditTxForm] = useState<TxForm>(txDefaults())
  const [editTxLedgerId, setEditTxLedgerId] = useState('')
  // 报销(0035)：编辑时记录原报销项 id，用于开/关报销标记同步
  const [editTxRbItemId, setEditTxRbItemId] = useState<string | null>(null)
  const [editTxAccounts, setEditTxAccounts] = useState<WorkspaceAccount[]>([])
  const [editTxCategories, setEditTxCategories] = useState<WorkspaceCategory[]>([])
  const [editTxTags, setEditTxTags] = useState<WorkspaceTag[]>([])
  const [editTxMembers, setEditTxMembers] = useState<ReadMember[]>([])
  // 报销公司维度(0037)：用户级公司档案，勾选报销时必选
  const [companies, setCompanies] = useState<Company[]>([])
  // 0044:可选关联的物品(编辑账单时选物品)。
  const [itemOptions, setItemOptions] = useState<Item[]>([])
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

  const loadRefsForLedger = useCallback(
    async (ledgerId: string) => {
      if (!ledgerId) return
      setRefsLoading(true)
      try {
        const [accts, cats, tagsList, memberRows, companyRows, itemRows] = await Promise.all([
          fetchWorkspaceAccounts(token, { ledgerId, limit: 500 }),
          fetchWorkspaceCategories(token, { ledgerId, limit: 500 }),
          fetchWorkspaceTags(token, { ledgerId, limit: 500 }),
          fetchReadMembers(token, ledgerId).catch(() => [] as ReadMember[]),
          fetchCompanies(token).catch(() => [] as Company[]),
          // 物品(0044):ledger-scoped,拉不到不阻断编辑。
          fetchItems(token, ledgerId).catch(() => [] as Item[]),
        ])
        setEditTxAccounts(accts)
        setEditTxCategories(cats)
        setEditTxTags(tagsList)
        setEditTxMembers(memberRows)
        setCompanies(companyRows)
        setItemOptions(itemRows)
      } catch (err) {
        notifyError(err)
      } finally {
        setRefsLoading(false)
      }
    },
    [token, notifyError],
  )

  // 监听全局 openEditTx 事件 — 任何页 dispatch 都会被这里接住
  // 先 await fetch refs 再 open dialog,避免下拉空数据闪现
  useEffect(() => {
    return onOpenEditTx(async (tx) => {
      const ledgerId =
        tx.ledger_id || writableLedgers[0]?.ledger_id || ''
      setEditTxLedgerId(ledgerId)
      // 报销(0035)：查该交易是否已登记为报销项（网络失败不阻断编辑）
      let rbItemId: string | null = null
      let rbCompanyId = ''
      let rbCompanyName = ''
      try {
        const rb = await fetchReimbursementItemByTx(token, tx.id)
        rbItemId = rb?.id ?? null
        rbCompanyId = rb?.company_id || ''
        rbCompanyName = rb?.company_name || ''
      } catch { /* ignore */ }
      setEditTxRbItemId(rbItemId)
      // 0044:回填该笔账单已关联的物品(流水 → 物品的反查)。
      let linkedItemId = ''
      try {
        const links = await fetchFlowLinks(token, tx.id, ledgerId)
        linkedItemId = links.links.find((l) => l.kind === 'item')?.target.item_id || ''
      } catch { /* ignore */ }
      setEditTxForm({
        editingId: tx.id,
        editingOwnerUserId: tx.created_by_user_id || '',
        tx_type: tx.tx_type,
        amount: String(tx.amount),
        happened_at: tx.happened_at,
        item_id: linkedItemId,
        // v30 多币种:回显该笔币种 + 原币种(提交时币种未变不发字段,金额
        // 变更折算由 server L14 隐含汇率联动,防快照漂移)
        currency: (tx.currency_code || '').toUpperCase(),
        original_currency: (tx.currency_code || '').toUpperCase(),
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
                .map((s) => s.trim())
                .filter((s) => s.length > 0),
        attachments: Array.isArray(tx.attachments) ? tx.attachments : [],
        exclude_from_stats: Boolean(tx.exclude_from_stats),
        exclude_from_budget: Boolean(tx.exclude_from_budget),
        is_reimbursement: rbItemId !== null,
        reimbursement_company_id: rbCompanyId,
        reimbursement_company_name: rbCompanyName,
        refund_of_sync_id: tx.refund_of_sync_id || null,
        transfer_fee_amount: tx.transfer_fee_amount != null && tx.transfer_fee_amount > 0 ? String(tx.transfer_fee_amount) : '',
        transfer_discount_amount: tx.transfer_discount_amount != null && tx.transfer_discount_amount > 0 ? String(tx.transfer_discount_amount) : '',
        transfer_fee_account_name: tx.transfer_fee_account_name || '',
        discount_amount: tx.discount_amount != null && tx.discount_amount > 0 ? String(tx.discount_amount) : '',
        member_id: tx.member_splits && tx.member_splits.length === 1 ? tx.member_splits[0].member_id : '',
        split_enabled: Boolean(tx.member_splits && tx.member_splits.length > 1),
        member_splits: (tx.member_splits || []).length > 1
          ? (tx.member_splits || []).map((s) => ({
              member_id: s.member_id,
              name: s.name || '',
              amount: String(s.amount)
            }))
          : [],
        // 支出组合付款(0028):编辑时回填各付款渠道
        payment_split_enabled: Boolean(tx.payment_splits && tx.payment_splits.length > 1),
        payment_splits: (tx.payment_splits || []).length > 1
          ? (tx.payment_splits || []).map((s) => ({
              account_id: s.account_id || '',
              account_name: s.account_name || '',
              amount: String(s.amount ?? '')
            }))
          : []
      })
      // 等 refs 拉完再打开 dialog,确保 category/account/tag 下拉有数据
      await loadRefsForLedger(ledgerId)
      setEditTxOpen(true)
    })
  }, [loadRefsForLedger, writableLedgers, token])

  // 监听全局 openNewTx 事件 — CmdK / CalendarPage / 任意页 dispatch 都接住,
  // 不再要求触发方先 navigate 到 transactions 再 dispatch。
  // prefill.happenedAt:CalendarPage 选中某日 + 点新增时填充;
  // prefill.ledgerId:CalendarPage 把当前账本带过来;CmdK 不传走 active ledger。
  useEffect(() => {
    return onOpenNewTx(async (prefill) => {
      const ledgerId =
        prefill?.ledgerId ||
        (activeLedgerId &&
        writableLedgers.some((l) => l.ledger_id === activeLedgerId)
          ? activeLedgerId
          : writableLedgers[0]?.ledger_id) ||
        ''
      setEditTxLedgerId(ledgerId)
      setEditTxRbItemId(null)
      const defaults = txDefaults()
      setEditTxForm({
        ...defaults,
        happened_at: prefill?.happenedAt || defaults.happened_at,
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

  const handleSaveTx = useCallback(async (attachments: AttachmentRef[] = editTxForm.attachments): Promise<boolean> => {
    const ledgerId = editTxLedgerId.trim()
    if (!ledgerId) {
      notifyError(new Error(t('transactions.error.ledgerRequired')))
      return false
    }
    if (editTxForm.editingId && isTxLocked(editTxForm.happened_at)) {
      notifyError(new Error(t('settings.lock.txBlocked')))
      return false
    }
    const amountNum = Number((editTxForm.amount || '').toString().trim())
    if (!Number.isFinite(amountNum) || amountNum <= 0) {
      notifyError(new Error(t('transactions.error.amountInvalid')))
      return false
    }
    // 报销公司维度(0037)：勾选报销必须选择公司。
    if (editTxForm.tx_type === 'expense' && editTxForm.is_reimbursement && !editTxForm.reimbursement_company_id) {
      notifyError(new Error(t('txFlagReimbursementCompanyRequired')))
      return false
    }
    // 支出优惠(0023):与 TransactionsPage 同一校验口径。
    const expenseDiscountRaw = editTxForm.tx_type === 'expense' ? editTxForm.discount_amount.trim() : ''
    const expenseDiscountNum = expenseDiscountRaw !== '' ? Number(expenseDiscountRaw) : null
    if (expenseDiscountNum !== null && (!Number.isFinite(expenseDiscountNum) || expenseDiscountNum < 0 || expenseDiscountNum > amountNum)) {
      notifyError(new Error(t('transactions.error.discountInvalid')))
      return false
    }
    // 成员分摊(0024):与 TransactionsPage 同一校验口径。
    const memberNameById = new Map(editTxMembers.map((m) => [m.id, m.name] as const))
    const memberSplits = buildMemberSplits(editTxForm, memberNameById)
    if (editTxForm.split_enabled && editTxForm.tx_type !== 'transfer') {
      if (editTxForm.member_splits.some((row) => !row.member_id || Number(row.amount) <= 0)) {
        notifyError(new Error(t('members.error.splitIncomplete')))
        return false
      }
      if (!memberSplitsValid(memberSplits, amountNum)) {
        notifyError(new Error(t('members.error.splitMismatch')))
        return false
      }
    }
    // 支出组合付款(0028):与 TransactionsPage 同一校验口径。
    const accountNameById = new Map(editTxAccounts.map((a) => [a.id, a.name] as const))
    const paymentSplits = buildPaymentSplits(editTxForm, accountNameById)
    if (editTxForm.payment_split_enabled && editTxForm.tx_type === 'expense') {
      if (editTxForm.payment_splits.some((row) => !row.account_id || Number(row.amount) <= 0)) {
        notifyError(new Error(t('paymentSplit.error.incomplete')))
        return false
      }
      const ids = editTxForm.payment_splits.map((r) => r.account_id).filter(Boolean)
      if (new Set(ids).size !== ids.length) {
        notifyError(new Error(t('paymentSplit.error.duplicateAccount')))
        return false
      }
      if (paymentSplits.length < 2 || !paymentSplitsValid(paymentSplits, amountNum)) {
        notifyError(new Error(t('paymentSplit.error.mismatch')))
        return false
      }
    }
    if (editTxForm.tx_type !== 'transfer' && editTxForm.tx_type !== 'refund' && !editTxForm.category_name.trim()) {
      notifyError(new Error(t('transactions.error.categoryRequired')))
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

    // v30 多币种:共享 helper(override 口径/编辑防漂移/改回本位币),与
    // TransactionsPage 提交完全同一实现。
    const ledgerBase = (
      ledgers.find((l) => l.ledger_id === ledgerId)?.currency || 'CNY'
    )
      .trim()
      .toUpperCase()
    const accountCurrencyOfForm = editTxAccounts
      .find((a) => (a.name || '').trim() === editTxForm.account_name.trim())
      ?.currency?.toUpperCase()
    const effCurrency = (
      editTxForm.currency ||
      (editTxForm.tx_type !== 'transfer' ? accountCurrencyOfForm : '') ||
      ledgerBase
    ).toUpperCase()
    let currencyFields: { currency_code?: string; native_amount?: number } = {}
    if (editTxForm.tx_type !== 'transfer') {
      try {
        const resolved = await resolveCurrencyFields({
          token,
          ledgerBase,
          currency: effCurrency,
          amount: amountNum,
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

    const transferFeeAmount = editTxForm.tx_type === 'transfer' && editTxForm.transfer_fee_amount.trim() !== ''
      ? Number(editTxForm.transfer_fee_amount)
      : null
    const transferDiscountAmount = editTxForm.tx_type === 'transfer' && editTxForm.transfer_discount_amount.trim() !== ''
      ? Number(editTxForm.transfer_discount_amount)
      : null
    const feeAccountName = editTxForm.tx_type === 'transfer' ? editTxForm.transfer_fee_account_name.trim() : ''
    const categoryKind: 'expense' | 'income' | null =
      editTxForm.tx_type === 'transfer'
        ? null
        : editTxForm.tx_type === 'income'
          ? 'income'
          : 'expense'
    const payload = {
      tx_type: editTxForm.tx_type,
      amount: amountNum,
      happened_at: editTxForm.happened_at,
      note: editTxForm.note.trim() || null,
      category_name:
        editTxForm.tx_type === 'transfer' || editTxForm.tx_type === 'refund'
          ? null
          : editTxForm.category_name.trim() || null,
      category_kind:
        editTxForm.tx_type === 'refund' ? null : categoryKind,
      account_name:
        editTxForm.tx_type === 'transfer'
          ? null
          : editTxForm.account_name.trim() || null,
      from_account_name:
        editTxForm.tx_type === 'transfer'
          ? editTxForm.from_account_name.trim()
          : null,
      to_account_name:
        editTxForm.tx_type === 'transfer'
          ? editTxForm.to_account_name.trim()
          : null,
      tags: editTxForm.tags.filter((s) => s.length > 0),
      attachments,
      // §三 标记按 type 条件落库:转账两者都置 false;收入只允许 stats;支出两者都允许。
      exclude_from_stats:
        editTxForm.tx_type === 'transfer' ? false : editTxForm.exclude_from_stats,
      exclude_from_budget:
        editTxForm.tx_type === 'expense' ? editTxForm.exclude_from_budget : false,
      ...(editTxForm.tx_type === 'transfer'
        ? {
            transfer_fee_amount: Number.isFinite(transferFeeAmount as number) && (transferFeeAmount as number) > 0 ? transferFeeAmount : null,
            transfer_discount_amount: Number.isFinite(transferDiscountAmount as number) && (transferDiscountAmount as number) > 0 ? transferDiscountAmount : null,
            transfer_fee_account_name: feeAccountName || null,
            transfer_fee_account_id: feeAccountName ? (editTxAccounts.find(a => (a.name || '').trim().toLowerCase() === feeAccountName.toLowerCase())?.id || null) : null,
          }
        : {}),
      // 支出优惠(0023):总是传,非 expense 传 null 以清除编辑时的旧值。
      discount_amount: expenseDiscountNum !== null && expenseDiscountNum > 0 ? expenseDiscountNum : null,
      // 成员关联/分摊(0024):总是传,空数组 = 清除。
      member_splits: memberSplits,
      // 支出组合付款(0028):总是传,空数组 = 清除/恢复单一账户。
      payment_splits: paymentSplits,
      // 退款(0025):编辑退款时回传原单关联,后端据此保留冲减语义。
      // 报销(0035)：仅支出可标记；新建时后端会自动登记待报销项
      is_reimbursement: editTxForm.tx_type === 'expense' ? editTxForm.is_reimbursement : false,
      ...(editTxForm.tx_type === 'expense' && editTxForm.is_reimbursement
        ? {
            reimbursement_company_id: editTxForm.reimbursement_company_id || null,
            reimbursement_company_name: editTxForm.reimbursement_company_name || null,
          }
        : {}),
      ...(editTxForm.tx_type === 'refund'
        ? { refund_of_sync_id: editTxForm.refund_of_sync_id || null }
        : {}),
      // 关联物品(0044):仅支出/收入可关联;空串表示「不关联 / 清除」。
      item_id:
        editTxForm.tx_type === 'expense' || editTxForm.tx_type === 'income'
          ? editTxForm.item_id || ''
          : '',
      ...currencyFields
    }

    try {
      if (editTxForm.editingId) {
        await retryOnConflict(ledgerId, (base) =>
          updateTransaction(token, ledgerId, editTxForm.editingId!, base, payload),
        )
        // 报销标记变更：编辑模式后端 update 不处理，这里显式同步报销项
        const wanted = editTxForm.tx_type === 'expense' && editTxForm.is_reimbursement
        if (wanted) {
          const acc = editTxAccounts.find((a) => (a.name || '').trim() === editTxForm.account_name.trim())
          // 重复登记 = 幂等 upsert：编辑时同步公司/金额等快照
          await createReimbursementItem(token, {
            ledger_id: ledgerId,
            tx_sync_id: editTxForm.editingId,
            amount: amountNum,
            happened_at: editTxForm.happened_at,
            note: editTxForm.note.trim() || null,
            category_name: editTxForm.category_name.trim() || null,
            account_id: acc?.id || null,
            account_name: editTxForm.account_name.trim() || null,
            currency_code: editTxForm.currency || null,
            attachments,
            company_id: editTxForm.reimbursement_company_id,
            company_name: editTxForm.reimbursement_company_name,
          })
        } else if (!wanted && editTxRbItemId) {
          try { await deleteReimbursementItem(token, editTxRbItemId) } catch { /* 已报销则忽略 */ }
        }
        notifySuccess(t('notice.transactionUpdated'))
      } else {
        await retryOnConflict(ledgerId, (base) =>
          createTransaction(token, ledgerId, base, payload),
        )
        notifySuccess(t('notice.transactionCreated'))
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
    editTxRbItemId,
    editTxAccounts,
    token,
    retryOnConflict,
    isWriteConflict,
    t,
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
        parent_sync_id: cat.parent_sync_id || '',
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
        parent_sync_id: editCatForm.parent_sync_id || null,
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
      accounts={editTxAccounts.filter((a) => {
        // 币种优先联动:账户下拉只显示「表单所选币种(默认=账本主币种)」的
        // 账户,防止选出币种与账户不一致的组合(与 TransactionsPage 同规则)
        const wanted = (editTxForm.currency || editTxBase).toUpperCase()
        return ((a.currency || 'CNY').trim().toUpperCase()) === wanted
      })}
      categories={editTxCategories}
      tags={editTxTags}
      members={editTxMembers}
      companies={companies}
      items={itemOptions}
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
      onUploadAttachment={(file) => uploadTransactionImage(token, editTxLedgerId, file)}
      onReset={() => setEditTxForm(txDefaults())}
      onReload={() => undefined}
      onPreviewAttachment={previewAttachment}
      resolveAttachmentPreviewUrl={(ref) => resolvePreview(ref.cloudFileId || '')}
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
