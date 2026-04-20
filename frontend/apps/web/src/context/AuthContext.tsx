import { createContext, useContext, type ReactNode } from 'react'

import type { ProfileMe } from '@beecount/api-client'

/**
 * 全局 Auth 上下文 —— 把 AppPage 里原先从顶层往下 drill 的 token / profileMe /
 * isAdminUser / onLogout 塞到 Provider。
 *
 * 使用:
 *   const { token, profileMe, isAdmin, logout } = useAuth()
 *
 * 注意:这个 context 只负责读和注销,**不负责 login / refresh** —— 那两个
 * 流程在 AppShell 外壳的登录页面里走,成功后 AppShell 把 token/profileMe
 * 通过 props 注入到 AuthProvider。这样登录前后生命周期清楚,不混淆。
 */
export interface AuthContextValue {
  token: string
  profileMe: ProfileMe | null
  sessionUserId: string | null
  isAdmin: boolean
  /** 管理员探测是否已完成(探测期间 isAdmin 为 false,但不该用于权限判断)。 */
  isAdminResolved: boolean
  /** 重新拉取 profileMe。mutation 成功后调,把 server 权威数据刷回 context。 */
  refreshProfile: () => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

interface Props extends AuthContextValue {
  children: ReactNode
}

export function AuthProvider({ children, ...value }: Props) {
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

/** 便捷 hook:直接返回 token,给大多数 `authedGet(path, token)` 场景省一行解构。 */
export function useToken(): string {
  return useAuth().token
}
