/**
 * 首頁卡片版面(2026-10-03,docs/STOCK_HOLDINGS_SD.md §12)的純函式邏輯:
 * 版面合併 / 容錯 / 移動 / 顯示切換 / 序列化。不依賴 React,方便單元測試。
 *
 * server 只存「有序的 {id, visible} 清單」(`GET/PUT /profile/dashboard-layout`),
 * 不認識卡片註冊表。所以:
 *  - 清單裡 client 不認識的 id(新版 client 新增的卡片)→ 合併時不顯示,但存檔時
 *    原樣保留在尾端(`unknown`),避免舊版 client 存檔時把新卡片的設定抹掉。
 *  - 註冊表裡有、清單裡沒有的 id(這個 client 新增的卡片)→ 依預設位置插入
 *    (接在預設順序上前一張已存在的卡片後面),可見性取預設值。
 */

export const DASHBOARD_LAYOUT_VERSION = 1

export interface LayoutCard {
  id: string
  visible: boolean
}

export interface LayoutPayload {
  version: number
  cards: LayoutCard[]
}

export interface CardDefaults {
  id: string
  defaultVisible: boolean
}

export interface MergedLayout {
  /** 註冊表認識的卡片,依顯示順序(含隱藏)。 */
  cards: LayoutCard[]
  /** 已儲存但此版 client 不認識的卡片,存檔時原樣帶回。 */
  unknown: LayoutCard[]
}

export function defaultLayout(defs: CardDefaults[]): LayoutCard[] {
  return defs.map((d) => ({ id: d.id, visible: d.defaultVisible }))
}

export function mergeLayout(defs: CardDefaults[], saved: LayoutCard[] | null | undefined): MergedLayout {
  const known = new Map(defs.map((d) => [d.id, d]))
  if (!saved || saved.length === 0) return { cards: defaultLayout(defs), unknown: [] }

  const seen = new Set<string>()
  const cards: LayoutCard[] = []
  const unknown: LayoutCard[] = []
  for (const raw of saved) {
    if (!raw || typeof raw.id !== 'string' || seen.has(raw.id)) continue
    seen.add(raw.id)
    const item = { id: raw.id, visible: raw.visible !== false }
    if (known.has(raw.id)) cards.push(item)
    else unknown.push(item)
  }

  // 新增的卡片:接在預設順序上「前一張已存在」的卡片之後;前面都沒有就放最前面
  // 的第一張後繼之前;兩邊都找不到(結果是空的)就直接放進去。
  defs.forEach((def, idx) => {
    if (cards.some((c) => c.id === def.id)) return
    const item = { id: def.id, visible: def.defaultVisible }
    let insertAt = -1
    for (let i = idx - 1; i >= 0; i--) {
      const pos = cards.findIndex((c) => c.id === defs[i].id)
      if (pos >= 0) {
        insertAt = pos + 1
        break
      }
    }
    if (insertAt < 0) {
      let before = cards.length
      for (let i = idx + 1; i < defs.length; i++) {
        const pos = cards.findIndex((c) => c.id === defs[i].id)
        if (pos >= 0) {
          before = pos
          break
        }
      }
      insertAt = before
    }
    cards.splice(insertAt, 0, item)
  })
  return { cards, unknown }
}

export function moveCard(cards: LayoutCard[], fromId: string, toId: string): LayoutCard[] {
  const from = cards.findIndex((c) => c.id === fromId)
  const to = cards.findIndex((c) => c.id === toId)
  if (from < 0 || to < 0 || from === to) return cards
  const next = cards.slice()
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

export function setCardVisible(cards: LayoutCard[], id: string, visible: boolean): LayoutCard[] {
  return cards.map((c) => (c.id === id ? { ...c, visible } : c))
}

/** 把卡片從隱藏變成顯示時,移到清單最後一張可見卡片的後面(出現在使用者眼前的末端)。 */
export function showCardAtEnd(cards: LayoutCard[], id: string): LayoutCard[] {
  const target = cards.find((c) => c.id === id)
  if (!target) return cards
  const rest = cards.filter((c) => c.id !== id)
  let lastVisible = -1
  rest.forEach((c, i) => {
    if (c.visible) lastVisible = i
  })
  rest.splice(lastVisible + 1, 0, { ...target, visible: true })
  return rest
}

export function isDefaultLayout(defs: CardDefaults[], cards: LayoutCard[]): boolean {
  const base = defaultLayout(defs)
  return base.length === cards.length && base.every((b, i) => cards[i].id === b.id && cards[i].visible === b.visible)
}

export function buildPayload(merged: MergedLayout): LayoutPayload {
  return { version: DASHBOARD_LAYOUT_VERSION, cards: [...merged.cards, ...merged.unknown] }
}

export function sameLayout(a: LayoutCard[], b: LayoutCard[]): boolean {
  return a.length === b.length && a.every((c, i) => c.id === b[i].id && c.visible === b[i].visible)
}
