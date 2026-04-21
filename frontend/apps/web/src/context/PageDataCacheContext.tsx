import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react'

/**
 * 进程内的 Page 级数据缓存 —— 消除切 section 时的空态闪烁。
 *
 * 问题:
 *   切路由会 unmount 旧 Page、mount 新 Page。新 Page 的 useState 初值是空
 *   数组/空对象 → 首帧渲染空态 → useEffect fire → fetch 发出去 → 数据回来
 *   再重渲染填上。视觉上就是"空一下再闪出来"。
 *
 * 方案:
 *   AppShell 层挂一个 Map<key, value>。每个 Page 把主要数据 state 从 useState
 *   改成 usePageCache(key, initial) —— 首次从 cache 读,没有才回落到 initial。
 *   每次 setter 调用都把新值同步写回 cache。于是下次 Page 重新 mount 时,
 *   立刻显示上一次的数据(0 延迟),useEffect 后台 refetch 再悄悄替换。
 *
 * 不做的事:
 *   - TTL / 过期:数据新鲜度由各 Page 的 mount-refetch + useSyncRefresh 保证
 *   - 跨 tab 共享:Map 只在当前 tab 的这个 Provider 实例里有效
 *   - 持久化:刷新浏览器丢;这是 *session 内* 切 section 的优化,不是跨会话 hydration
 *
 * 登出 / 切用户:Provider 会跟 AppShell 一起 unmount + re-mount(因为 token
 * 变了 → App 重新渲染),cache 自然清空,避免 User A 的数据被 User B 看到。
 */
type CacheMap = Map<string, unknown>

const PageDataCacheContext = createContext<CacheMap | null>(null)

export function PageDataCacheProvider({ children }: { children: ReactNode }) {
  const cache = useMemo<CacheMap>(() => new Map(), [])
  return <PageDataCacheContext.Provider value={cache}>{children}</PageDataCacheContext.Provider>
}

/**
 * 跟 useState 同签名,但 key 相同时会复用 cache 里的上次值作为初值。
 *
 * ```tsx
 * const [accounts, setAccounts] = usePageCache<Account[]>('accounts:rows', [])
 * ```
 *
 * 关键点:
 *   - 只在 mount 的首次 render 读 cache(`useState` 初始化 lazy 函数),之后
 *     一切 setState 都走本地 React state,额外写 cache 一次 —— 避免每次 render
 *     都跟 cache 做比对的开销
 *   - key 应该带上决定数据分桶的维度(activeLedgerId / userId / filter 等),
 *     不同桶不该混用。例如 budgets 应当用 `budgets:${activeLedgerId}:rows`
 */
export function usePageCache<T>(
  key: string,
  initial: T | (() => T)
): [T, Dispatch<SetStateAction<T>>] {
  const cache = useContext(PageDataCacheContext)
  const [state, setState] = useState<T>(() => {
    if (cache && cache.has(key)) {
      return cache.get(key) as T
    }
    return typeof initial === 'function' ? (initial as () => T)() : initial
  })

  const set: Dispatch<SetStateAction<T>> = useCallback(
    (next) => {
      setState((prev) => {
        const value =
          typeof next === 'function' ? (next as (p: T) => T)(prev) : next
        if (cache) cache.set(key, value)
        return value
      })
    },
    [cache, key]
  )

  return [state, set]
}
