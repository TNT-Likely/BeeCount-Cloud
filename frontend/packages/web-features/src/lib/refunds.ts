/** 交易退款(0022)的纯业务逻辑。

对应 App issue #465 的「退款功能」:挂在原始支出交易上的退款记录。
服务端是权威(累计不超原单由 write 路径校验),这里放前端的同款预校验 +
列表派生计算,保持可单测。
 */

/** 状态机:pending → received;received / cancelled 无下一档。 */
export const REFUND_STATUSES = ['pending', 'received', 'cancelled'] as const

export type RefundStatus = (typeof REFUND_STATUSES)[number]

/** pending 的下一档是 received;终态(received/cancelled)返回 null。 */
export function nextRefundStatus(status: string): RefundStatus | null {
  return status === 'pending' ? 'received' : null
}

export type RefundProgress = {
  /** 已退比例 0..1;原单金额缺失/非正时恒 0。 */
  ratio: number
  /** none = 未退;partial = 部分退;full = 已退满。 */
  state: 'none' | 'partial' | 'full'
}

/**
 * 退款进度(与原服务端 remaining_refundable 同口径)。
 * refundedTotal 传服务端现算值(不含 cancelled)。
 */
export function refundProgress(
  originalAmount: number | null | undefined,
  refundedTotal: number,
): RefundProgress {
  const original = Number(originalAmount)
  if (!Number.isFinite(original) || original <= 0) {
    return { ratio: 0, state: 'none' }
  }
  const total = Math.max(Number(refundedTotal) || 0, 0)
  const ratio = Math.min(total / original, 1)
  const state: RefundProgress['state'] =
    total <= 0 ? 'none' : total >= original - 0.011 ? 'full' : 'partial'
  return { ratio, state }
}

/** 金额容差:与服务端 _AMOUNT_TOLERANCE 一致(浮点累计误差)。 */
const AMOUNT_TOLERANCE = 0.011

/**
 * 流水金额删除线判定(卡维度,与服务端 refunded_to_account 同口径)。
 *
 * 只有「退回本卡」的退款累计 ≥ 原单金额才画删除线。跨卡退款(统一退到别的卡 /
 * 分别退到其他卡)钱到了别的卡,本卡这笔支出仍然成立、余额也没回来,不能算
 * 已退款 —— 与卡的角度(账户余额)保持一致,避免不同维度看到相反结论。
 *
 * 老服务端没返回 refunded_to_account 时退化为总口径(refunded_total)。
 */
export function isFullyRefundedToCard(row: {
  amount: number
  refunded_total?: number
  refunded_to_account?: number
}): boolean {
  const backToCard = row.refunded_to_account ?? row.refunded_total ?? 0
  return backToCard > 0 && backToCard >= row.amount - AMOUNT_TOLERANCE
}

/**
 * 交易详情弹窗的「已全额退款」判定(0028)—— 与流水行同一卡维度口径。
 *
 * 详情里的 refunds 是实时拉的(刚登记的退款立刻可见),所以从
 * refundCardSplit / refundCardSplitFromList 的分解结果判,而不是读交易行的
 * refunded_to_account(可能旧于刚写的退款)。只有退回本卡的累计 ≥ 原单金额
 * 才算全额退款:跨卡退款钱到了别的卡,本卡支出仍成立,不置灰不画线。
 */
export function isFullyRefundedFromSplit(
  originalAmount: number | null | undefined,
  split: RefundCardSplit,
): boolean {
  const backToCard = Number(split?.backToCard) || 0
  return isFullyRefundedToCard({
    amount: Number(originalAmount) || 0,
    refunded_to_account: backToCard,
  })
}

export type RefundCardSplit = {
  /** 有效退款合计(不含 cancelled)。 */
  total: number
  /** 其中退回本卡的部分。 */
  backToCard: number
  /** 退至其他卡的部分(> 0 即「本卡支出仍成立」)。 */
  toOtherCards: number
}

/**
 * 支出统计口径的「净支出额」(全局/账本维度,与服务端统计端点同口径)。
 *
 * 「支出统计不含已退款的支出金额」:原单金额扣掉有效退款合计
 * (refunded_total,不含 cancelled),钳 0 —— 脏数据超退不产生负支出。
 * 老服务端没返回 refunded_total 时按 0 扣(即原额),行为退化自然。
 *
 * 维度分工:统计聚合(分类 / 标签 / 日历 / 年度报告 / 首页)都是全局维度,
 * 扣全部退款用本函数;「只看退回本卡」的卡维度(refunded_to_account)给
 * 流水删除线 / 账户页用 —— 跨卡退款钱没回本卡,那张卡的支出仍成立。
 *
 * 只对 expense 行调用(income / transfer 永无退款)。
 */
export function netExpenseAmount(row: {
  amount: number | string | null | undefined
  refunded_total?: number | null
}): number {
  const amount = Math.abs(Number(row.amount) || 0)
  const refunded = Math.max(Number(row.refunded_total) || 0, 0)
  return Math.max(amount - refunded, 0)
}

/**
 * 退款去向分解(交易详情弹窗用):总退款 / 退回本卡 / 退至其他卡。
 * refunded_to_account 缺省(老服务端)时按全部退回本卡计,toOtherCards 恒 ≥ 0。
 */
export function refundCardSplit(row: {
  refunded_total?: number
  refunded_to_account?: number
}): RefundCardSplit {
  const total = Math.max(Number(row.refunded_total) || 0, 0)
  const backToCard = Math.min(Math.max(row.refunded_to_account ?? total, 0), total)
  return { total, backToCard, toOtherCards: Math.max(total - backToCard, 0) }
}

/**
 * 同上,但从退款记录列表现算(交易详情弹窗用)。
 *
 * 详情弹窗里退款列表是实时拉的,而交易行数据可能旧于刚登记的退款,所以按
 * 记录现算,保证「登记完立刻看到正确分解」。sameAccountId = 原单所在账户:
 * split 按腿 accountId 匹配,unified / 老数据按顶层 account_id 匹配;匹配
 * 不上的都算「退至其他卡」—— 钱没回本卡,本卡支出仍成立。原单无账户(脏
 * 数据)时无从判断,整体按退回本卡计,不误报。
 */
export function refundCardSplitFromList(
  refunds: readonly {
    amount?: number | null
    status?: string
    account_id?: string | null
    refund_mode?: string
    refund_legs?: readonly { accountId?: string; amount?: number | string }[] | null
  }[],
  sameAccountId: string | null | undefined,
): RefundCardSplit {
  let total = 0
  let backToCard = 0
  for (const r of refunds) {
    if (!r || r.status === 'cancelled') continue
    const amount = Number(r.amount) || 0
    if (amount <= 0) continue
    total += amount
    if (!sameAccountId) continue
    if (r.refund_mode === 'split' && r.refund_legs?.length) {
      for (const leg of r.refund_legs) {
        const legAmount = Number(leg?.amount) || 0
        if (legAmount > 0 && leg?.accountId === sameAccountId) {
          backToCard += legAmount
        }
      }
    } else if (r.account_id === sameAccountId) {
      backToCard += amount
    }
  }
  if (!sameAccountId) backToCard = total
  return { total, backToCard, toOtherCards: Math.max(total - backToCard, 0) }
}

export type RefundDetailBreakdown<T> = {
  /** 按退款日期正序 —— 多次部分退时按时间线比服务端返回顺序更好读。 */
  ordered: T[]
  /** 有效退款合计(不含 cancelled,与服务端 refunded_total 同口径)。 */
  refunded: number
  /** 剩余可退(钳 0)。 */
  remaining: number
  /** 已退比例 0..1;原单金额非正时恒 0。 */
  ratio: number
}

/**
 * 交易详情「退款明细」弹窗(0028)的派生汇总 —— 只读,不落库。
 *
 * cancelled 的记录仍然保留在 ordered 里(用户要看自己撤掉过哪一笔),但
 * 不计入 refunded —— 与服务端 refunded_total 同口径。
 */
export function refundDetailBreakdown<T extends { happened_at?: string | null; amount?: number | null; status?: string }>(
  refunds: readonly T[],
  originalAmount: number | null | undefined,
): RefundDetailBreakdown<T> {
  const ordered = [...refunds].sort((a, b) =>
    String(a?.happened_at || '').localeCompare(String(b?.happened_at || '')),
  )
  const refunded = refunds
    .filter((r) => r?.status !== 'cancelled')
    .reduce((sum, r) => sum + (Number(r?.amount) || 0), 0)
  const original = Number(originalAmount)
  const safeOriginal = Number.isFinite(original) && original > 0 ? original : 0
  const ratio = safeOriginal > 0 ? Math.min(refunded / safeOriginal, 1) : 0
  return {
    ordered,
    refunded,
    remaining: Math.max(safeOriginal - refunded, 0),
    ratio,
  }
}

export type RefundInputError =
  | 'txRequired'
  | 'amountRequired'
  | 'overRefund'
  | 'legsRequired'
  | 'legsMismatch'

/**
 * 保存前预校验(与服务端 _make_refund_validator 同规则,错误 key 由 UI 翻译)。
 *
 * - originalTxId 必填
 * - amount > 0
 * - amount <= remainingRefundable(容差 0.011,跟服务端口径一致);
 *   remainingRefundable 传 null = 原单金额未知(原交易已删且无快照),
 *   此时跳过超额校验只查正数(服务端仍是最终权威);
 *   编辑时调用方传「remaining + 本笔原值」把本笔加回额度。
 * - refundMode === 'split' 时还需:至少一条腿、各腿金额为正且合计 == amount
 *   (容差 0.011,跟服务端口径一致)。重复账户 / 缺账户由 wire 转换层拦截,
 *   这里只判腿数与合计。
 */
export function validateRefundInput(input: {
  originalTxId: string
  amount: number
  remainingRefundable: number | null
  refundMode?: 'unified' | 'split'
  refundLegs?: { account_id?: string; account_name?: string; amount?: string | number }[]
}): RefundInputError | null {
  if (!input.originalTxId) return 'txRequired'
  const amount = Number(input.amount)
  if (!Number.isFinite(amount) || amount <= 0) return 'amountRequired'
  const remaining = input.remainingRefundable
  if (remaining !== null && Number.isFinite(remaining) && amount > remaining + 0.011) {
    return 'overRefund'
  }
  if (input.refundMode === 'split') {
    const legs = input.refundLegs || []
    if (legs.length === 0) return 'legsRequired'
    const total = legs.reduce((sum, l) => sum + (Number(l.amount) || 0), 0)
    if (Math.abs(total - amount) > 0.011) return 'legsMismatch'
  }
  return null
}

/** 原单选择器候选:只列支出交易,按 query 过滤(备注/分类/金额/id),日期新的在前。 */
export function matchRefundTxCandidates<
  T extends {
    id: string
    tx_type?: string
    amount?: number
    note?: string | null
    category_name?: string | null
    happened_at?: string
  },
>(txs: readonly T[], query: string, limit = 20): T[] {
  const q = query.trim().toLowerCase()
  const expenses = txs.filter((t) => (t.tx_type ?? 'expense') === 'expense')
  const filtered = q
    ? expenses.filter((t) => {
        const hay = [t.note, t.category_name, String(t.amount ?? ''), t.id]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        return hay.includes(q)
      })
    : expenses
  return filtered
    .slice()
    .sort((a, b) => String(b.happened_at ?? '').localeCompare(String(a.happened_at ?? '')))
    .slice(0, limit)
}

/** 原单展示标签:'10-12 数码 ¥500'(备注有时追加)。 */
export function refundTxLabel(tx: {
  amount?: number
  category_name?: string | null
  note?: string | null
  happened_at?: string
}): string {
  const date = String(tx.happened_at ?? '').slice(5, 10)
  const cat = tx.category_name || '—'
  const amount = Number(tx.amount ?? 0).toFixed(2)
  const note = tx.note ? ` ${tx.note}` : ''
  return `${date} ${cat} ¥${amount}${note}`
}
