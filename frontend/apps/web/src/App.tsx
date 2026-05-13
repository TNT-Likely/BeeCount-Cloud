import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom'

import { API_BASE, clearStoredSession, configureHttp, getStoredUserId, refreshAuth } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { AppShell } from './app/AppShell'
import { RequireAuth } from './app/router'
import { LoginPage } from './pages/LoginPage'
import { clearCursor } from './state/sync-client'

// Section 页面全部懒加载 — 首屏只下载当前 route 需要的 chunk,显著降低
// 首次进入 /app/overview 的 JS 体积。每个页面会是独立 chunk,后续切到
// 其他 section 时按需 fetch。
const TransactionsPage = lazy(() =>
  import('./pages/sections/TransactionsPage').then((m) => ({ default: m.TransactionsPage })),
)
const AccountsPage = lazy(() =>
  import('./pages/sections/AccountsPage').then((m) => ({ default: m.AccountsPage })),
)
const AdminBackupPage = lazy(() =>
  import('./pages/sections/AdminBackupPage').then((m) => ({ default: m.AdminBackupPage })),
)
const AdminUsersPage = lazy(() =>
  import('./pages/sections/AdminUsersPage').then((m) => ({ default: m.AdminUsersPage })),
)
const BudgetsPage = lazy(() =>
  import('./pages/sections/BudgetsPage').then((m) => ({ default: m.BudgetsPage })),
)
const CalendarPage = lazy(() =>
  import('./pages/sections/CalendarPage').then((m) => ({ default: m.CalendarPage })),
)
const CategoriesPage = lazy(() =>
  import('./pages/sections/CategoriesPage').then((m) => ({ default: m.CategoriesPage })),
)
const LedgersPage = lazy(() =>
  import('./pages/sections/LedgersPage').then((m) => ({ default: m.LedgersPage })),
)
const OverviewPage = lazy(() =>
  import('./pages/sections/OverviewPage').then((m) => ({ default: m.OverviewPage })),
)
const SettingsAiPage = lazy(() =>
  import('./pages/sections/SettingsAiPage').then((m) => ({ default: m.SettingsAiPage })),
)
const SettingsDevicesPage = lazy(() =>
  import('./pages/sections/SettingsDevicesPage').then((m) => ({ default: m.SettingsDevicesPage })),
)
const SettingsPatsPage = lazy(() =>
  import('./pages/sections/SettingsPatsPage').then((m) => ({ default: m.SettingsPatsPage })),
)
const SettingsHealthPage = lazy(() =>
  import('./pages/sections/SettingsHealthPage').then((m) => ({ default: m.SettingsHealthPage })),
)
const SettingsProfilePage = lazy(() =>
  import('./pages/sections/SettingsProfilePage').then((m) => ({ default: m.SettingsProfilePage })),
)
const TagsPage = lazy(() =>
  import('./pages/sections/TagsPage').then((m) => ({ default: m.TagsPage })),
)
const ImportPage = lazy(() =>
  import('./pages/sections/ImportPage').then((m) => ({ default: m.ImportPage })),
)

/** 路由切换时的 Suspense fallback。section 切换通常 < 200ms,加个轻量
 *  loading shell 避免白屏闪烁。 */
function RouteFallback() {
  return (
    <div className="flex h-32 items-center justify-center">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-muted border-t-primary" />
    </div>
  )
}

const LEGACY_TOKEN_KEY = 'beecount.token'
const TOKEN_KEY = `beecount.token.${API_BASE}`

/**
 * 清掉 per-user 作用域的 localStorage 键 —— 仅限承载"账户数据缓存/选择"
 * 的键,不要碰 `primaryColor` / `theme` / `locale` 这些跨用户的偏好。
 * 多用户切换时避免 User A 残留的 activeLedger / txFilter 被 User B 读到。
 */
function clearUserScopedStorage(userId: string): void {
  if (typeof window === 'undefined' || !userId) return
  try {
    window.localStorage.removeItem(`beecount.active-ledger.${userId}`)
    const prefix = `beecount:web:txFilter:v1:${userId}:`
    const doomed: string[] = []
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i)
      if (key && key.startsWith(prefix)) doomed.push(key)
    }
    for (const key of doomed) window.localStorage.removeItem(key)
  } catch {
    // localStorage 在 private mode / 超配额时可能抛异常,忽略即可。
  }
}

export function App() {
  const t = useT()

  useEffect(() => {
    document.title = t('shell.docTitle')
  }, [t])

  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

function AppRoutes() {
  const navigate = useNavigate()
  const [token, setToken] = useState<string>(() => {
    const scoped = localStorage.getItem(TOKEN_KEY)
    if (scoped) return scoped
    return localStorage.getItem(LEGACY_TOKEN_KEY) || ''
  })

  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    } else {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    }
  }, [token])

  const handleLogout = useCallback(() => {
    const prev = getStoredUserId()
    if (prev) {
      clearCursor(prev)
      clearUserScopedStorage(prev)
    }
    clearStoredSession()
    setToken('')
    navigate('/login', { replace: true })
  }, [navigate])

  useEffect(() => {
    configureHttp({
      refreshToken: async () => {
        const fresh = await refreshAuth()
        setToken(fresh)
        return fresh
      },
      onLogout: handleLogout
    })
    return () => {
      configureHttp({ refreshToken: null, onLogout: null })
    }
  }, [handleLogout])

  // Nested routes:AppShell 作为 /app 父路由的 element,其 <Outlet /> 渲染
  // 当前子路由,切换 section 时 AppShell 不 unmount —— profileMe / ledgers
  // 等全局数据跨页面保持。所有 section 都有独立 Page,挂到 Outlet 下。
  const shellElement = (
    <RequireAuth isAuthed={!!token}>
      <AppShell token={token} onLogout={handleLogout} />
    </RequireAuth>
  )

  return (
    <Routes>
      <Route
        path="/login"
        element={
          token ? (
            <Navigate to="/app/overview" replace />
          ) : (
            <LoginPage
              onLoggedIn={(nextToken) => {
                setToken(nextToken)
                navigate('/app/overview', { replace: true })
              }}
            />
          )
        }
      />
      <Route path="/app" element={shellElement}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route
          path="overview"
          element={
            <Suspense fallback={<RouteFallback />}>
              <OverviewPage />
            </Suspense>
          }
        />
        <Route
          path="transactions"
          element={
            <Suspense fallback={<RouteFallback />}>
              <TransactionsPage />
            </Suspense>
          }
        />
        <Route
          path="calendar"
          element={
            <Suspense fallback={<RouteFallback />}>
              <CalendarPage />
            </Suspense>
          }
        />
        <Route
          path="ledgers"
          element={
            <Suspense fallback={<RouteFallback />}>
              <LedgersPage />
            </Suspense>
          }
        />
        <Route
          path="budgets"
          element={
            <Suspense fallback={<RouteFallback />}>
              <BudgetsPage />
            </Suspense>
          }
        />
        <Route
          path="accounts"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AccountsPage />
            </Suspense>
          }
        />
        <Route
          path="categories"
          element={
            <Suspense fallback={<RouteFallback />}>
              <CategoriesPage />
            </Suspense>
          }
        />
        <Route
          path="tags"
          element={
            <Suspense fallback={<RouteFallback />}>
              <TagsPage />
            </Suspense>
          }
        />
        <Route
          path="import"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ImportPage />
            </Suspense>
          }
        />
        <Route
          path="admin/users"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminUsersPage />
            </Suspense>
          }
        />
        <Route
          path="admin/backup"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminBackupPage />
            </Suspense>
          }
        />
        <Route
          path="settings/profile"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsProfilePage />
            </Suspense>
          }
        />
        <Route
          path="settings/appearance"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsProfilePage />
            </Suspense>
          }
        />
        <Route
          path="settings/ai"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsAiPage />
            </Suspense>
          }
        />
        <Route
          path="settings/health"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsHealthPage />
            </Suspense>
          }
        />
        <Route
          path="settings/devices"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsDevicesPage />
            </Suspense>
          }
        />
        <Route
          path="settings/developer"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsPatsPage />
            </Suspense>
          }
        />
        {/* legacy 深链 /app/:ledgerId/... 目前直接 fall-through 到 transactions */}
        <Route path="*" element={<Navigate to="/app/overview" replace />} />
      </Route>
      <Route path="/" element={<Navigate to={token ? '/app/overview' : '/login'} replace />} />
      <Route path="*" element={<Navigate to={token ? '/app/overview' : '/login'} replace />} />
    </Routes>
  )
}

// LegacyAppPage 和 useLegacyRoute 桥已随阶段 3 T15 移除 —— 所有 section 都是
// 独立 Page,直接挂到 react-router 的 Outlet 下。
