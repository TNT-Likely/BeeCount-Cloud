import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import { fetchLockSettings, type LockSettings } from '@beecount/api-client'

import { useAuth } from './AuthContext'

/**
 * 用户级锁账状态(0067)。
 *
 * - 后端是强制防线:projection 层对锁定期交易的增删改与账户初始金额变更
 *   直接返回 423。这里的 context 只负责 UI 层提示与按钮禁用,避免用户
 *   点了才报错。
 * - isTxLocked(iso) 判断某笔交易是否落在锁定期(含当天)。
 * - isAccountFrozen 恒等于 isLocked:账户是 user-global,锁定后初始金额冻结。
 */
export interface LockContextValue {
  isLocked: boolean
  /** 销账日期(本地日 YYYY-MM-DD),仅用于展示。 */
  lockDay: string | null
  /** 交易是否在锁定期内。 */
  isTxLocked: (happenedAt: string | null | undefined) => boolean
  /** 账户初始金额是否冻结。 */
  isAccountFrozen: boolean
  refresh: () => Promise<void>
}

const LockContext = createContext<LockContextValue | null>(null)

const EMPTY: LockSettings = { is_locked: false, lock_date: null }

export function LockProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const [settings, setSettings] = useState<LockSettings>(EMPTY)

  const refresh = useCallback(async () => {
    if (!token) return
    try {
      setSettings(await fetchLockSettings(token))
    } catch {
      // 锁配置读取失败不应阻塞页面:后端仍会在写入时强制拦截。
      setSettings(EMPTY)
    }
  }, [token])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo<LockContextValue>(() => {
    const cutoff = settings.lock_date ? new Date(settings.lock_date).getTime() : null
    const isTxLocked = (happenedAt: string | null | undefined) => {
      if (!settings.is_locked || cutoff === null || !happenedAt) return false
      const ts = new Date(happenedAt).getTime()
      return Number.isFinite(ts) && ts <= cutoff
    }
    return {
      isLocked: settings.is_locked,
      lockDay: settings.lock_date ? settings.lock_date.slice(0, 10) : null,
      isTxLocked,
      isAccountFrozen: settings.is_locked,
      refresh,
    }
  }, [settings, refresh])

  return <LockContext.Provider value={value}>{children}</LockContext.Provider>
}

export function useLock(): LockContextValue {
  const ctx = useContext(LockContext)
  if (!ctx) throw new Error('useLock must be used inside <LockProvider>')
  return ctx
}

/**
 * 把本地日历日(YYYY-MM-DD)转成当天 23:59:59 的 UTC ISO 字符串。
 * 后端以 UTC 存储并按 `happened_at <= lock_date` 判断,所以截止时刻必须
 * 是用户本地当天的最后一秒,否则当天晚些时候的交易会被漏锁。
 */
export function lockDayToIso(day: string): string {
  const date = new Date(`${day}T23:59:59`)
  return date.toISOString()
}
