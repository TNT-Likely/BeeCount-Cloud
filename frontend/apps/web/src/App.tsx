import { useEffect, useState } from 'react'

import { API_BASE } from '@beecount/api-client'

import { AppPage } from './pages/AppPage'
import { LoginPage } from './pages/LoginPage'
import { usePathRouter } from './state/usePathRouter'

const LEGACY_TOKEN_KEY = 'beecount.token'
const TOKEN_KEY = `beecount.token.${API_BASE}`

export function App() {
  const { route, navigate } = usePathRouter()
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
    if (!token && route.kind !== 'login') {
      navigate({ kind: 'login' }, { replace: true })
      return
    }
    if (token && route.kind === 'login') {
      navigate({ kind: 'app', ledgerId: '', section: 'transactions' }, { replace: true })
    }
  }, [route.kind, token, navigate])

  if (!token) {
    return (
      <LoginPage
        onLoggedIn={(nextToken) => {
          setToken(nextToken)
          navigate({ kind: 'app', ledgerId: '', section: 'transactions' }, { replace: true })
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
        setToken('')
        navigate({ kind: 'login' }, { replace: true })
      }}
    />
  )
}
