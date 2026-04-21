import { createContext, useContext, useMemo, type ReactNode } from 'react'

import type { ReadLedger } from '@beecount/api-client'

/**
 * 全局账本上下文 —— ledgers 列表 + 当前选中账本 + 切换函数,所有 section
 * 都靠这个拿数据。避免从 AppShell/AppPage 往每个 section 都 drill 一遍
 * `ledgers` `activeLedgerId` `setActiveLedgerId`。
 *
 * 派生值 `currentLedger`(按 activeLedgerId 反查)也在 Provider 里算一次
 * 复用,避免各 section 重复写 `ledgers.find(l => l.ledger_id === activeLedgerId)`。
 *
 * `refreshLedgers` 是 AppShell 拉 ledgers 列表的权威入口 —— 任何 mutation
 * 成功后都应该调一下,让 UI 跟 server 状态对齐。
 */
export interface LedgersContextValue {
  ledgers: ReadLedger[]
  activeLedgerId: string | null
  currentLedger: ReadLedger | null
  /** 当前账本的币种,无账本时回落 CNY 方便 UI 直接渲染 */
  currency: string
  setActiveLedgerId: (id: string) => void
  /** 重新拉取 ledgers 列表。mutation 成功后调。 */
  refreshLedgers: () => Promise<void>
}

const LedgersContext = createContext<LedgersContextValue | null>(null)

interface Props {
  ledgers: ReadLedger[]
  activeLedgerId: string | null
  setActiveLedgerId: (id: string) => void
  refreshLedgers: () => Promise<void>
  children: ReactNode
}

export function LedgersProvider({
  ledgers,
  activeLedgerId,
  setActiveLedgerId,
  refreshLedgers,
  children,
}: Props) {
  const value = useMemo<LedgersContextValue>(() => {
    const currentLedger =
      ledgers.find((l) => l.ledger_id === activeLedgerId) || null
    return {
      ledgers,
      activeLedgerId,
      currentLedger,
      currency: currentLedger?.currency || 'CNY',
      setActiveLedgerId,
      refreshLedgers,
    }
  }, [ledgers, activeLedgerId, setActiveLedgerId, refreshLedgers])

  return (
    <LedgersContext.Provider value={value}>{children}</LedgersContext.Provider>
  )
}

export function useLedgers(): LedgersContextValue {
  const ctx = useContext(LedgersContext)
  if (!ctx) throw new Error('useLedgers must be used inside <LedgersProvider>')
  return ctx
}

/** 快捷 hook:直接拿当前账本币种,默认 CNY。 */
export function useCurrentCurrency(): string {
  return useLedgers().currency
}
