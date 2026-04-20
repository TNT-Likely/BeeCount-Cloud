import { useCallback, useEffect, useState } from 'react'

import {
  fetchAdminHealth,
  fetchAdminOverview,
  type AdminHealth,
  type AdminOverview,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import { SettingsHealthSection } from '../../components/sections/SettingsHealthSection'
import { useAuth } from '../../context/AuthContext'
import { useSyncEvent } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'

/**
 * 健康页 —— 任何登录用户都能看 /health ping(server 运行状态);管理员额外
 * 看到 overview 的全局使用统计(user / ledger / tx 总数)。
 *
 * 数据每次进入页面拉一次;用户点"刷新"也重拉。
 */
export function SettingsHealthPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [health, setHealth] = useState<AdminHealth | null>(null)
  const [overview, setOverview] = useState<AdminOverview | null>(null)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    try {
      const [h, ov] = await Promise.all([
        fetchAdminHealth(token),
        isAdmin ? fetchAdminOverview(token) : Promise.resolve<AdminOverview | null>(null),
      ])
      setHealth(h)
      setOverview(ov)
    } catch (err) {
      notifyError(err)
    }
  }, [token, isAdmin, notifyError])

  useEffect(() => {
    if (!isAdminResolved) return
    void refresh()
  }, [isAdminResolved, refresh])

  // backup_restore 会把所有数据面回档,刷新概览统计很有必要。
  useSyncEvent('backup_restore', () => {
    void refresh()
  })

  return (
    <SettingsHealthSection
      adminHealth={health}
      adminOverview={overview}
      onRefresh={() => void refresh()}
    />
  )
}
