import {
  fetchWorkspaceTransactions,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
  type WorkspaceTransaction,
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

/**
 * 按交易的 sync_id 拉取完整交易并打开全局交易详情弹窗。
 *
 * 业务实体(借款/存单/物品等)的详情弹窗里,明细行只持有 sync_id 摘要,
 * 用这个 helper 拉全量后复用 dispatchOpenDetailTx —— 这样从明细可跳到
 * 「原流水单子」进行查看/编辑/复制/删除,与交易流水页点开完全一致。
 *
 * 反向(流水 → 业务实体)由 GlobalEntityDialogs 的 flowLinks 处理。
 */
export function openTxDetailBySyncId(
  token: string,
  syncId: string | null | undefined,
  ledgerId?: string,
  onBeforeOpen?: () => void,
) {
  if (!token || !syncId) return
  void fetchWorkspaceTransactions(token, {
    ledgerId: ledgerId || undefined,
    txSyncId: syncId,
    limit: 1,
  })
    .then((page) => {
      const tx = page.items[0]
      if (tx) {
        // 先关闭来源实体弹窗(如存单/借款详情),避免两个弹窗叠加。
        onBeforeOpen?.()
        dispatchOpenDetailTx(tx)
      }
    })
    .catch(() => undefined)
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

/**
 * Account/Category/Tag 详情弹窗的"账本作用域"开关。
 * - 'all'    : 跨账本聚合(默认从一级页面 资产/分类/标签 进入)
 * - 'current': 当前账本(从首页图表 / Top 卡片等场景进入)
 * 弹窗 UI 顶部有 segmented toggle 让用户切换,这里只是"打开时的默认值"。
 */
export type DetailScope = 'all' | 'current'

export type DetailOpenOptions = {
  defaultScope?: DetailScope
}

// ========== Account ==========
const DETAIL_ACCOUNT_EVENT = 'bee:open-detail-account'

type AccountDetailPayload = {
  account: WorkspaceAccount
  defaultScope: DetailScope
}

export function dispatchOpenDetailAccount(
  account: WorkspaceAccount,
  options?: DetailOpenOptions,
) {
  const payload: AccountDetailPayload = {
    account,
    defaultScope: options?.defaultScope ?? 'current',
  }
  window.dispatchEvent(new CustomEvent(DETAIL_ACCOUNT_EVENT, { detail: payload }))
}

export function onOpenDetailAccount(
  handler: (account: WorkspaceAccount, defaultScope: DetailScope) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<AccountDetailPayload>).detail
    if (detail?.account) handler(detail.account, detail.defaultScope)
  }
  window.addEventListener(DETAIL_ACCOUNT_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_ACCOUNT_EVENT, wrapped)
}

// ========== Tag ==========
const DETAIL_TAG_EVENT = 'bee:open-detail-tag'

type TagDetailPayload = {
  tag: WorkspaceTag
  defaultScope: DetailScope
}

export function dispatchOpenDetailTag(
  tag: WorkspaceTag,
  options?: DetailOpenOptions,
) {
  const payload: TagDetailPayload = {
    tag,
    defaultScope: options?.defaultScope ?? 'current',
  }
  window.dispatchEvent(new CustomEvent(DETAIL_TAG_EVENT, { detail: payload }))
}

export function onOpenDetailTag(
  handler: (tag: WorkspaceTag, defaultScope: DetailScope) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<TagDetailPayload>).detail
    if (detail?.tag) handler(detail.tag, detail.defaultScope)
  }
  window.addEventListener(DETAIL_TAG_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_TAG_EVENT, wrapped)
}

// ========== Category ==========
const DETAIL_CATEGORY_EVENT = 'bee:open-detail-category'
const EDIT_CATEGORY_EVENT = 'bee:open-edit-category'

type CategoryDetailPayload = {
  category: WorkspaceCategory
  defaultScope: DetailScope
}

export function dispatchOpenDetailCategory(
  category: WorkspaceCategory,
  options?: DetailOpenOptions,
) {
  const payload: CategoryDetailPayload = {
    category,
    defaultScope: options?.defaultScope ?? 'current',
  }
  window.dispatchEvent(
    new CustomEvent(DETAIL_CATEGORY_EVENT, { detail: payload }),
  )
}

export function dispatchOpenEditCategory(category: WorkspaceCategory) {
  window.dispatchEvent(
    new CustomEvent(EDIT_CATEGORY_EVENT, { detail: category }),
  )
}

export function onOpenDetailCategory(
  handler: (category: WorkspaceCategory, defaultScope: DetailScope) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<CategoryDetailPayload>).detail
    if (detail?.category) handler(detail.category, detail.defaultScope)
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

// ========== Loan / Deposit (流水详情「关联记录」跳回业务实体) ==========
// 交易详情的 flowLinks 跳回借款/存单时用 URL 参数 + 本事件双通道:
//   跨页面 → 目标页挂载后由 URL 参数 effect 打开;
//   同页面 → 组件不重挂载、URL 参数 effect 的 ref guard 会拦截,靠本事件打开。
const OPEN_DETAIL_LOAN_EVENT = 'bee:open-detail-loan'
const OPEN_DETAIL_DEPOSIT_EVENT = 'bee:open-detail-deposit'
const OPEN_DETAIL_CONTACT_EVENT = 'bee:open-detail-contact'
const OPEN_DETAIL_MEMBER_EVENT = 'bee:open-detail-member'

export function dispatchOpenDetailLoan(loanId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_DETAIL_LOAN_EVENT, { detail: loanId }))
}

export function onOpenDetailLoan(handler: (loanId: string) => void): () => void {
  const wrapped = (e: Event) => {
    const id = (e as CustomEvent<string>).detail
    if (id) handler(id)
  }
  window.addEventListener(OPEN_DETAIL_LOAN_EVENT, wrapped)
  return () => window.removeEventListener(OPEN_DETAIL_LOAN_EVENT, wrapped)
}

export function dispatchOpenDetailDeposit(depositId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_DETAIL_DEPOSIT_EVENT, { detail: depositId }))
}

export function onOpenDetailDeposit(handler: (depositId: string) => void): () => void {
  const wrapped = (e: Event) => {
    const id = (e as CustomEvent<string>).detail
    if (id) handler(id)
  }
  window.addEventListener(OPEN_DETAIL_DEPOSIT_EVENT, wrapped)
  return () => window.removeEventListener(OPEN_DETAIL_DEPOSIT_EVENT, wrapped)
}

/** 交易详情「关联记录」跳回联系人详情弹窗(0047)。 */
export function dispatchOpenDetailContact(contactId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_DETAIL_CONTACT_EVENT, { detail: contactId }))
}

export function onOpenDetailContact(handler: (contactId: string) => void): () => void {
  const wrapped = (e: Event) => {
    const id = (e as CustomEvent<string>).detail
    if (id) handler(id)
  }
  window.addEventListener(OPEN_DETAIL_CONTACT_EVENT, wrapped)
  return () => window.removeEventListener(OPEN_DETAIL_CONTACT_EVENT, wrapped)
}

/** 交易详情「关联记录」跳回成员详情弹窗(0055)。 */
export function dispatchOpenDetailMember(memberId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_DETAIL_MEMBER_EVENT, { detail: memberId }))
}

export function onOpenDetailMember(handler: (memberId: string) => void): () => void {
  const wrapped = (e: Event) => {
    const id = (e as CustomEvent<string>).detail
    if (id) handler(id)
  }
  window.addEventListener(OPEN_DETAIL_MEMBER_EVENT, wrapped)
  return () => window.removeEventListener(OPEN_DETAIL_MEMBER_EVENT, wrapped)
}

/**
 * 交易详情「关联记录」跳回报销详情弹窗(0056)。
 * kind='reimbursement' → 报销单；kind='item' → 待报销项。
 */
export type ReimbursementDetailTarget =
  | { kind: 'reimbursement'; id: string }
  | { kind: 'item'; id: string }

const OPEN_DETAIL_REIMBURSEMENT_EVENT = 'bee:open-detail-reimbursement'

export function dispatchOpenDetailReimbursement(target: ReimbursementDetailTarget) {
  window.dispatchEvent(
    new CustomEvent<ReimbursementDetailTarget>(OPEN_DETAIL_REIMBURSEMENT_EVENT, { detail: target }),
  )
}

export function onOpenDetailReimbursement(
  handler: (target: ReimbursementDetailTarget) => void,
): () => void {
  const wrapped = (e: Event) => {
    const target = (e as CustomEvent<ReimbursementDetailTarget>).detail
    if (target && target.id) handler(target)
  }
  window.addEventListener(OPEN_DETAIL_REIMBURSEMENT_EVENT, wrapped)
  return () => window.removeEventListener(OPEN_DETAIL_REIMBURSEMENT_EVENT, wrapped)
}

/**
 * 交易详情「关联记录」跳回储蓄罐详情弹窗。
 *
 * 与联系人同款双通道:URL 参数(跨页/可分享)+ 事件(同页兜底)。
 * 储蓄罐详情弹窗里点「查看流水」打开交易详情,交易详情的关联记录再能跳回来,
 * 形成互相跳转闭环。
 */
const OPEN_DETAIL_SAVING_PLAN_EVENT = 'bee:open-detail-saving-plan'

export function dispatchOpenDetailSavingPlan(planId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_DETAIL_SAVING_PLAN_EVENT, { detail: planId }))
}

export function onOpenDetailSavingPlan(handler: (planId: string) => void): () => void {
  const wrapped = (e: Event) => {
    const id = (e as CustomEvent<string>).detail
    if (id) handler(id)
  }
  window.addEventListener(OPEN_DETAIL_SAVING_PLAN_EVENT, wrapped)
  return () => window.removeEventListener(OPEN_DETAIL_SAVING_PLAN_EVENT, wrapped)
}
