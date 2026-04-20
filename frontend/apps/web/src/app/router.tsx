import { useCallback, type ReactNode } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { parseRoute, routePath, type AppRoute } from '../state/router'

/**
 * react-router ↔ legacy AppRoute 适配桥。
 *
 * 过渡期(阶段 3 Step 1 ~ Step 4)期间,AppPage 内部仍然基于 `route: AppRoute` /
 * `onNavigate(next: AppRoute)` 对象工作,不直接依赖 react-router。这里把
 * react-router 的 `useLocation()` 反解析成 AppRoute,`useNavigate()` 适配成
 * 接受 AppRoute 的 `navigate()`,让 AppPage 可以零改动跑在新 router 上。
 *
 * 阶段 3 逐个 section 拆出独立 Page 后,各 Page 自己用 `useNavigate()` /
 * `<Link>`,这个 hook 就只给 AppPage 的遗留代码用,直到 AppPage 瘦下来。
 */
export function useLegacyRoute(): {
  route: AppRoute
  navigate: (next: AppRoute, options?: { replace?: boolean }) => void
} {
  const location = useLocation()
  const rrNavigate = useNavigate()
  const route = parseRoute(location.pathname)
  const navigate = useCallback(
    (next: AppRoute, options?: { replace?: boolean }) => {
      rrNavigate(routePath(next), { replace: options?.replace })
    },
    [rrNavigate]
  )
  return { route, navigate }
}

interface RequireAuthProps {
  isAuthed: boolean
  children: ReactNode
}

/**
 * 未登录的 /app/* 深链会 replace 到 /login,避免 AppPage 在没 token 的场景下挂载。
 * 登录成功后 LoginPage 侧用 `navigate('/app/overview', { replace: true })` 跳回。
 */
export function RequireAuth({ isAuthed, children }: RequireAuthProps) {
  const location = useLocation()
  if (!isAuthed) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <>{children}</>
}

/** 顶层 /app 节点的占位 —— 未来会替换成 AppShell(Provider 栈 + AppLayout + Outlet)。 */
export function AppOutlet() {
  return <Outlet />
}
