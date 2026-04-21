import { useCallback, useEffect, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

import {
  ApiError,
  fetchAdminUsers,
  fetchProfileMe,
  fetchReadLedgers,
  type ProfileMe,
  type ReadLedger,
} from '@beecount/api-client'
import { usePrimaryColor } from '@beecount/ui'
import type { AppSection } from '@beecount/web-features'

import { ChangelogDialog } from '../components/ChangelogDialog'
import { LogsDialog } from '../components/LogsDialog'
import { MobileBottomNav } from '../components/MobileBottomNav'
import { AttachmentCacheProvider } from '../context/AttachmentCacheContext'
import { AuthProvider } from '../context/AuthContext'
import { LedgersProvider } from '../context/LedgersContext'
import { PageDataCacheProvider } from '../context/PageDataCacheContext'
import { SyncSocketProvider, useSyncEvent } from '../context/SyncSocketContext'
import { AppLayout } from '../layout/AppLayout'
import { jwtUserId } from '../state/jwt'
import { parseRoute, routePath } from '../state/router'
import { AppHeader } from './AppHeader'

interface Props {
  token: string
  onLogout: () => void
}

/**
 * /app/* 所有路由的外壳 —— 阶段 3 的数据边界 + 布局边界。
 *
 * AppShell 负责:
 *   1. fetch profileMe + ledgers + 管理员探测
 *   2. 管理 activeLedgerId per-user localStorage 持久化
 *   3. 提供 AuthProvider + LedgersProvider
 *   4. 渲染 AppLayout + AppHeader + <Outlet /> + MobileBottomNav
 *   5. 挂 LogsDialog / ChangelogDialog(全局 dialog,任意 section 都能开)
 *
 * 各 section Page 只渲染 content,切换时 shell / header / dialog 不 unmount。
 */
export function AppShell({ token, onLogout }: Props) {
  const navigate = useNavigate()
  const location = useLocation()
  const { applyServerColor: applyServerPrimaryColor } = usePrimaryColor()

  const [ledgers, setLedgers] = useState<ReadLedger[]>([])
  const [profileMe, setProfileMe] = useState<ProfileMe | null>(null)
  const [isAdmin, setIsAdmin] = useState(false)
  const [isAdminResolved, setIsAdminResolved] = useState(false)
  const [logsOpen, setLogsOpen] = useState(false)
  const [changelogOpen, setChangelogOpen] = useState(false)
  const sessionUserId = useMemo(() => jwtUserId(token), [token])

  const activeLedgerStorageKey = useMemo(
    () => `beecount.active-ledger.${sessionUserId || 'anon'}`,
    [sessionUserId]
  )
  const [activeLedgerId, setActiveLedgerIdRaw] = useState<string>(() => {
    if (typeof window === 'undefined') return ''
    try {
      return window.localStorage.getItem(activeLedgerStorageKey) || ''
    } catch {
      return ''
    }
  })

  const setActiveLedgerId = useCallback(
    (next: string) => {
      setActiveLedgerIdRaw(next)
      try {
        if (next) {
          window.localStorage.setItem(activeLedgerStorageKey, next)
        } else {
          window.localStorage.removeItem(activeLedgerStorageKey)
        }
      } catch {
        // localStorage 在 private mode / 超配额时可能抛异常,忽略即可。
      }
    },
    [activeLedgerStorageKey]
  )

  const reconcileActiveLedger = useCallback(
    (rows: ReadLedger[]) => {
      if (rows.length === 0) {
        if (activeLedgerId) setActiveLedgerId('')
        return
      }
      if (activeLedgerId && rows.some((r) => r.ledger_id === activeLedgerId)) {
        return
      }
      setActiveLedgerId(rows[0].ledger_id)
    },
    [activeLedgerId, setActiveLedgerId]
  )

  const refreshLedgers = useCallback(async () => {
    const rows = await fetchReadLedgers(token)
    setLedgers(rows)
    reconcileActiveLedger(rows)
  }, [token, reconcileActiveLedger])

  const refreshProfile = useCallback(async () => {
    const row = await fetchProfileMe(token)
    setProfileMe(row)
    applyIncomeColorScheme(row.income_is_red ?? true)
    applyServerPrimaryColor(row.theme_primary_color)
  }, [token, applyServerPrimaryColor])

  useEffect(() => {
    if (!token) return
    let cancelled = false
    const run = async () => {
      try {
        await Promise.all([refreshLedgers(), refreshProfile()])
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          onLogout()
        }
        // eslint-disable-next-line no-console
        console.warn('[AppShell] initial load error', err)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    if (!token) return
    let cancelled = false
    const run = async () => {
      try {
        await fetchAdminUsers(token, { limit: 1 })
        if (!cancelled) setIsAdmin(true)
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && (err.status === 403 || err.status === 401)) {
          setIsAdmin(false)
        } else {
          // eslint-disable-next-line no-console
          console.warn('[AppShell] admin probe error', err)
        }
      } finally {
        if (!cancelled) setIsAdminResolved(true)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [token])

  useEffect(() => {
    if (!isAdminResolved) return
    if (!isAdmin && location.pathname.startsWith('/app/admin')) {
      navigate('/app/overview', { replace: true })
    }
  }, [isAdmin, isAdminResolved, location.pathname, navigate])

  const currentSection: AppSection = useMemo(() => {
    const parsed = parseRoute(location.pathname)
    return parsed.kind === 'app' ? parsed.section : 'transactions'
  }, [location.pathname])

  const handleSectionNavigate = useCallback(
    (section: AppSection) => {
      navigate(routePath({ kind: 'app', ledgerId: '', section }))
    },
    [navigate]
  )

  return (
    <AuthProvider
      token={token}
      profileMe={profileMe}
      sessionUserId={sessionUserId}
      isAdmin={isAdmin}
      isAdminResolved={isAdminResolved}
      refreshProfile={refreshProfile}
      logout={onLogout}
    >
      <LedgersProvider
        ledgers={ledgers}
        activeLedgerId={activeLedgerId}
        setActiveLedgerId={setActiveLedgerId}
        refreshLedgers={refreshLedgers}
      >
        <SyncSocketProvider>
        <PageDataCacheProvider>
        <AttachmentCacheProvider>
        <AppShellSyncReactor
          refreshLedgers={refreshLedgers}
          refreshProfile={refreshProfile}
          applyServerPrimaryColor={applyServerPrimaryColor}
        />
          <AppLayout
            header={
              <AppHeader
                onOpenLogs={() => setLogsOpen(true)}
                onOpenChangelog={() => setChangelogOpen(true)}
              />
            }
          >
            <div className="space-y-4 pb-20 md:pb-0">
              <Outlet />
            </div>
            <MobileBottomNav
              activeSection={currentSection}
              isAdmin={isAdmin}
              onOpenChangelog={() => setChangelogOpen(true)}
              onNavigate={handleSectionNavigate}
              onLogout={onLogout}
            />
          </AppLayout>
          <LogsDialog token={token} open={logsOpen} onOpenChange={setLogsOpen} />
          <ChangelogDialog open={changelogOpen} onOpenChange={setChangelogOpen} />
        </AttachmentCacheProvider>
        </PageDataCacheProvider>
        </SyncSocketProvider>
      </LedgersProvider>
    </AuthProvider>
  )
}

function applyIncomeColorScheme(incomeIsRed: boolean) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.incomeColor = incomeIsRed ? 'red' : 'green'
}

/**
 * 订阅全局同步事件,给 Shell 层做响应:
 *   - `profile_change`:
 *       优先用 payload 里的字段(比 refetch 快)短路应用收支配色 + 主题色
 *       然后 refreshProfile() 拿完整最新 profileMe。
 *   - `sync_change` / `backup_restore` / `sync_change_batch`:
 *       任何数据改动都可能牵扯到 ledgers 列表(新建/删除/重命名),refreshLedgers。
 *       各 Page 自己订阅这几类事件来 refresh 自己的列表。
 *
 * 这个组件放在 SyncSocketProvider + AuthProvider 内部才能拿到 context,所以
 * 独立抽出来 —— 父组件体里调 hooks 不方便穿过 Provider 边界。
 */
function AppShellSyncReactor({
  refreshLedgers,
  refreshProfile,
  applyServerPrimaryColor,
}: {
  refreshLedgers: () => Promise<void>
  refreshProfile: () => Promise<void>
  applyServerPrimaryColor: (color: string | null | undefined) => void
}) {
  useSyncEvent('profile_change', (payload) => {
    const p = payload as {
      income_is_red?: boolean | null
      theme_primary_color?: string | null
    }
    if (typeof p.income_is_red === 'boolean') {
      applyIncomeColorScheme(p.income_is_red)
    }
    if (p.theme_primary_color) {
      applyServerPrimaryColor(p.theme_primary_color)
    }
    void refreshProfile()
  })

  useSyncEvent('sync_change', () => {
    void refreshLedgers()
  })
  useSyncEvent('backup_restore', () => {
    void refreshLedgers()
  })
  useSyncEvent('sync_change_batch', () => {
    void refreshLedgers()
  })

  return null
}
