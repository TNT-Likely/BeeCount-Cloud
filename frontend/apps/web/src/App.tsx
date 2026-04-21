import { useCallback, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom'

import { API_BASE, clearStoredSession, configureHttp, getStoredUserId, refreshAuth } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { AppShell } from './app/AppShell'
import { RequireAuth } from './app/router'
import { LoginPage } from './pages/LoginPage'
import { TransactionsPage } from './pages/sections/TransactionsPage'
import { AccountsPage } from './pages/sections/AccountsPage'
import { AdminUsersPage } from './pages/sections/AdminUsersPage'
import { BudgetsPage } from './pages/sections/BudgetsPage'
import { CategoriesPage } from './pages/sections/CategoriesPage'
import { LedgersPage } from './pages/sections/LedgersPage'
import { OverviewPage } from './pages/sections/OverviewPage'
import { SettingsAiPage } from './pages/sections/SettingsAiPage'
import { SettingsDevicesPage } from './pages/sections/SettingsDevicesPage'
import { SettingsHealthPage } from './pages/sections/SettingsHealthPage'
import { SettingsProfilePage } from './pages/sections/SettingsProfilePage'
import { TagsPage } from './pages/sections/TagsPage'
import { clearCursor } from './state/sync-client'

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
        <Route path="overview" element={<OverviewPage />} />
        <Route path="transactions" element={<TransactionsPage />} />
        <Route path="ledgers" element={<LedgersPage />} />
        <Route path="budgets" element={<BudgetsPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="categories" element={<CategoriesPage />} />
        <Route path="tags" element={<TagsPage />} />
        <Route path="admin/users" element={<AdminUsersPage />} />
        <Route path="settings/profile" element={<SettingsProfilePage />} />
        <Route path="settings/appearance" element={<SettingsProfilePage />} />
        <Route path="settings/ai" element={<SettingsAiPage />} />
        <Route path="settings/health" element={<SettingsHealthPage />} />
        <Route path="settings/devices" element={<SettingsDevicesPage />} />
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
