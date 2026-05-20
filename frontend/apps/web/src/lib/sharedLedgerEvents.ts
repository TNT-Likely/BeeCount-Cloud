/**
 * §7 共享账本全局事件 — CommandPalette / 其他 entry point 触发,
 * AppShell 顶层挂载的 SharedLedgerDialogs 监听并开对应 dialog。
 *
 * 跟 askDialogEvents / parseTxEvents / txDialogEvents 同模式,避免在每个
 * 触发点都重复挂 dialog state。
 */
const OPEN_JOIN_EVENT = 'beecount:shared-ledger-join'
const OPEN_MANAGE_EVENT = 'beecount:shared-ledger-manage'

export type OpenSharedManageDetail = {
  ledgerId: string
  ledgerName: string
  isOwner: boolean
}

export function dispatchOpenSharedJoin(): void {
  window.dispatchEvent(new CustomEvent(OPEN_JOIN_EVENT))
}

export function dispatchOpenSharedManage(detail: OpenSharedManageDetail): void {
  window.dispatchEvent(
    new CustomEvent<OpenSharedManageDetail>(OPEN_MANAGE_EVENT, { detail }),
  )
}

export function listenOpenSharedJoin(handler: () => void): () => void {
  const listener = () => handler()
  window.addEventListener(OPEN_JOIN_EVENT, listener)
  return () => window.removeEventListener(OPEN_JOIN_EVENT, listener)
}

export function listenOpenSharedManage(
  handler: (detail: OpenSharedManageDetail) => void,
): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<OpenSharedManageDetail>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(OPEN_MANAGE_EVENT, listener)
  return () => window.removeEventListener(OPEN_MANAGE_EVENT, listener)
}
