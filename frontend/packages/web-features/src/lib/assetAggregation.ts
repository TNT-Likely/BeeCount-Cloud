import type { ExchangeRateOverride, ExchangeRatesResponse, ReadAccount } from '@beecount/api-client'

/**
 * 资产页多币种聚合的纯逻辑核心。
 *
 * 铁律:**资产统计绝不跨币种相加** —— $1000 不是 ¥1000。没有汇率基建,也不做换算,
 * 所以净值/资产/负债都先按币种切分再各算各的(单币种就退化成 1 组,展示维持原样)。
 * 这块逻辑抽到 lib 是为了能脱离 React 组件单测,锁住"不跨币种合并"这个契约 ——
 * 历史上这页就是因为裸加 balance 把多币种加错了。
 */

export type AssetSummary = {
  assetTotal: number
  liabilityTotal: number
  netWorth: number
}

/** 负债类账户类型:余额按 |balance| 计欠款,从净值里扣减。 */
export const LIABILITY_TYPES = new Set(['credit_card', 'loan'])

/** 账户展示余额:优先 server 聚合后的 balance(含所有交易),否则回退 initial_balance。 */
export function accountBalance(row: ReadAccount): number {
  const stats = row as ReadAccount & { balance?: number | null }
  return typeof stats.balance === 'number' && stats.balance !== null
    ? stats.balance
    : row.initial_balance ?? 0
}

/**
 * 按币种切分账户 —— 所有跨币种聚合的第一步。币种缺省按 CNY,统一大写归一
 * (`usd` / `USD` 视作同一种)。返回的 Map 保持插入顺序。
 */
export function splitByCurrency(rows: ReadAccount[]): Map<string, ReadAccount[]> {
  const map = new Map<string, ReadAccount[]>()
  for (const row of rows) {
    const cur = (row.currency || 'CNY').toUpperCase()
    const arr = map.get(cur)
    if (arr) arr.push(row)
    else map.set(cur, [row])
  }
  return map
}

/**
 * 单币种净值汇总。负债类按 |balance| 累计欠款,资产类保留符号(透支账户 balance<0
 * 会扣减总资产),跟 mobile `local_account_repository.getNetWorthBreakdown` 口径一致。
 *
 * 入参**必须是同一币种**的账户(由 {@link splitByCurrency} 保证)—— 传混币种进来
 * 得到的就是那个错的合并数字,这正是本模块要避免的。
 */
export function computeCurrencySummary(rows: ReadAccount[]): AssetSummary {
  let assetTotal = 0
  let liabilityTotal = 0
  for (const row of rows) {
    const raw = accountBalance(row)
    if (LIABILITY_TYPES.has(row.account_type || '')) liabilityTotal += Math.abs(raw)
    else assetTotal += raw
  }
  return { assetTotal, liabilityTotal, netWorth: assetTotal - liabilityTotal }
}

/** 有效汇率:override(1 quote = x base)优先;否则代理自动值(1 base = x quote)取倒数。缺失返回 null,绝不回落 1。 */
export function effectiveRateToBase(
  quote: string, base: string,
  auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[]
): { rate: number; source: 'manual' | 'auto'; date?: string } | null {
  if (quote === base) return { rate: 1, source: 'auto' }
  const ov = overrides.find((o) => o.base_currency === base && o.quote_currency === quote)
  if (ov) {
    const r = Number(ov.rate)
    return Number.isFinite(r) && r > 0 ? { rate: r, source: 'manual' } : null
  }
  const raw = Number(auto?.rates?.[quote])
  if (!Number.isFinite(raw) || raw <= 0) return null
  return { rate: 1 / raw, source: 'auto', date: auto!.rate_date }
}
