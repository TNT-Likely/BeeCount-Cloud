import { type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

interface RequireAuthProps {
  isAuthed: boolean
  children: ReactNode
}

/**
 * 未登录的 /app/* 深链会 replace 到 /login。登录成功后 LoginPage 侧用
 * `navigate('/app/overview', { replace: true })` 跳回。
 */
export function RequireAuth({ isAuthed, children }: RequireAuthProps) {
  const location = useLocation()
  if (!isAuthed) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <>{children}</>
}
