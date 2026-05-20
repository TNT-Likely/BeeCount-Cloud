import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import {
  fetchSharedResources,
  type SharedAccountItem,
  type SharedCategoryItem,
  type SharedResourcesBundle,
  type SharedTagItem,
} from '@beecount/api-client'

import { useAttachmentCache } from './AttachmentCacheContext'
import { useAuth } from './AuthContext'
import { useSyncEvent } from './SyncSocketContext'

/**
 * 共享账本 Owner user-global 资源快照独立 state(对齐 mobile
 * SharedLedger{Categories,Accounts,Tags} 镜像表):
 *
 * - **不污染**用户自己的 user-global state(categories/tags/accounts)
 * - **lazy** 加载:进入共享账本才拉一次 `/ledgers/{id}/shared-resources`
 * - **per-ledger 缓存**:Map<ledgerExternalId, Bundle>
 * - **失效**:
 *   - WS `shared_resource_change` 事件 → `invalidate(ledgerId)`
 *   - 用户切回自己账本 → state 保留(不强制清,下次切回来仍可用)
 *   - logout → 整个 provider unmount,state 自然清掉
 *
 * 用法:
 *   const { resources, isLoading } = useSharedLedgerResources(ledgerId)
 *   if (resources) {
 *     // 共享账本 picker / tile / icon lookup 走这套
 *     const owner = resources.owner_user_id
 *     const cats: SharedCategoryItem[] = resources.categories
 *   }
 */
export interface SharedLedgerResourcesContextValue {
  /** lazy 加载并返回某 ledger 的 shared bundle。返回 null = 还没拉到 */
  getBundle: (ledgerId: string) => SharedResourcesBundle | null
  /** 是否正在拉。组件可用来 show loading skeleton */
  isLoading: (ledgerId: string) => boolean
  /** 强制重新拉一次(WS shared_resource_change 触发) */
  invalidate: (ledgerId: string) => void
  /** 主动预拉(可选,通常 useSharedLedgerResources hook 会自动触发) */
  ensureLoaded: (ledgerId: string) => Promise<void>
}

const SharedLedgerResourcesContext =
  createContext<SharedLedgerResourcesContextValue | null>(null)

interface Props {
  children: ReactNode
}

export function SharedLedgerResourcesProvider({ children }: Props) {
  const { token } = useAuth()
  const { ensureLoadedMany } = useAttachmentCache()
  const [bundles, setBundles] = useState<Map<string, SharedResourcesBundle>>(
    () => new Map(),
  )
  const [loading, setLoading] = useState<Set<string>>(() => new Set())
  // inflight 去重:同一 ledger 并发 ensureLoaded 共享一个 Promise
  const inflight = useRef<Map<string, Promise<void>>>(new Map())

  const ensureLoaded = useCallback(
    async (ledgerId: string): Promise<void> => {
      if (!token || !ledgerId) return
      if (bundles.has(ledgerId)) return
      const pending = inflight.current.get(ledgerId)
      if (pending) return pending
      const p = (async () => {
        setLoading((s) => {
          const next = new Set(s)
          next.add(ledgerId)
          return next
        })
        try {
          const bundle = await fetchSharedResources(token, ledgerId)
          setBundles((m) => {
            const next = new Map(m)
            next.set(ledgerId, bundle)
            return next
          })
          // §7 共享账本:bundle 里 Owner 自定义分类图标的 icon_cloud_file_id
          // 必须预热到 AttachmentCache,否则 CategoryIcon 拿不到 blob URL,
          // tx 列表/picker 只显示空图标。这里 fire-and-forget,失败不阻塞。
          const fileIds = bundle.categories
            .map((c) => (c.icon_cloud_file_id || '').trim())
            .filter((v) => v.length > 0)
          if (fileIds.length > 0) ensureLoadedMany(fileIds)
        } catch (err) {
          // 拉失败不阻塞 UI — picker / tile 会 fallback 到 self 资源(显示
          // 空或不显示 icon),后续 WS / 切换重试时再补。日志由调用方处理。
          // eslint-disable-next-line no-console
          console.warn('fetchSharedResources failed', ledgerId, err)
        } finally {
          setLoading((s) => {
            const next = new Set(s)
            next.delete(ledgerId)
            return next
          })
          inflight.current.delete(ledgerId)
        }
      })()
      inflight.current.set(ledgerId, p)
      return p
    },
    [token, bundles, ensureLoadedMany],
  )

  const invalidate = useCallback((ledgerId: string) => {
    setBundles((m) => {
      const next = new Map(m)
      next.delete(ledgerId)
      return next
    })
    // 不主动 ensureLoaded — 等下次 useSharedLedgerResources 触发或显式调
  }, [])

  const getBundle = useCallback(
    (ledgerId: string): SharedResourcesBundle | null => {
      return bundles.get(ledgerId) || null
    },
    [bundles],
  )

  const isLoading = useCallback(
    (ledgerId: string): boolean => loading.has(ledgerId),
    [loading],
  )

  // §7 共享账本 WS 同步:
  // - shared_resource_change(Owner 改 category/account/tag)→ invalidate
  //   该 ledger,下次 useSharedLedgerResources 会 re-fetch
  // - member_change(被踢 / 加入 / 角色变)→ invalidate,因为成员变化可
  //   能伴随权限变化(被踢后查不到了)。前端 useSharedLedgerResources
  //   触发 re-fetch 时 server 会返 403,我们 console.warn 不阻塞;UI 层
  //   还会有 ledger 列表 refresh 处理 ledger 的去留。
  useSyncEvent('shared_resource_change', (payload) => {
    const ledgerId = (payload as { ledgerId?: string }).ledgerId
    if (typeof ledgerId === 'string' && ledgerId) {
      // 直接调 invalidate(setState) — 不能用 invalidate ref 因为 Provider
      // body 内的 invalidate 已经是 stable callback。
      setBundles((m) => {
        if (!m.has(ledgerId)) return m
        const next = new Map(m)
        next.delete(ledgerId)
        return next
      })
    }
  })
  useSyncEvent('member_change', (payload) => {
    const ledgerId = (payload as { ledgerId?: string }).ledgerId
    if (typeof ledgerId === 'string' && ledgerId) {
      setBundles((m) => {
        if (!m.has(ledgerId)) return m
        const next = new Map(m)
        next.delete(ledgerId)
        return next
      })
    }
  })

  const value = useMemo<SharedLedgerResourcesContextValue>(
    () => ({ getBundle, isLoading, invalidate, ensureLoaded }),
    [getBundle, isLoading, invalidate, ensureLoaded],
  )

  return (
    <SharedLedgerResourcesContext.Provider value={value}>
      {children}
    </SharedLedgerResourcesContext.Provider>
  )
}

/**
 * Hook 形式:传入 ledgerId,自动 ensureLoaded + 返回当前状态。
 *
 * - ledgerId 为 null/empty → 不做任何操作,返回 null bundle
 * - 调用方应在共享账本(currentLedger.is_shared)场景使用;非共享账本走
 *   自己 user-global 数据。
 *
 * 返回:
 *   - bundle: 拉到的 Owner 资源;还没拉 / 拉失败 = null
 *   - isLoading: 是否在拉
 *   - reload: 主动重新拉
 */
export function useSharedLedgerResources(ledgerId: string | null): {
  bundle: SharedResourcesBundle | null
  isLoading: boolean
  reload: () => void
} {
  const ctx = useContext(SharedLedgerResourcesContext)
  if (!ctx) {
    throw new Error(
      'useSharedLedgerResources must be used within SharedLedgerResourcesProvider',
    )
  }
  const bundle = ledgerId ? ctx.getBundle(ledgerId) : null
  const isLoading = ledgerId ? ctx.isLoading(ledgerId) : false
  // 触发 lazy fetch:bundle 为 null 时(初次进 / WS invalidate 后)走 effect
  // 触发 ensureLoaded — provider 内部已经按 inflight Promise + bundles.has
  // 双重去重,这里不需要再加 ref 守卫(老实现的 triggeredRef 漏 reset 导致
  // WS 失效后永远不重 fetch)。
  useEffect(() => {
    if (!ledgerId) return
    if (bundle) return
    void ctx.ensureLoaded(ledgerId)
  }, [ledgerId, bundle, ctx])
  const reload = useCallback(() => {
    if (!ledgerId) return
    ctx.invalidate(ledgerId)
    void ctx.ensureLoaded(ledgerId)
  }, [ctx, ledgerId])
  return { bundle, isLoading, reload }
}

/**
 * 不传 ledgerId 的 raw context — 给 SyncSocketContext 等需要 invalidate 但
 * 不订阅具体 ledger 的场景。
 */
export function useSharedLedgerResourcesContext(): SharedLedgerResourcesContextValue {
  const ctx = useContext(SharedLedgerResourcesContext)
  if (!ctx) {
    throw new Error(
      'useSharedLedgerResourcesContext must be used within SharedLedgerResourcesProvider',
    )
  }
  return ctx
}

// 类型 re-export 给 consumer 用
export type {
  SharedAccountItem,
  SharedCategoryItem,
  SharedResourcesBundle,
  SharedTagItem,
}
