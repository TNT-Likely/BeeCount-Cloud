import { useCallback, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom'

import { API_BASE, clearStoredSession, configureHttp, getStoredUserId, refreshAuth } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { RequireAuth, useLegacyRoute } from './app/router'
import { AppPage } from './pages/AppPage'
import { LoginPage } from './pages/LoginPage'
import { jwtUserId } from './state/jwt'
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

  // 显式枚举所有 section 路由 —— 阶段 3 Step 1。每条路由目前都走
  // LegacyAppPage(内部仍靠 useLegacyRoute 桥反解析 URL → AppRoute),
  // 后续 Step 2 每次把一条路由的 element 换成真正独立的 <XxxPage />。
  const protectedElement = (
    <RequireAuth isAuthed={!!token}>
      <LegacyAppPage token={token} onLogout={handleLogout} />
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
      <Route path="/app" element={<Navigate to="/app/overview" replace />} />
      <Route path="/app/overview" element={protectedElement} />
      <Route path="/app/transactions" element={protectedElement} />
      <Route path="/app/ledgers" element={protectedElement} />
      <Route path="/app/budgets" element={protectedElement} />
      <Route path="/app/accounts" element={protectedElement} />
      <Route path="/app/categories" element={protectedElement} />
      <Route path="/app/tags" element={protectedElement} />
      <Route path="/app/admin/users" element={protectedElement} />
      <Route path="/app/settings/profile" element={protectedElement} />
      <Route path="/app/settings/appearance" element={protectedElement} />
      <Route path="/app/settings/ai" element={protectedElement} />
      <Route path="/app/settings/health" element={protectedElement} />
      <Route path="/app/settings/devices" element={protectedElement} />
      {/* legacy 深链:/app/:ledgerId/... 由 useLegacyRoute 里的 parseRoute 兜底 */}
      <Route path="/app/*" element={protectedElement} />
      <Route path="/" element={<Navigate to={token ? '/app/overview' : '/login'} replace />} />
      <Route path="*" element={<Navigate to={token ? '/app/overview' : '/login'} replace />} />
    </Routes>
  )
}

/**
 * 过渡壳:react-router 的 `/app/*` 通配路由挂这里,内部通过 useLegacyRoute
 * 把 URL 反解析成老的 AppRoute 对象注入 AppPage,保持 AppPage 内部代码零改动。
 *
 * key={userId}:切换用户时 React 会 unmount 旧 AppPage + 全新 mount,
 * 彻底清掉所有 useState(ledgers/accounts/categories/tags/...) 和 useEffect 闭包,
 * 避免 User A 的数据泄漏到 User B 的 session。
 */
function LegacyAppPage({ token, onLogout }: { token: string; onLogout: () => void }) {
  const { route, navigate } = useLegacyRoute()
  if (route.kind !== 'app') {
    return null
  }
  return (
    <AppPage
      key={jwtUserId(token) || 'anon'}
      token={token}
      route={route}
      onNavigate={navigate}
      onLogout={onLogout}
    />
  )
}
