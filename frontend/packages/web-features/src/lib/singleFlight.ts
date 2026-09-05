import { useCallback, useRef, useState } from 'react'

/** 单飞守卫:执行期间的再次调用被丢弃(返回 undefined),结束后自动释放。 */
export type SingleFlightGuard = <R>(fn: () => Promise<R> | R) => Promise<R | undefined>

/**
 * 创建一个单飞守卫,用于提交按钮防连点(#450:建交易请求未返回前
 * 再次点击会重复创建)。inFlight 标志在调用的同步段就置位,同一事件
 * 循环内的重入也能挡住;onInFlightChange 在真正开始/结束执行时收到
 * true/false,可用来驱动按钮的 disabled 态,被挡掉的调用不触发通知。
 */
export function createSingleFlight(
  onInFlightChange?: (inFlight: boolean) => void,
): SingleFlightGuard {
  let inFlight = false
  return async <R,>(fn: () => Promise<R> | R): Promise<R | undefined> => {
    if (inFlight) return undefined
    inFlight = true
    onInFlightChange?.(true)
    try {
      return await fn()
    } finally {
      inFlight = false
      onInFlightChange?.(false)
    }
  }
}

/**
 * createSingleFlight 的 React 绑定:guard 跨 render 稳定,saving 给
 * 按钮做 disabled/loading。用法:
 *
 *   const { saving, guard } = useSingleFlight()
 *   <Button disabled={saving} onClick={() => guard(onSave)} />
 */
export function useSingleFlight(): { saving: boolean; guard: SingleFlightGuard } {
  const [saving, setSaving] = useState(false)
  const guardRef = useRef<SingleFlightGuard | null>(null)
  if (!guardRef.current) {
    guardRef.current = createSingleFlight(setSaving)
  }
  const guard = useCallback<SingleFlightGuard>((fn) => guardRef.current!(fn), [])
  return { saving, guard }
}
