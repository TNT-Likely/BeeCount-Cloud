import { useCallback, useEffect, useState } from 'react'

import {
  createCategory,
  createTransaction,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  updateCategory,
  updateTransaction,
  uploadAttachment,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  CategoriesPanel,
  categoryDefaults,
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
  const [editTxLedgerId, setEditTxLedgerId] = useState('')
  const [editTxAccounts, setEditTxAccounts] = useState<WorkspaceAccount[]>([])
  const [editTxCategories, setEditTxCategories] = useState<WorkspaceCategory[]>([])
  const [editTxTags, setEditTxTags] = useState<WorkspaceTag[]>([])
  const [refsLoading, setRefsLoading] = useState(false)

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
        const [accts, cats, tagsList] = await Promise.all([
          fetchWorkspaceAccounts(token, { ledgerId, limit: 500 }),
          fetchWorkspaceCategories(token, { ledgerId, limit: 500 }),
          fetchWorkspaceTags(token, { ledgerId, limit: 500 }),
        ])
        setEditTxAccounts(accts)
        setEditTxCategories(cats)
        setEditTxTags(tagsList)
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
      attachments: editTxForm.attachments,
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
      form={editTxForm}
      rows={[]}
      total={0}
      page={1}
      pageSize={20}
      accounts={editTxAccounts}
      categories={editTxCategories}
      tags={editTxTags}
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
