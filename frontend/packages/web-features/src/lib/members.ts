/** 账本内自定义成员(0024)的纯业务逻辑。

对应需求「账单中可以选择成员,可以选择分摊比例、金额」:
- 成员是账本自己维护的人名(爸爸/妈妈/我),不是登录用户;
- 一笔支出可按人头拆给多个成员,每人一个「比例 + 金额」;
- **金额是权威**,比例是派生展示值(sum(amount) 必须等于账单金额,服务端校验)。

这里放可单测的纯计算,服务端仍是最终权威(超额/不足会被拒)。
 */
import type { TxMemberShare } from '@beecount/api-client'

/** 金额容差,跟服务端 _validate_member_shares 同款(吸收浮点累计误差)。 */
export const SHARE_AMOUNT_TOLERANCE = 0.011

export type MemberInputError = 'nameRequired' | 'memberRequired' | 'amountMismatch'

/** 成员表单校验:名字必填。 */
export function validateMemberInput(input: { name: string }): MemberInputError | null {
  if (!input.name.trim()) return 'nameRequired'
  return null
}

/** 份额列表校验(前端即时预提示,服务端权威):至少一人 + 合计等于账单金额。 */
export function validateMemberShares(
  shares: readonly { memberId?: string; memberName?: string | null; amount?: number }[],
  billAmount: number,
): MemberInputError | null {
  if (shares.length === 0) return 'memberRequired'
  for (const s of shares) {
    if (!s.memberId && !s.memberName) return 'memberRequired'
  }
  const total = shares.reduce((sum, s) => sum + (Number(s.amount) || 0), 0)
  if (Math.abs(total - billAmount) > SHARE_AMOUNT_TOLERANCE) return 'amountMismatch'
  return null
}

/**
 * 把账单金额**均分**给一组成员,余数补给前面的人,保证合计精确等于金额
 * (避免 100/3 出现 33.33*3=99.99 的浮点缺口)。
 *
 * @param memberIds 参与分摊的成员 syncId(去重、保持传入顺序)
 * @param amount    账单金额
 */
export function evenShares(
  memberIds: readonly string[],
  amount: number,
  nameById?: ReadonlyMap<string, string>,
): TxMemberShare[] {
  const ids = Array.from(new Set(memberIds.filter(Boolean)))
  if (ids.length === 0) return []
  const total = Number(amount) || 0
  // 先按「向下取整到分」分,再把余下的分补给前几位。
  const cents = Math.round(total * 100)
  const base = Math.floor(cents / ids.length)
  let remainder = cents - base * ids.length
  return ids.map((memberId) => {
    const extra = remainder > 0 ? 1 : 0
    if (remainder > 0) remainder -= 1
    const value = (base + extra) / 100
    return {
      memberId,
      memberName: nameById?.get(memberId) ?? null,
      ratio: total > 0 ? value / total : 0,
      amount: value,
    }
  })
}

/** 按金额重算每份的 ratio(展示用;金额为 0 时 ratio 记 0)。 */
export function withRatios(
  shares: readonly TxMemberShare[],
  billAmount: number,
): TxMemberShare[] {
  const total = Number(billAmount) || 0
  return shares.map((s) => ({
    ...s,
    ratio: total > 0 ? (Number(s.amount) || 0) / total : 0,
  }))
}

/** 份额合计。表单里金额是字符串绑 input,这里宽松接受 number | string。 */
export function sharesTotal(
  shares: readonly { amount?: number | string }[],
): number {
  return shares.reduce((sum, s) => sum + (Number(s.amount) || 0), 0)
}

// --------------------------------------------------------------------------- #
// form ↔ wire 转换(跟 lib/txExtensions 的 legsToWire/legsFromWire 同款)      #
// --------------------------------------------------------------------------- #

/** 成员分摊的一份(0024),对齐 forms.MemberShareForm 的形状。 */
export type MemberShareWireInput = {
  member_id: string
  member_name: string
  /** 分摊金额,字符串绑 input。 */
  amount: string
}

/**
 * 表单 shares → 提交给后端的 camelCase 数组。
 *
 * 任一行缺成员 ID 或金额非法 → 返回 null(不发该字段,跟 legs 同策略:
 * 宁可不提交,也不提交一份残缺的分摊让服务端 400)。
 * ratio 由后端/统计端按 amount 派生,这里一并带上方便读侧直接展示。
 */
export function sharesToWire(
  shares: readonly MemberShareWireInput[],
  billAmount: number,
): TxMemberShare[] | null {
  if (!shares || shares.length === 0) return null
  const total = Number(billAmount) || 0
  const out: TxMemberShare[] = []
  for (const s of shares) {
    const memberId = (s.member_id || '').trim()
    const amount = Number(s.amount)
    if (!memberId || !Number.isFinite(amount) || amount < 0) return null
    out.push({
      memberId,
      memberName: (s.member_name || '').trim() || null,
      ratio: total > 0 ? Math.round((amount / total) * 10000) / 10000 : 0,
      amount: Math.round(amount * 100) / 100
    })
  }
  return out
}

/** 后端 wire 数组 → 表单行(编辑回填)。 */
export function sharesFromWire(
  wire: readonly TxMemberShare[] | null | undefined,
): MemberShareWireInput[] {
  if (!Array.isArray(wire) || wire.length === 0) return []
  return wire.map((s) => ({
    member_id: s.memberId || '',
    member_name: s.memberName || '',
    amount: String(s.amount ?? '')
  }))
}
