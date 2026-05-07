/**
 * B2/B3 全局事件 — CommandPalette 触发,GlobalParseTxDialog 监听后开 Dialog。
 * 跟 askDialogEvents / txDialogEvents 同模式。
 */
const OPEN_PARSE_TX_EVENT = 'beecount:open-parse-tx'

export type OpenParseTxDetail =
  | { mode: 'image'; image: Blob }
  | { mode: 'text'; text: string }

export function dispatchOpenParseTxImage(image: Blob): void {
  window.dispatchEvent(
    new CustomEvent<OpenParseTxDetail>(OPEN_PARSE_TX_EVENT, {
      detail: { mode: 'image', image },
    }),
  )
}

export function dispatchOpenParseTxText(text: string): void {
  window.dispatchEvent(
    new CustomEvent<OpenParseTxDetail>(OPEN_PARSE_TX_EVENT, {
      detail: { mode: 'text', text },
    }),
  )
}

export function listenOpenParseTx(handler: (detail: OpenParseTxDetail) => void): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<OpenParseTxDetail>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(OPEN_PARSE_TX_EVENT, listener)
  return () => window.removeEventListener(OPEN_PARSE_TX_EVENT, listener)
}
