import { useCallback, useEffect, useState } from 'react'

import {
  fetchAdminHealth,
  fetchAdminIntegrityScan,
  fetchAdminOverview,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  type AdminHealth,
  type AdminIntegrityIssueSample,
  type AdminIntegrityScan,
  type AdminOverview,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import { SettingsHealthSection } from '../../components/sections/SettingsHealthSection'
import { useAuth } from '../../context/AuthContext'
import { useSyncEvent } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import {
  dispatchOpenDetailAccount,
  dispatchOpenDetailCategory,
  dispatchOpenDetailTag,
  dispatchOpenDetailTx,
} from '../../lib/txDialogEvents'

/**
 * 健康页 —— 任何登录用户都能看 /health ping(server 运行状态);管理员额外
 * 看到 overview 的全局使用统计 + 数据完整性扫描结果。
 *
 * 进页面自动并发拉 health / overview / integrity 三组数据。完整性扫描结果
 * 里点 sample → 跳到对应页面 + 派发 detail 事件 → 复用之前的详情弹窗。
 */
export function SettingsHealthPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [health, setHealth] = useState<AdminHealth | null>(null)
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [integrity, setIntegrity] = useState<AdminIntegrityScan | null>(null)
  const [integrityLoading, setIntegrityLoading] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    try {
      const [h, ov, integrityResult] = await Promise.allSettled([
        fetchAdminHealth(token),
        isAdmin ? fetchAdminOverview(token) : Promise.resolve<AdminOverview | null>(null),
        isAdmin
          ? fetchAdminIntegrityScan(token)
          : Promise.resolve<AdminIntegrityScan | null>(null),
      ])
      if (h.status === 'fulfilled') setHealth(h.value)
      if (ov.status === 'fulfilled') setOverview(ov.value)
      if (integrityResult.status === 'fulfilled') setIntegrity(integrityResult.value)
      if (h.status === 'rejected') notifyError(h.reason)
    } catch (err) {
      notifyError(err)
    }
  }, [token, isAdmin, notifyError])

  const refreshIntegrity = useCallback(async () => {
    if (!isAdmin) return
    setIntegrityLoading(true)
    try {
      const result = await fetchAdminIntegrityScan(token)
      setIntegrity(result)
    } catch (err) {
      notifyError(err)
    } finally {
      setIntegrityLoading(false)
    }
  }, [token, isAdmin, notifyError])

  // 进页面自动跑(包括 integrity 扫描)
  useEffect(() => {
    if (!isAdminResolved) return
    void refresh()
  }, [isAdminResolved, refresh])

  // backup_restore 后所有数据回档,刷新概览 + 重新扫描完整性
  useSyncEvent('backup_restore', () => {
    void refresh()
  })

  /**
   * sample 点击 → fetch 实体 → 派发 detail 事件,弹窗在 GlobalEntityDialogs
   * 渲染。**不跳页**,弹窗直接在 health 页之上展开。
   * 附件类问题不开弹窗(没有附件详情弹窗),只 toast 提示去 admin 后台清理。
   */
  const handleJumpToSample = useCallback(
    async (issueType: string, sample: AdminIntegrityIssueSample) => {
      if (
        issueType.startsWith('orphan_tx_') ||
        issueType === 'future_tx' ||
        issueType === 'zero_amount_tx'
      ) {
        try {
          const page = await fetchWorkspaceTransactions(token, {
            txSyncId: sample.sync_id,
            limit: 1,
          })
          const tx = page.items[0]
          if (!tx) {
            toast.error(t('admin.integrity.row.notFound'), t('notice.error'))
            return
          }
          dispatchOpenDetailTx(tx)
        } catch (err) {
          notifyError(err)
        }
        return
      }
      if (issueType === 'unused_category') {
        try {
          const list = await fetchWorkspaceCategories(token, { limit: 500 })
          const cat = list.find((c) => c.id === sample.sync_id)
          if (!cat) {
            toast.error(t('admin.integrity.row.notFound'), t('notice.error'))
            return
          }
          dispatchOpenDetailCategory(cat)
        } catch (err) {
          notifyError(err)
        }
        return
      }
      if (issueType === 'unused_account') {
        try {
          const list = await fetchWorkspaceAccounts(token, { limit: 500 })
          const account = list.find((a) => a.id === sample.sync_id)
          if (!account) {
            toast.error(t('admin.integrity.row.notFound'), t('notice.error'))
            return
          }
          dispatchOpenDetailAccount(account)
        } catch (err) {
          notifyError(err)
        }
        return
      }
      if (issueType === 'unused_tag') {
        try {
          const list = await fetchWorkspaceTags(token, { limit: 500 })
          const tagItem = list.find((g) => g.id === sample.sync_id)
          if (!tagItem) {
            toast.error(t('admin.integrity.row.notFound'), t('notice.error'))
            return
          }
          dispatchOpenDetailTag(tagItem)
        } catch (err) {
          notifyError(err)
        }
        return
      }
      // orphan_attachment 暂无详情弹窗,toast 提示去清理
      if (issueType === 'orphan_attachment') {
        toast.success(
          t('admin.integrity.row.attachmentHint', { id: sample.sync_id }),
          t('notice.success'),
        )
      }
    },
    [token, notifyError, toast, t],
  )

  return (
    <SettingsHealthSection
      adminHealth={health}
      adminOverview={overview}
      integrity={integrity}
      integrityLoading={integrityLoading}
      onRefresh={() => void refresh()}
      onRefreshIntegrity={() => void refreshIntegrity()}
      onJumpToSample={handleJumpToSample}
    />
  )
}
