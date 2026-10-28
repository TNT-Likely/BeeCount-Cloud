import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createCategory,
  createTransaction,
  fetchReadItems,
  fetchReadMembers,
  fetchReadTimeDeposits,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  updateCategory,
  updateTransaction,
  uploadAttachment,
  type AttachmentRef,
  type ReadItem,
  type ReadMember,
  type ReadTimeDeposit,
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
  type CategoryForm,
  type TxForm,
  discountFromWire,
  discountToWire,
  legsFromWire,
  legsToWire,
  sharesFromWire,
  sharesToWire,
  transferFeeFromWire,
  transferFeeToWire,
} from '@beecount/web-features'

import { useLedgerWrite } from '../app/useLedgerWrite'
import { useAttachmentPreview } from '../context/AttachmentPreviewContext'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAuth } from '../context/AuthContext'
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
  const { ledgers, currency, activeLedgerId } = useLedgers()
  const { previewMap: iconPreviewByFileId, resolvePreview } = useAttachmentCache()
  const previewAttachment = useAttachmentPreview()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const [editTxOpen, setEditTxOpen] = useState(false)
  const [editTxForm, setEditTxForm] = useState<TxForm>(txDefaults())
  const [editTxLedgerId, setEditTxLedgerId] = useState('')
  const [editTxAccounts, setEditTxAccounts] = useState<WorkspaceAccount[]>([])
  const [editTxCategories, setEditTxCategories] = useState<WorkspaceCategory[]>([])
  const [editTxTags, setEditTxTags] = useState<WorkspaceTag[]>([])
  // 0024 账本内自定义成员(支出账单人头分摊用)
  const [editTxMembers, setEditTxMembers] = useState<ReadMember[]>([])
  // 0029 归物候选(编辑弹窗的「关联物品」下拉)
  const [editTxItems, setEditTxItems] = useState<ReadItem[]>([])
  // 0030 定期存款候选(编辑弹窗的「关联定期存款」下拉,利息按键预填后展示)
  const [editTxDeposits, setEditTxDeposits] = useState<ReadTimeDeposit[]>([])
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
        const [accts, cats, tagsList, members, items, deposits] = await Promise.all([
          fetchWorkspaceAccounts(token, { ledgerId, limit: 500 }),
          fetchWorkspaceCategories(token, { ledgerId, limit: 500 }),
          fetchWorkspaceTags(token, { ledgerId, limit: 500 }),
          // 0024 成员(账本内自定义成员,给支出分摊用)。没有成员时返回空数组,
          // 编辑区就不展示分摊区块 —— 这条失败不阻断其它 refs。
          fetchReadMembers(token, ledgerId).catch(() => [] as ReadMember[]),
          // 0029 归物候选(给支出的「关联物品」下拉用),同上容错。
          fetchReadItems(token, ledgerId).catch(() => [] as ReadItem[]),
          // 0030 定期存款候选(给收入的「关联定期存款」下拉用),同上容错。
          fetchReadTimeDeposits(token, ledgerId).catch(() => [] as ReadTimeDeposit[]),
        ])
        setEditTxAccounts(accts)
        setEditTxCategories(cats)
        setEditTxTags(tagsList)
        setEditTxMembers(members)
        setEditTxItems(items)
        setEditTxDeposits(deposits)
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
      setEditTxForm({
        editingId: tx.id,
        editingOwnerUserId: tx.created_by_user_id || '',
        tx_type: tx.tx_type,
        amount: String(tx.amount),
        happened_at: tx.happened_at,
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
        // 0021 扩展字段回填(与 TransactionsPage.onEdit 同一套 wire→form 转换)
        discount: discountFromWire(tx.discount),
        transfer_fee: transferFeeFromWire(tx.transfer_fee),
        payment_merge_enabled:
          Array.isArray(tx.payment_legs) && tx.payment_legs.length > 0,
        payment_legs: legsFromWire(tx.payment_legs),
        // 0024 成员分摊回填(expense / income)
        member_split_enabled:
          Array.isArray(tx.member_shares) && tx.member_shares.length > 0,
        member_shares: sharesFromWire(tx.member_shares),
        // 0029 归物回填(仅 expense)
        item_id: tx.item_id || '',
        // 0030 定期存款利息回填(仅 income)
        time_deposit_id: tx.time_deposit_id || '',
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
      const ledgerId =
        prefill?.ledgerId ||
        (activeLedgerId &&
        writableLedgers.some((l) => l.ledger_id === activeLedgerId)
          ? activeLedgerId
          : writableLedgers[0]?.ledger_id) ||
        ''
      setEditTxLedgerId(ledgerId)
      const defaults = txDefaults()
      setEditTxForm({
        ...defaults,
        // 0030 利息按键 / 其它场景预填:交易类型 + 金额 + 账户 + 关联存款。
        tx_type: prefill?.txType || defaults.tx_type,
        category_kind: (prefill?.txType || defaults.tx_type),
        amount: prefill?.amount || defaults.amount,
        account_name: prefill?.accountName || defaults.account_name,
        time_deposit_id: prefill?.timeDepositId || defaults.time_deposit_id,
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
    const amountNum = Number((editTxForm.amount || '').toString().trim())
    if (!Number.isFinite(amountNum) || amountNum <= 0) {
      notifyError(new Error(t('transactions.error.amountInvalid')))
      return false
    }
    if (editTxForm.tx_type !== 'transfer' && !editTxForm.category_name.trim()) {
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

    // 0021 扩展字段:折扣/手续费/合并付款提交(与 TransactionsPage 同一口径,
    // form → camelCase wire,未启用/不合法 → null)
    const accountByName = new Map(
      editTxAccounts
        .filter((row) => (row.name || '').trim())
        .map((row) => [(row.name || '').trim().toLowerCase(), row.id] as const),
    )
    const isTransfer = editTxForm.tx_type === 'transfer'

    const payload = {
      tx_type: editTxForm.tx_type,
      amount: amountNum,
      happened_at: editTxForm.happened_at,
      note: editTxForm.note.trim() || null,
      category_name:
        editTxForm.tx_type === 'transfer'
          ? null
          : editTxForm.category_name.trim() || null,
      category_kind:
        editTxForm.tx_type === 'transfer' ? null : editTxForm.tx_type,
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
      ...currencyFields,
      discount: editTxForm.tx_type === 'expense'
        ? discountToWire(editTxForm.discount, amountNum)
        : null,
      transfer_fee: isTransfer
        ? transferFeeToWire(editTxForm.transfer_fee, accountByName)
        : null,
      payment_legs:
        editTxForm.tx_type === 'expense' && editTxForm.payment_merge_enabled
          ? legsToWire(editTxForm.payment_legs, amountNum, accountByName)
          : null,
      // 0024 成员分摊:expense / income + 开启时提交;未开启/不合法 → null
      member_shares:
        editTxForm.member_split_enabled
          ? sharesToWire(editTxForm.member_shares, amountNum)
          : null,
      // 0029 归物:仅 expense 可挂物品;不关联 → 显式 null(解除关联)
      item_id:
        editTxForm.tx_type === 'expense' ? editTxForm.item_id || null : null,
      // 0030 定期存款利息:仅 income 可挂存款(利息按键生成);不关联 → 显式 null
      time_deposit_id:
        editTxForm.tx_type === 'income' ? editTxForm.time_deposit_id || null : null,
    }

    try {
      if (editTxForm.editingId) {
        await retryOnConflict(ledgerId, (base) =>
          updateTransaction(token, ledgerId, editTxForm.editingId!, base, payload),
        )
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

  const editTxMemberOptions = useMemo(
    () => editTxMembers.map((m) => ({ id: m.id, name: m.name })),
    [editTxMembers],
  )
  const editTxItemOptions = useMemo(
    () =>
      editTxItems
        .filter((it) => (it.name || '').trim())
        .map((it) => ({ id: it.id, name: (it.name || '').trim() })),
    [editTxItems],
  )
  const editTxDepositOptions = useMemo(
    () =>
      editTxDeposits
        .filter((d) => (d.id || '').trim())
        .map((d) => ({
          id: d.id,
          // 下拉项展示:账户(币种) + 本金 + 利率,方便从多笔存款里挑出目标。
          name: `${d.account_name || t('timeDeposits.noAccount')} · ${(
            d.currency || 'CNY'
          ).trim()} ${(d.principal || 0).toFixed(2)} @ ${((d.annual_rate || 0) * 100).toFixed(2)}%`
        })),
    [editTxDeposits, t],
  )

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
      memberOptions={editTxMemberOptions}
      itemOptions={editTxItemOptions}
      depositOptions={editTxDepositOptions}
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
