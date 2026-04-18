import { useEffect, useState } from 'react'

import { API_BASE, clearStoredSession, configureHttp, getStoredUserId, refreshAuth } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { AppPage } from './pages/AppPage'
import { LoginPage } from './pages/LoginPage'
import { clearCursor } from './state/sync-client'
import { usePathRouter } from './state/usePathRouter'

const LEGACY_TOKEN_KEY = 'beecount.token'
const TOKEN_KEY = `beecount.token.${API_BASE}`

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
        if (prev) clearCursor(prev)
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

  return (
    <AppPage
      token={token}
      route={route}
      onNavigate={navigate}
      onLogout={() => {
        const prev = getStoredUserId()
        if (prev) clearCursor(prev)
        clearStoredSession()
        setToken('')
        navigate({ kind: 'login' }, { replace: true })
      }}
    />
  )
}
