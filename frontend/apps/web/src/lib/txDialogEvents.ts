import type {
  WorkspaceAccount,
  WorkspaceCategory,
  WorkspaceTag,
  WorkspaceTransaction,
} from '@beecount/api-client'

/**
 * 跨组件触发实体详情/编辑弹窗的轻量事件总线 — 让 CommandPalette 等不在
 * 各 *Page 内的组件也能命令性地打开弹窗。
 *
 * 事件分两类:
 *   1) detail:在对应 Page 上展示「详情弹窗」(只读 + 编辑/删除入口)。
 *   2) edit:跳过详情直接进编辑(交易行的「编辑」按钮、详情里点编辑)。
 *
 * 用 window CustomEvent 而不是 React Context,因为:
 *  1) 触发方(CommandPalette / List 行)与接收方(各 *Page)不在同一棵子树;
 *  2) 事件是命令式("现在打开"),用 Context 反而要造 trigger counter 绕一道。
 *
 * 接收方在 *Page 用 useEffect + addEventListener 监听。
 * 如果用户当前不在对应页面,先 navigate 再 dispatch(调用方处理 50ms 延迟)。
 */

// ========== Transaction ==========
const NEW_TX_EVENT = 'bee:open-new-tx'
const EDIT_TX_EVENT = 'bee:open-edit-tx'
const DETAIL_TX_EVENT = 'bee:open-detail-tx'

// 监听器计数 — 让派发方判断"目标页是否挂载",决定是否展示「请到对应页编辑」
// 提示。比 dispatchEvent 自己的回执机制简单。
let editTxHandlerCount = 0
let editCategoryHandlerCount = 0

/**
 * 打开"新建交易" dialog 的可选预填项。
 * - happenedAt:ISO 8601 字符串,日历视图选中某一天后调用,prefill 到 happened_at 字段
 * - ledgerId:目标账本(留空 = 用 active ledger)
 */
export type NewTxPrefill = {
  happenedAt?: string
  ledgerId?: string
}

export function dispatchOpenNewTx(prefill?: NewTxPrefill) {
  window.dispatchEvent(new CustomEvent(NEW_TX_EVENT, { detail: prefill }))
}

export function dispatchOpenEditTx(tx: WorkspaceTransaction) {
  window.dispatchEvent(new CustomEvent(EDIT_TX_EVENT, { detail: tx }))
}

export function dispatchOpenDetailTx(tx: WorkspaceTransaction) {
  window.dispatchEvent(new CustomEvent(DETAIL_TX_EVENT, { detail: tx }))
}

export function hasEditTxHandler(): boolean {
  return editTxHandlerCount > 0
}

export function onOpenNewTx(handler: (prefill?: NewTxPrefill) => void): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<NewTxPrefill | undefined>).detail
    handler(detail)
  }
  window.addEventListener(NEW_TX_EVENT, wrapped)
  return () => window.removeEventListener(NEW_TX_EVENT, wrapped)
}

export function onOpenEditTx(
  handler: (tx: WorkspaceTransaction) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceTransaction>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(EDIT_TX_EVENT, wrapped)
  editTxHandlerCount += 1
  return () => {
    window.removeEventListener(EDIT_TX_EVENT, wrapped)
    editTxHandlerCount -= 1
  }
}

export function onOpenDetailTx(
  handler: (tx: WorkspaceTransaction) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceTransaction>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(DETAIL_TX_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_TX_EVENT, wrapped)
}

// ========== Account ==========
const DETAIL_ACCOUNT_EVENT = 'bee:open-detail-account'

export function dispatchOpenDetailAccount(account: WorkspaceAccount) {
  window.dispatchEvent(new CustomEvent(DETAIL_ACCOUNT_EVENT, { detail: account }))
}

export function onOpenDetailAccount(
  handler: (account: WorkspaceAccount) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceAccount>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(DETAIL_ACCOUNT_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_ACCOUNT_EVENT, wrapped)
}

// ========== Tag ==========
const DETAIL_TAG_EVENT = 'bee:open-detail-tag'

export function dispatchOpenDetailTag(tag: WorkspaceTag) {
  window.dispatchEvent(new CustomEvent(DETAIL_TAG_EVENT, { detail: tag }))
}

export function onOpenDetailTag(
  handler: (tag: WorkspaceTag) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceTag>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(DETAIL_TAG_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_TAG_EVENT, wrapped)
}

// ========== Category ==========
const DETAIL_CATEGORY_EVENT = 'bee:open-detail-category'
const EDIT_CATEGORY_EVENT = 'bee:open-edit-category'

export function dispatchOpenDetailCategory(category: WorkspaceCategory) {
  window.dispatchEvent(
    new CustomEvent(DETAIL_CATEGORY_EVENT, { detail: category }),
  )
}

export function dispatchOpenEditCategory(category: WorkspaceCategory) {
  window.dispatchEvent(
    new CustomEvent(EDIT_CATEGORY_EVENT, { detail: category }),
  )
}

export function onOpenDetailCategory(
  handler: (category: WorkspaceCategory) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceCategory>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(DETAIL_CATEGORY_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_CATEGORY_EVENT, wrapped)
}

export function onOpenEditCategory(
  handler: (category: WorkspaceCategory) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceCategory>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(EDIT_CATEGORY_EVENT, wrapped)
  editCategoryHandlerCount += 1
  return () => {
    window.removeEventListener(EDIT_CATEGORY_EVENT, wrapped)
    editCategoryHandlerCount -= 1
  }
}

export function hasEditCategoryHandler(): boolean {
  return editCategoryHandlerCount > 0
}
