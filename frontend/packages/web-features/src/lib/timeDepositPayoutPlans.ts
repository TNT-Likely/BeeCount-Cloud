/** 定期存款「到息计划」(0028,多笔)的纯逻辑。

表单行(date / amount 是输入态字符串)↔ wire payload 的转换放在这里,
保持可单测、与页面组件解耦。过滤口径跟服务端
``_normalize_payout_plans`` 一致:date 不可解析或金额非正的行直接丢掉 ——
宁可少一条,也不提交一条解释不了的。
*/

export type PayoutPlanFormLike = {
  date?: string | null
  amount?: string | number | null
  note?: string | null
}

export type PayoutPlanWire = {
  date: string
  amount: number
  note?: string | null
}

/** 表单行 → wire:丢掉 date 为空 / 金额非正 / 金额非数字的行。 */
export function payoutPlansToWire(
  plans: readonly PayoutPlanFormLike[] | null | undefined,
): PayoutPlanWire[] {
  const out: PayoutPlanWire[] = []
  for (const plan of plans || []) {
    if (!plan) continue
    const date = String(plan.date ?? '').trim()
    const amount = Number(plan.amount)
    if (!date || !Number.isFinite(amount) || amount <= 0) continue
    const note = String(plan.note ?? '').trim()
    out.push({ date, amount, note: note || null })
  }
  return out
}

/** 计划金额合计(展示用;计划不参与余额与统计)。 */
export function payoutPlansTotal(
  plans: readonly { amount?: number | string | null }[] | null | undefined,
): number {
  let total = 0
  for (const plan of plans || []) {
    const amount = Number(plan?.amount)
    if (Number.isFinite(amount)) total += amount
  }
  return Math.round(total * 100) / 100
}

/** 最近一条尚未到息的计划日期;全部已到息 / 无计划 → null。 */
export function nextPayoutDate(
  plans: readonly { date?: string | null; is_due?: boolean }[] | null | undefined,
): string | null {
  let next: string | null = null
  for (const plan of plans || []) {
    const date = String(plan?.date ?? '').trim()
    if (!date || plan?.is_due) continue
    if (next === null || date < next) next = date
  }
  return next
}
