import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from 'react'

import { getStoredDeviceId, getStoredUserId } from '@beecount/api-client'

import { useSyncSocket } from '../hooks/useSyncSocket'
import { drainPull, startPoller, type SyncChangeEnvelope } from '../state/sync-client'
import { useAuth } from './AuthContext'

/**
 * WS 推送 / polling fallback 推上来的事件。两种形态:
 *   - WS profile_change / sync_change / backup_restore 等来自 server 的广播
 *   - 本地 drain/poller 产生的 sync_change_batch(从 /sync/pull 拉回来的 changes)
 *
 * 各 Page 通过 `useSyncEvent(kind, handler)` 订阅自己关心的事件,handler 只在
 * 匹配的事件到来时触发。handler 内部 re-render、re-fetch 或静默处理都可以。
 */
export type SyncEventKind =
  | 'profile_change'
  | 'sync_change'
  | 'backup_restore'
  /** 本地 poller 拉到一批 change_envelope;payload.changes 是 envelope 数组 */
  | 'sync_change_batch'
  /** 订阅"任何 server 事件",原始 payload 透传 */
  | 'any'

export interface SyncEventBase {
  type?: string
  [key: string]: unknown
}

export interface SyncChangeBatchPayload {
  type: 'sync_change_batch'
  changes: SyncChangeEnvelope[]
}

export type SyncEventPayload = SyncEventBase | SyncChangeBatchPayload

type Handler = (payload: SyncEventPayload) => void

interface Subscriber {
  kind: SyncEventKind
  handler: Handler
}

interface SyncSocketContextValue {
  /** 内部:向所有订阅者广播一条事件。只给 Provider 自己用,不对外暴露。 */
  _emit: (payload: SyncEventPayload) => void
  /** 内部:注册/注销订阅。 */
  _subscribe: (sub: Subscriber) => () => void
}

const SyncSocketContext = createContext<SyncSocketContextValue | null>(null)

function wsUrl(token: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.port === '5173' ? `${window.location.hostname}:8080` : window.location.host
  return `${protocol}://${host}/ws?token=${encodeURIComponent(token)}`
}

/**
 * 全局同步管道 Provider —— 挂在 AppShell 里,登录会话期间常驻。
 *
 * 做三件事:
 *   1. 打开一个 WebSocket(useSyncSocket 负责重连 / 心跳)
 *   2. 启动 polling fallback(startPoller 每 30s 拉一次 /sync/pull)
 *   3. 重连 / 可见时补拉一次 drainPull,把 offline 期间漏的 change 吃进来
 *
 * 所有收到的事件都通过 subscriber 列表广播给各 Page 的 useSyncEvent(...)。
 * Page 按需订阅 'sync_change' + 'backup_restore'(自己 refresh)或 'profile_change'
 * (AppShell 自己订了,各 Page 无需关心 profile 事件)。
 */
export function SyncSocketProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const subscribersRef = useRef<Set<Subscriber>>(new Set())

  const syncUserIdRef = useRef<string>('')
  if (!syncUserIdRef.current) {
    syncUserIdRef.current = getStoredUserId() || ''
  }
  const syncDeviceId = useMemo(() => getStoredDeviceId(), [token])

  const emit = useCallback((payload: SyncEventPayload) => {
    // 遍历快照,避免 handler 内部 unsubscribe 时 mutate 列表。
    const snapshot = Array.from(subscribersRef.current)
    for (const sub of snapshot) {
      const typeMatches =
        sub.kind === 'any' ||
        (payload as SyncEventBase).type === sub.kind
      if (typeMatches) {
        try {
          sub.handler(payload)
        } catch (err) {
          // 单个 handler 抛错不影响其它订阅者。
          // eslint-disable-next-line no-console
          console.error('[SyncSocket] handler threw', err)
        }
      }
    }
  }, [])

  const subscribe = useCallback((sub: Subscriber) => {
    subscribersRef.current.add(sub)
    return () => {
      subscribersRef.current.delete(sub)
    }
  }, [])

  const wsBuildUrl = useCallback((tok: string) => wsUrl(tok), [])

  useSyncSocket({
    token,
    buildUrl: wsBuildUrl,
    onEvent: (payload: unknown) => {
      if (!payload || typeof payload !== 'object') return
      emit(payload as SyncEventPayload)
    },
    onOpen: () => {
      // 重连补拉:拉 since cursor 以来的所有 change,合并成一条 batch 广播。
      const userId = syncUserIdRef.current
      if (!token || !userId) return
      void drainPull(token, userId, syncDeviceId).then((res) => {
        if (res.changes.length > 0) {
          emit({ type: 'sync_change_batch', changes: res.changes })
        }
      })
    },
  })

  // Polling fallback:WS 通道健康时 since-filter 让它基本 no-op,断网 / 代理
  // 杀连接场景下兜底。收到 changes 时一样广播 sync_change_batch。
  useEffect(() => {
    if (!token) return
    const userId = syncUserIdRef.current
    if (!userId) return
    const poller = startPoller({
      token,
      userId,
      deviceId: syncDeviceId,
      onChanges: (changes) => {
        if (changes.length > 0) {
          emit({ type: 'sync_change_batch', changes })
        }
      },
    })
    return () => {
      poller.stop()
    }
  }, [token, syncDeviceId, emit])

  const value = useMemo<SyncSocketContextValue>(
    () => ({ _emit: emit, _subscribe: subscribe }),
    [emit, subscribe]
  )

  return <SyncSocketContext.Provider value={value}>{children}</SyncSocketContext.Provider>
}

/**
 * 订阅某一类 sync 事件。handler 用 ref 持有,deps 只看 `kind` —— 这样各 Page
 * 组件传 inline handler 也不会每次 render 都重订阅。
 *
 * 典型用法:
 * ```tsx
 * useSyncEvent('sync_change', () => { void refresh() })
 * useSyncEvent('backup_restore', () => { void refresh() })
 * useSyncEvent('sync_change_batch', (p) => {
 *   // p.changes 是 SyncChangeEnvelope[],需要可按 entity_type 精细过滤
 * })
 * ```
 */
export function useSyncEvent(kind: SyncEventKind, handler: Handler): void {
  const ctx = useContext(SyncSocketContext)
  if (!ctx) throw new Error('useSyncEvent must be used inside <SyncSocketProvider>')
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  useEffect(() => {
    const stable: Handler = (payload) => handlerRef.current(payload)
    return ctx._subscribe({ kind, handler: stable })
  }, [ctx, kind])
}

/**
 * 便捷 hook:订阅常规"数据类"事件(sync_change + backup_restore + 本地
 * drain/poller 拉到的 batch),触发页面 refresh。大多数 Page 直接用这个,
 * 不用挨个订三次。
 */
export function useSyncRefresh(handler: () => void): void {
  const handlerRef = useRef(handler)
  handlerRef.current = handler
  const wrapped = useCallback(() => handlerRef.current(), [])
  useSyncEvent('sync_change', wrapped)
  useSyncEvent('backup_restore', wrapped)
  useSyncEvent('sync_change_batch', wrapped)
}
