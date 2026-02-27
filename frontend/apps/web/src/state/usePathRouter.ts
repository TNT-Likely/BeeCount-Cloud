import { useCallback, useEffect, useState } from 'react'

import { type AppRoute, parseRoute, routePath } from './router'

export function usePathRouter() {
  const [route, setRoute] = useState<AppRoute>(() => parseRoute(window.location.pathname))

  useEffect(() => {
    const onPopState = () => setRoute(parseRoute(window.location.pathname))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    const canonical = routePath(route)
    if (window.location.pathname !== canonical) {
      window.history.replaceState(null, '', canonical)
    }
  }, [route])

  const navigate = useCallback((next: AppRoute, options?: { replace?: boolean }) => {
    const nextPath = routePath(next)
    const method = options?.replace ? 'replaceState' : 'pushState'
    window.history[method](null, '', nextPath)
    setRoute(next)
  }, [])

  return {
    route,
    navigate
  }
}
