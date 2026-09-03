import { useCallback, useEffect, useState } from 'react'

import {
  fetchAdminHealth,
  fetchAdminOverview,
  fetchRagIndexStatus,
  refreshRagIndex,
  type AdminHealth,
  type AdminOverview,
  type RagIndexStatus,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import { SettingsHealthSection } from '../../components/sections/SettingsHealthSection'
import { useAuth } from '../../context/AuthContext'
import { useSyncEvent } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'

/**
 * 健康页 —— 任何登录用户都能看 /health ping(server 运行状态);管理员额外
 * 看到 overview 的全局使用统计。
 *
 * 数据完整性扫描已迁出到独立 admin 页(/app/admin/data-cleanup),不再嵌在
 * 此页底部。
 */
export function SettingsHealthPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [health, setHealth] = useState<AdminHealth | null>(null)
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [ragStatus, setRagStatus] = useState<RagIndexStatus | null>(null)
  const [isRefreshingRag, setIsRefreshingRag] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    try {
      const [h, ov, rag] = await Promise.allSettled([
        fetchAdminHealth(token),
        isAdmin ? fetchAdminOverview(token) : Promise.resolve<AdminOverview | null>(null),
        fetchRagIndexStatus(token, { checkLatest: true }),
      ])
      if (h.status === 'fulfilled') setHealth(h.value)
      if (ov.status === 'fulfilled') setOverview(ov.value)
      if (rag.status === 'fulfilled') setRagStatus(rag.value)
      if (h.status === 'rejected') notifyError(h.reason)
    } catch (err) {
      notifyError(err)
    }
  }, [token, isAdmin, notifyError])

  const refreshRag = useCallback(async () => {
    setIsRefreshingRag(true)
    try {
      setRagStatus(await refreshRagIndex(token))
    } catch (err) {
      notifyError(err)
    } finally {
      setIsRefreshingRag(false)
    }
  }, [token, notifyError])

  useEffect(() => {
    if (!isAdminResolved) return
    void refresh()
  }, [isAdminResolved, refresh])

  useSyncEvent('backup_restore', () => {
    void refresh()
  })

  return (
    <SettingsHealthSection
      adminHealth={health}
      adminOverview={overview}
      ragStatus={ragStatus}
      isRefreshingRag={isRefreshingRag}
      onRefresh={() => void refresh()}
      onRefreshRag={() => void refreshRag()}
    />
  )
}
