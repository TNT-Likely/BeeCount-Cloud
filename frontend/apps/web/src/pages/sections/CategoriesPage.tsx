import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createCategory,
  deleteCategory,
  fetchWorkspaceCategories,
  updateCategory,
  uploadAttachment,
  type ReadCategory,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  CategoriesPanel,
  ConfirmDialog,
  categoryDefaults,
  type CategoryForm,
} from '@beecount/web-features'

import { useLedgerWrite } from '../../app/useLedgerWrite'
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
        onFormChange={setForm}
        onCreate={() => setForm(categoryDefaults())}
        onSave={onSave}
        onReset={() => setForm(categoryDefaults())}
        onEdit={(row) => {
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
        }}
        onDelete={(row) => setPendingDelete({ id: row.id, name: row.name })}
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
    </>
  )
}
