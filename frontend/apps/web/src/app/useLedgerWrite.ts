import { useCallback } from 'react'

import { ApiError, fetchReadLedgerDetail } from '@beecount/api-client'

import { useAuth } from '../context/AuthContext'

/**
 * 共享的账本写入工具 hook —— 封装 base_change_id fetch + 409 WRITE_CONFLICT
 * 的乐观锁重试循环。供 accounts / categories / tags / transactions / budgets
 * 等需要写 snapshot 的 Page 复用。
 *
 * 设计:
 *   - 每次写入前 fetch 当前 source_change_id 作为 base,提交给 server 走 CAS。
 *   - 409 WRITE_CONFLICT ⇒ mobile 在我们之前推了变更,重新抓 base 重试(最多 4 次)。
 *   - 其它错误直接抛,调用方自己处理(通常是 localizeError → toast)。
 *
 * 为什么不缓存 baseChangeId 到 context:
 *   服务端 change_id 是单调递增 u64,WS 推送 / 另一个标签页的写入都会让它变。
 *   每次写前现抓一次比同步一份 stale state 简单,fetchReadLedgerDetail 轻量。
 */
export function useLedgerWrite() {
  const { token } = useAuth()

  const fetchBase = useCallback(
    async (ledgerId: string): Promise<number> => {
      const detail = await fetchReadLedgerDetail(token, ledgerId)
      return detail.source_change_id
    },
    [token]
  )

  const retryOnConflict = useCallback(
    async <T,>(
      ledgerId: string,
      submit: (baseChangeId: number) => Promise<T>
    ): Promise<T> => {
      const maxAttempts = 4
      let lastErr: unknown
      for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        const base = await fetchBase(ledgerId)
        try {
          return await submit(base)
        } catch (err) {
          if (!(err instanceof ApiError) || err.code !== 'WRITE_CONFLICT') throw err
          lastErr = err
          if (attempt < maxAttempts - 1) {
            await new Promise((r) => setTimeout(r, 50 + Math.random() * 100))
          }
        }
      }
      throw lastErr
    },
    [fetchBase]
  )

  /**
   * 识别 WRITE_CONFLICT 错误。返回 true 时,调用方应当把自己的数据重新拉一次
   * (server 状态已变,UI 里的旧数据不一致),并可向用户提示 "已刷新"。
   * 其它错误返回 false,让调用方走常规 error toast 路径。
   */
  const isWriteConflict = useCallback((err: unknown): boolean => {
    return err instanceof ApiError && err.code === 'WRITE_CONFLICT'
  }, [])

  return { retryOnConflict, isWriteConflict, fetchBase }
}
