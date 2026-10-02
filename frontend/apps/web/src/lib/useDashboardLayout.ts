import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchDashboardLayout, putDashboardLayout } from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import { useAuth } from '../context/AuthContext'
import { useSyncRefresh } from '../context/SyncSocketContext'
import {
  buildPayload,
  defaultLayout,
  mergeLayout,
  moveCard,
  sameLayout,
  setCardVisible,
  showCardAtEnd,
  type CardDefaults,
  type LayoutCard,
} from './dashboardLayout'

/**
 * 首頁版面狀態 + 伺服器同步(`/profile/dashboard-layout`)。
 *
 * - 載入完成前用預設版面,不阻塞畫面(避免白屏/閃爍的取捨:載入後若不同才換)。
 * - 每次修改都樂觀更新並立刻 PUT;同時只有一個請求在飛,期間的新修改只保留最後
 *   一份,飛完再送。失敗 → toast + 回滾到最後一次伺服器確認的版面。
 * - 其它裝置改版面後,下次 sync 事件/視窗重新載入時會讀到(編輯中不覆蓋)。
 */
export function useDashboardLayout(defs: CardDefaults[], editing: boolean) {
  const { token } = useAuth()
  const t = useT()
  const toast = useToast()
  const [cards, setCards] = useState<LayoutCard[]>(() => defaultLayout(defs))
  const [loaded, setLoaded] = useState(false)
  const cardsRef = useRef(cards)
  cardsRef.current = cards
  const unknownRef = useRef<LayoutCard[]>([])
  const confirmedRef = useRef<LayoutCard[]>(cards)
  const inflightRef = useRef(false)
  const queuedRef = useRef<LayoutCard[] | null>(null)
  const editingRef = useRef(editing)
  editingRef.current = editing
  const defsRef = useRef(defs)
  defsRef.current = defs

  const load = useCallback(async () => {
    try {
      const res = await fetchDashboardLayout(token)
      if (editingRef.current || inflightRef.current) return
      const merged = mergeLayout(defsRef.current, res.layout?.cards)
      unknownRef.current = merged.unknown
      confirmedRef.current = merged.cards
      setCards((prev) => (sameLayout(prev, merged.cards) ? prev : merged.cards))
    } catch {
      // 讀失敗就維持預設/目前版面,不打擾使用者
    } finally {
      setLoaded(true)
    }
  }, [token])

  useEffect(() => {
    void load()
  }, [load])
  useSyncRefresh(() => {
    void load()
  })

  const persist = useCallback(
    async (next: LayoutCard[], reset = false) => {
      if (inflightRef.current) {
        queuedRef.current = next
        return
      }
      inflightRef.current = true
      try {
        const payload = reset
          ? { version: 1, cards: [] }
          : buildPayload({ cards: next, unknown: unknownRef.current })
        await putDashboardLayout(token, payload)
        confirmedRef.current = next
        if (reset) unknownRef.current = []
      } catch {
        queuedRef.current = null
        cardsRef.current = confirmedRef.current
        setCards(confirmedRef.current)
        toast.error(t('home.layout.saveFailed'), t('notice.error'))
      } finally {
        inflightRef.current = false
        const queued = queuedRef.current
        if (queued) {
          queuedRef.current = null
          void persist(queued)
        }
      }
    },
    [token, toast, t],
  )

  const apply = useCallback(
    (fn: (prev: LayoutCard[]) => LayoutCard[]) => {
      // 不能把 persist 放在 setState updater 裡(StrictMode 會呼叫兩次)。
      const prev = cardsRef.current
      const next = fn(prev)
      if (next === prev) return
      cardsRef.current = next
      setCards(next)
      void persist(next)
    },
    [persist],
  )

  const move = useCallback((from: string, to: string) => apply((p) => moveCard(p, from, to)), [apply])
  const hide = useCallback((id: string) => apply((p) => setCardVisible(p, id, false)), [apply])
  const show = useCallback((id: string) => apply((p) => showCardAtEnd(p, id)), [apply])
  const reset = useCallback(() => {
    const base = defaultLayout(defsRef.current)
    cardsRef.current = base
    setCards(base)
    void persist(base, true)
  }, [persist])

  return useMemo(
    () => ({ cards, loaded, move, hide, show, reset }),
    [cards, loaded, move, hide, show, reset],
  )
}
