import { useEffect, useState } from 'react'

import { API_BASE, clearStoredSession, configureHttp, getStoredUserId, refreshAuth } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { AppPage } from './pages/AppPage'
import { LoginPage } from './pages/LoginPage'
import { jwtUserId } from './state/jwt'
import { clearCursor } from './state/sync-client'
import { usePathRouter } from './state/usePathRouter'

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
    // txFilter 的 key 是 `beecount:web:txFilter:v1:<uid>:<ledgerFilter>`,
    // ledgerFilter 不固定,遍历清理所有符合前缀的 key。
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
  const { route, navigate } = usePathRouter()

  useEffect(() => {
    document.title = t('shell.docTitle')
  }, [t])
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

  useEffect(() => {
    configureHttp({
      refreshToken: async () => {
        const fresh = await refreshAuth()
        setToken(fresh)
        return fresh
      },
      onLogout: () => {
        const prev = getStoredUserId()
        if (prev) {
          clearCursor(prev)
          clearUserScopedStorage(prev)
        }
        clearStoredSession()
        setToken('')
        navigate({ kind: 'login' }, { replace: true })
      }
    })
    return () => {
      configureHttp({ refreshToken: null, onLogout: null })
    }
  }, [navigate])

  useEffect(() => {
    if (!token && route.kind !== 'login') {
      navigate({ kind: 'login' }, { replace: true })
      return
    }
    if (token && route.kind === 'login') {
      navigate({ kind: 'app', ledgerId: '', section: 'overview' }, { replace: true })
    }
  }, [route.kind, token, navigate])

  if (!token) {
    return (
      <LoginPage
        onLoggedIn={(nextToken) => {
          setToken(nextToken)
          navigate({ kind: 'app', ledgerId: '', section: 'overview' }, { replace: true })
        }}
      />
    )
  }

  if (route.kind !== 'app') {
    return null
  }

  // key 绑定到 userId:切换用户时 React 会 unmount 旧 AppPage + 全新 mount,
  // 彻底清掉所有 useState(ledgers/accounts/categories/tags/...) 和 useEffect 闭包,
  // 避免 User A 的数据泄漏到 User B 的 session。
  return (
    <AppPage
      key={jwtUserId(token) || 'anon'}
      token={token}
      route={route}
      onNavigate={navigate}
      onLogout={() => {
        const prev = getStoredUserId()
        if (prev) {
          clearCursor(prev)
          clearUserScopedStorage(prev)
        }
        clearStoredSession()
        setToken('')
        navigate({ kind: 'login' }, { replace: true })
      }}
    />
  )
}
