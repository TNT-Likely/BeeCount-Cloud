import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createTag,
  deleteTag,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  updateTag,
  type ReadTag,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  ConfirmDialog,
  TagsPanel,
  tagDefaults,
  type TagForm,
} from '@beecount/web-features'

import { dispatchOpenDetailTag } from '../../lib/txDialogEvents'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'

const TAG_DETAIL_PAGE_SIZE = 20

/**
 * 标签管理页 —— 列表 + 统计(每个标签的笔数/收入/支出,server 一次性汇好)
 * + CRUD(带 ConfirmDialog 二次确认删除)+ 点击标签弹 TagDetailDialog
 * 无限滚动加载该标签下的交易。
 */
export function TagsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const [rows, setRows] = usePageCache<WorkspaceTag[]>('tags:rows', [])
  const [form, setForm] = useState<TagForm>(tagDefaults())
  const [pendingDelete, setPendingDelete] = useState<{ id: string; name: string } | null>(null)

  // detail 弹窗已迁到 GlobalEntityDialogs(AppShell 顶层),本页只 dispatch
  // openDetailTag 事件,弹窗在全局渲染。

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
      setRows(await fetchWorkspaceTags(token, { limit: 500 }))
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

  const tagStatsById = useMemo(() => {
    const out: Record<string, { count: number; expense: number; income: number }> = {}
    for (const tag of rows) {
      if (!tag.id) continue
      out[tag.id] = {
        count: tag.tx_count ?? 0,
        expense: tag.expense_total ?? 0,
        income: tag.income_total ?? 0,
      }
    }
    return out
  }, [rows])

  const onSave = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    try {
      const payload = { name: form.name, color: form.color || null }
      await retryOnConflict(activeLedgerId, (base) =>
        form.editingId
          ? updateTag(token, activeLedgerId, form.editingId, base, payload)
          : createTag(token, activeLedgerId, base, payload)
      )
      setForm(tagDefaults())
      await refresh()
      notifySuccess(form.editingId ? t('notice.tagUpdated') : t('notice.tagCreated'))
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
        deleteTag(token, activeLedgerId, pendingDelete.id, base)
      )
      await refresh()
      notifySuccess(t('notice.tagDeleted'))
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    } finally {
      setPendingDelete(null)
    }
  }


  return (
    <>
      <TagsPanel
        form={form}
        rows={rows}
        canManage
        statsById={tagStatsById}
        onFormChange={setForm}
        onCreate={() => setForm(tagDefaults())}
        onSave={onSave}
        onReset={() => setForm(tagDefaults())}
        onEdit={(row) => {
          setForm({
            editingId: row.id,
            editingOwnerUserId: row.created_by_user_id || '',
            name: row.name,
            color: row.color || '#F59E0B',
          })
        }}
        onDelete={(row) => {
          // 关联交易 > 0 直接拦,不让走 confirm dialog,跟 app 端行为对齐。
          // server 也有兜底校验(snapshot_mutator.delete_tag)防止漏网。
          const linkedCount = tagStatsById[row.id]?.count ?? 0
          if (linkedCount > 0) {
            toast.error(
              t('tags.error.hasTransactions').replace('{count}', String(linkedCount)),
              t('notice.error')
            )
            return
          }
          setPendingDelete({ id: row.id, name: row.name })
        }}
        onClickTag={(row) => dispatchOpenDetailTag(row as WorkspaceTag)}
      />
      {/* TagDetailDialog 已迁到 GlobalEntityDialogs */}
      <ConfirmDialog
        open={!!pendingDelete}
        title={t('confirm.deleteTag.title')}
        description={
          pendingDelete ? t('confirm.deleteTag.desc').replace('{name}', pendingDelete.name) : ''
        }
        confirmText={t('confirm.delete')}
        cancelText={t('confirm.cancel')}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
    </>
  )
}
