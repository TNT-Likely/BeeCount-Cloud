import { useCallback, useEffect, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

import {
  ApiError,
  fetchAdminUsers,
  fetchProfileMe,
  fetchReadLedgers,
  fetchWorkspaceCategories,
  type ProfileMe,
  type ReadLedger,
} from '@beecount/api-client'
import { usePrimaryColor, useT } from '@beecount/ui'
import type { AppSection } from '@beecount/web-features'

import { AboutDialog } from '../components/AboutDialog'
import { PwaInstallBanner } from '../components/PwaInstallBanner'
import { PwaUpdateBanner } from '../components/PwaUpdateBanner'
import { GlobalAskDialog } from '../components/cmdk-ai/GlobalAskDialog'
import { GlobalParseTxDialog } from '../components/cmdk-ai/GlobalParseTxDialog'
import { GlobalEditDialogs } from '../components/GlobalEditDialogs'
import { GlobalSharedLedgerDialogs } from '../components/GlobalSharedLedgerDialogs'
import { GlobalEntityDialogs } from '../components/GlobalEntityDialogs'
import { LogsDialog } from '../components/LogsDialog'
import { MobileBottomNav } from '../components/MobileBottomNav'
import { AttachmentCacheProvider, useAttachmentCache } from '../context/AttachmentCacheContext'
import { AuthProvider } from '../context/AuthContext'
import { LedgersProvider } from '../context/LedgersContext'
import { SharedLedgerResourcesProvider } from '../context/SharedLedgerResourcesContext'
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
 *   5. 挂 LogsDialog / AboutDialog(全局 dialog,任意 section 都能开)
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
  const [loadedShellUserId, setLoadedShellUserId] = useState<string | null>()
  const [shellErrorUserId, setShellErrorUserId] = useState<string | null>()
  const [shellLoadAttempt, setShellLoadAttempt] = useState(0)
  const [logsOpen, setLogsOpen] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const sessionUserId = useMemo(() => jwtUserId(token), [token])
  // access token 定期刷新时 user id 不变,不应重新闪一次骨架屏；只有首次进入
  // 或真正切换账号时才重新建立 shell 启动边界。
  const shellLoading = loadedShellUserId !== sessionUserId
  const shellLoadFailed = shellLoading && shellErrorUserId === sessionUserId

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
    const isInitialBootstrap = loadedShellUserId !== sessionUserId
    const run = async () => {
      const results = await Promise.allSettled([refreshLedgers(), refreshProfile()])
      if (cancelled) return
      const errors = results.flatMap((result) =>
        result.status === 'rejected' ? [result.reason] : [],
      )
      const authError = errors.find(
        (err) => err instanceof ApiError && (err.status === 401 || err.status === 403),
      )
      if (authError) {
        onLogout()
        return
      }
      if (errors.length > 0) {
        // eslint-disable-next-line no-console
        console.warn('[AppShell] initial load error', errors)
        // token 轮换后的后台刷新失败不能卸载已经工作的页面；首次加载失败
        // 则停在明确的 error/retry 状态,不能把 [] 误当成“确实没有账本”。
        if (isInitialBootstrap) setShellErrorUserId(sessionUserId)
        return
      }
      setShellErrorUserId(undefined)
      setLoadedShellUserId(sessionUserId)
    }
    void run()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, shellLoadAttempt])

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
        {shellLoading ? (
          <AppLayout
            header={
              <AppHeader
                loading
                onOpenLogs={() => setLogsOpen(true)}
                onOpenAbout={() => setAboutOpen(true)}
              />
            }
          >
            {shellLoadFailed ? (
              <InitialShellError
                onRetry={() => {
                  // 立即卸载 retry 按钮、切回 skeleton，避免连续点击叠加多组
                  // profile + ledgers 请求。
                  setShellErrorUserId(undefined)
                  setShellLoadAttempt((value) => value + 1)
                }}
              />
            ) : (
              <InitialShellSkeleton />
            )}
          </AppLayout>
        ) : (
          <SyncSocketProvider>
            <PageDataCacheProvider>
              <AttachmentCacheProvider>
                <SharedLedgerResourcesProvider>
                  <CategoryIconPrefetcher token={token} />
                  <AppShellSyncReactor
                    refreshLedgers={refreshLedgers}
                    refreshProfile={refreshProfile}
                    applyServerPrimaryColor={applyServerPrimaryColor}
                  />
                  <AppLayout
                    header={
                      <AppHeader
                        onOpenLogs={() => setLogsOpen(true)}
                        onOpenAbout={() => setAboutOpen(true)}
                      />
                    }
                  >
                    <div className="space-y-4 pb-20 md:pb-0">
                      <Outlet />
                    </div>
                    <MobileBottomNav
                      activeSection={currentSection}
                      onNavigate={handleSectionNavigate}
                    />
                  </AppLayout>
                  <LogsDialog token={token} open={logsOpen} onOpenChange={setLogsOpen} />
                  <AboutDialog open={aboutOpen} onOpenChange={setAboutOpen} />
                  <PwaUpdateBanner />
                  <PwaInstallBanner />
                  <GlobalEntityDialogs />
                  <GlobalEditDialogs />
                  <GlobalAskDialog />
                  <GlobalParseTxDialog />
                  <GlobalSharedLedgerDialogs />
                </SharedLedgerResourcesProvider>
              </AttachmentCacheProvider>
            </PageDataCacheProvider>
          </SyncSocketProvider>
        )}
      </LedgersProvider>
    </AuthProvider>
  )
}

/**
 * profile + ledgers 是整个 Web app 的启动边界。它们完成前不挂载 Outlet、
 * WebSocket/poller 和页面预取，避免 Overview 的十余个并发请求抢占这两条
 * 首屏关键请求；同时明确告诉用户页面仍在加载，而不是显示成空账号。
 */
function InitialShellSkeleton() {
  const t = useT()
  return (
    <div className="space-y-4 pb-20 md:pb-0" aria-busy="true" role="status">
      <span className="sr-only">{t('common.loading')}</span>
      <div className="grid gap-4 md:grid-cols-3">
        {[0, 1, 2].map((key) => (
          <div key={key} className="card h-28 animate-pulse bg-muted/60" aria-hidden="true" />
        ))}
      </div>
      <div className="card h-72 animate-pulse bg-muted/60" aria-hidden="true" />
    </div>
  )
}

function InitialShellError({ onRetry }: { onRetry: () => void }) {
  const t = useT()
  return (
    <div className="card mx-auto max-w-lg space-y-4 p-8 text-center" role="alert">
      <p className="text-sm text-muted-foreground">{t('shell.initialLoadError')}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
      >
        {t('shell.initialLoadRetry')}
      </button>
    </div>
  )
}

function applyIncomeColorScheme(incomeIsRed: boolean) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.incomeColor = incomeIsRed ? 'red' : 'green'
}

/**
 * 在 AppShell 挂载时一次性预热所有分类自定义图标 — 拉一次 categories,
 * 把所有 cloud icon fileId 灌给 AttachmentCache,让任何后续 page(交易页 /
 * 分类页 / 预算页 / 概览页)进入时图标已经在内存里,**消除"晚出来"闪烁**。
 *
 * AttachmentCache.ensureLoadedMany 内部:
 *   - 去重:同一 fileId 只 fetch 一次
 *   - dedupe inflight:并发 N 个相同 fileId 只走 1 个网络请求
 *   - 已加载就立即 noop
 * 所以这里调一次 = 全局预热,跟 page 内重复调用零冲突。
 */
function CategoryIconPrefetcher({ token }: { token: string }) {
  const { ensureLoadedMany } = useAttachmentCache()
  useEffect(() => {
    if (!token) return
    let cancelled = false
    fetchWorkspaceCategories(token, { limit: 500 })
      .then((rows) => {
        if (cancelled) return
        const ids = rows
          .map((row) => (row.icon_cloud_file_id || '').trim())
          .filter((value) => value.length > 0)
        if (ids.length > 0) ensureLoadedMany(ids)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [token, ensureLoadedMany])
  return null
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
  // §7 共享账本:member_change(joined/role_changed/removed)可能改动用户的
  // 可见 ledger 集合(被加入新共享账本 / 被踢)。刷一次列表让 sidebar 立即
  // 反映。SharedLedgerResources cache 单独由 SharedLedgerResourcesProvider
  // 监听同一事件做 per-ledger invalidate。
  useSyncEvent('member_change', () => {
    void refreshLedgers()
  })

  return null
}
