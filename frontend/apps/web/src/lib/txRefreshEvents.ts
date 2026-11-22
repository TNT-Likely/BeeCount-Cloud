/** 交易列表刷新事件 — 退款 / 其它写操作完成后,松耦合地通知交易页重新拉取列表。
 *
 * 退款是从「原单详情弹窗」发起的,而交易列表由 TransactionsPage 独立管理,二者
 * 没有直接的 props 链路。用 window CustomEvent 解耦:退款成功 → dispatchTxRefresh(),
 * TransactionsPage 监听 → 重新 fetch 当前页。避免把刷新函数层层透传。
 */
export const TX_REFRESH_EVENT = 'beecount:tx-refresh'

export function dispatchTxRefresh(): void {
  window.dispatchEvent(new CustomEvent(TX_REFRESH_EVENT))
}

export function onTxRefresh(cb: () => void): () => void {
  const handler = () => cb()
  window.addEventListener(TX_REFRESH_EVENT, handler)
  return () => window.removeEventListener(TX_REFRESH_EVENT, handler)
}
