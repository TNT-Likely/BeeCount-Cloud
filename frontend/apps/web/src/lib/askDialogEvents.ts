/**
 * AI 文档搜索弹窗的全局事件 — 跟 txDialogEvents 同模式。
 *
 * 任何地方(⌘K / 文档页内的「问 AI」按钮 / 顶部 nav 入口...)dispatch
 * `open-ask`,AppShell 顶层的 GlobalAskDialog 监听并打开。
 *
 * 这样:
 * - AskDialog 跟 ⌘K 解耦,不被 cmdk 的 keyboard nav / Command.List 渲染规则影响
 * - 任意页面都能触发,不需要先 navigate
 * - Dialog 关闭由 AskDialog 自己掌控,⌘K 早已关
 */
const OPEN_ASK_EVENT = 'beecount:open-ask'

export type OpenAskDetail = {
  query: string
}

export function dispatchOpenAsk(query: string): void {
  if (!query.trim()) return
  window.dispatchEvent(
    new CustomEvent<OpenAskDetail>(OPEN_ASK_EVENT, {
      detail: { query: query.trim() },
    }),
  )
}

export function listenOpenAsk(handler: (query: string) => void): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<OpenAskDetail>).detail
    if (detail?.query) handler(detail.query)
  }
  window.addEventListener(OPEN_ASK_EVENT, listener)
  return () => window.removeEventListener(OPEN_ASK_EVENT, listener)
}
