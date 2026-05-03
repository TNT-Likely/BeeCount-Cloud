import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createCategory,
  deleteCategory,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  updateCategory,
  uploadAttachment,
  type ReadCategory,
  type WorkspaceCategory,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  CategoriesPanel,
  ConfirmDialog,
  categoryDefaults,
  type CategoryForm,
} from '@beecount/web-features'

import { useLedgerWrite } from '../../app/useLedgerWrite'
import { CategoryDetailDialog } from '../../components/dialogs/CategoryDetailDialog'
import { onOpenDetailCategory } from '../../lib/txDialogEvents'
import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'

/**
 * 分类管理页 —— 分类列表 + CRUD + 自定义图标的 preview URL 解析(拿
 * icon_cloud_file_id 去 downloadAttachment,转成 objectURL 给 CategoriesPanel
 * 的 `iconPreviewUrlByFileId` 用)。
 *
 * 图标 preview cache 随 Page unmount 时主动 revokeObjectURL 释放,避免
 * 长时间在该页停留积累 blob。
 */
export function CategoriesPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()

  const [rows, setRows] = usePageCache<WorkspaceCategory[]>('categories:rows', [])
  const [form, setForm] = useState<CategoryForm>(categoryDefaults())
  const [pendingDelete, setPendingDelete] = useState<{ id: string; name: string } | null>(null)
  // 编辑 dialog 受控开关 — CategoriesPanel 行编辑、CategoryDetailDialog 联动
  // 编辑都通过这个 state 触发;由 panel 内部 onCreate/onEdit 也会切到 true。
  const [editDialogOpen, setEditDialogOpen] = useState(false)

  // 分类详情弹窗 — 跟 AccountDetailDialog / TagDetailDialog 模式对齐:
  // 选中分类 → 顶部展示分类信息 + 该分类下交易列表 + 底部 Edit/Delete
  const [detail, setDetail] = useState<WorkspaceCategory | null>(null)
  const [detailTx, setDetailTx] = useState<WorkspaceTransaction[]>([])
  const [detailTotal, setDetailTotal] = useState(0)
  const [detailOffset, setDetailOffset] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailTags, setDetailTags] = useState<WorkspaceTag[]>([])

  const CATEGORY_DETAIL_PAGE_SIZE = 50

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
      setRows(await fetchWorkspaceCategories(token, { limit: 500 }))
    } catch (err) {
      notifyError(err)
    }
  }, [token, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  // 通过共享 AttachmentCache 惰性加载自定义图标。rows 更新时把所有
  // icon_cloud_file_id push 给 context,context 内部自己去重 + dedupe inflight。
  useEffect(() => {
    const ids = rows
      .map((row) => row.icon_cloud_file_id || '')
      .filter((value) => value.trim().length > 0)
    if (ids.length > 0) ensureLoadedMany(ids)
  }, [rows, ensureLoadedMany])

  const txCountById = useMemo(() => {
    const out: Record<string, number> = {}
    for (const row of rows) {
      if (!row.id) continue
      out[row.id] = row.tx_count ?? 0
    }
    return out
  }, [rows])

  const onSave = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    try {
      const payload = {
        name: form.name,
        kind: form.kind,
        level: form.level ? Number(form.level) : null,
        sort_order: form.sort_order ? Number(form.sort_order) : null,
        icon: form.icon || null,
        icon_type: form.icon_type || null,
        custom_icon_path: form.custom_icon_path || null,
        icon_cloud_file_id: form.icon_cloud_file_id || null,
        icon_cloud_sha256: form.icon_cloud_sha256 || null,
        parent_name: form.parent_name || null,
      }
      await retryOnConflict(activeLedgerId, (base) =>
        form.editingId
          ? updateCategory(token, activeLedgerId, form.editingId, base, payload)
          : createCategory(token, activeLedgerId, base, payload)
      )
      setForm(categoryDefaults())
      await refresh()
      notifySuccess(form.editingId ? t('notice.categoryUpdated') : t('notice.categoryCreated'))
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const loadDetailPage = useCallback(
    async (categorySyncId: string, offset: number) => {
      setDetailLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          categorySyncId,
          limit: CATEGORY_DETAIL_PAGE_SIZE,
          offset,
        })
        setDetailTx((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setDetailTotal(page.total)
        setDetailOffset(offset + page.items.length)
      } catch (err) {
        notifyError(err)
      } finally {
        setDetailLoading(false)
      }
    },
    [token, notifyError],
  )

  const openDetail = useCallback(
    (row: WorkspaceCategory) => {
      setDetail(row)
      setDetailTx([])
      setDetailTotal(0)
      setDetailOffset(0)
      void loadDetailPage(row.id, 0)
      // 加载 tag 颜色字典(detail 弹窗内交易行渲染 tag chip 用)
      fetchWorkspaceTags(token, { limit: 500 })
        .then(setDetailTags)
        .catch(() => undefined)
    },
    [loadDetailPage, token],
  )

  const closeDetail = () => {
    setDetail(null)
    setDetailTx([])
    setDetailTotal(0)
    setDetailOffset(0)
  }

  const enterEdit = useCallback((row: ReadCategory) => {
    setForm({
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
      parent_name: row.parent_name || '',
    })
    setEditDialogOpen(true)
  }, [])

  // CommandPalette 派发的「打开分类详情」事件 → 详情弹窗(非直接编辑)
  useEffect(() => {
    return onOpenDetailCategory((row) => {
      openDetail(row)
    })
  }, [openDetail])

  const confirmDelete = async () => {
    if (!pendingDelete || !activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteCategory(token, activeLedgerId, pendingDelete.id, base)
      )
      await refresh()
      notifySuccess(t('notice.categoryDeleted'))
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    } finally {
      setPendingDelete(null)
    }
  }

  return (
    <>
      <CategoriesPanel
        form={form}
        rows={rows}
        iconPreviewUrlByFileId={iconPreviewByFileId}
        txCountById={txCountById}
        canManage
        dialogOpen={editDialogOpen}
        onDialogOpenChange={setEditDialogOpen}
        onFormChange={setForm}
        onCreate={() => setForm(categoryDefaults())}
        onSave={onSave}
        onReset={() => setForm(categoryDefaults())}
        onEdit={enterEdit}
        onDelete={(row) => {
          // 跟 mobile + AccountsPage 对齐:有关联交易 / 子分类 → 拒删,要求
          // 用户先迁移这些数据。比"允许删除并 orphan 子分类/交易"更严格。
          const ws =
            (rows.find((r) => r.id === row.id) as WorkspaceCategory | undefined) ||
            (row as WorkspaceCategory)
          const txCount = ws.tx_count ?? 0
          if (txCount > 0) {
            toast.error(
              t('categories.delete.blockedByTransactions', {
                name: ws.name,
                count: txCount,
              }),
              t('notice.error'),
            )
            return
          }
          const childCount = rows.filter(
            (r) =>
              r.id !== ws.id &&
              r.parent_name === ws.name &&
              r.kind === ws.kind,
          ).length
          if (childCount > 0) {
            toast.error(
              t('categories.delete.blockedByChildren', {
                name: ws.name,
                count: childCount,
              }),
              t('notice.error'),
            )
            return
          }
          setPendingDelete({ id: ws.id, name: ws.name })
        }}
        onUploadIcon={async (file) => {
          if (!activeLedgerId) {
            toast.error(t('accounts.error.ledgerRequired'), t('notice.error'))
            return null
          }
          try {
            const out = await uploadAttachment(token, { ledger_id: activeLedgerId, file })
            return { fileId: out.file_id, sha256: out.sha256 }
          } catch (err) {
            notifyError(err)
            return null
          }
        }}
      />
      <ConfirmDialog
        open={!!pendingDelete}
        title={t('confirm.deleteCategory.title')}
        description={
          pendingDelete
            ? t('confirm.deleteCategory.desc').replace('{name}', pendingDelete.name)
            : ''
        }
        confirmText={t('confirm.delete')}
        cancelText={t('confirm.cancel')}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
      <CategoryDetailDialog
        category={detail}
        transactions={detailTx}
        total={detailTotal}
        offset={detailOffset}
        loading={detailLoading}
        tags={detailTags}
        iconPreviewUrlByFileId={iconPreviewByFileId}
        onClose={closeDetail}
        onLoadMore={(syncId, offset) => void loadDetailPage(syncId, offset)}
        onEdit={(row) => {
          closeDetail()
          enterEdit(row)
        }}
      />
    </>
  )
}
