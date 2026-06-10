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

/**
 * 一个账户类型分组(构成饼图 / 分组列表的最小单元)。subtotals 按币种拆 ——
 * 单币种入参时每组只有 1 条,跨币种(底部混合列表)时各币种独立累计、绝不相加。
 * 定义放 lib 是为了让 {@link mergeGroupsToBase} 这类纯聚合脱离 React 组件单测。
 */
export type AssetGroup = {
  type: string
  label: string
  color: string
  isLiability: boolean
  rows: ReadAccount[]
  subtotals: { currency: string; value: number }[]
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

/**
 * 把「每币种各自的类型分组」按汇率折算到主币种后,合并成一份主币种构成 ——
 * 用于折算汇总视图的合并饼图(`AssetsCompositionMini` currency=base)。
 *
 * 入参 `buckets` 是各币种的分组列表(单币种页里的 `computeTypeGroups` 产物按币种装好)。
 * 对每个币种取 {@link effectiveRateToBase},把该币种每组的 subtotal 求和后 × rate
 * 折进主币种,再按 type 跨币种累加成一份 `AssetGroup[]`。
 *
 * **铁律对齐**:缺失汇率(`effectiveRateToBase` 返回 null)的整币种直接剔除,
 * **绝不按 1 折入** —— 与净资产/资产/负债折算同口径。输出每组只有 1 条 subtotal(主币种),
 * 顺序沿用首次出现的类型顺序(即 `computeTypeGroups` 的 ACCOUNT_ORDER)。
 */
export function mergeGroupsToBase(
  buckets: { currency: string; groups: AssetGroup[] }[],
  base: string,
  auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[],
): AssetGroup[] {
  // type → 累加器。保留首次出现的 label/color/isLiability 与出现顺序。
  const merged = new Map<string, AssetGroup>()
  for (const bucket of buckets) {
    const eff = effectiveRateToBase(bucket.currency.toUpperCase(), base, auto, overrides)
    if (!eff) continue // 缺失汇率:整币种剔除,绝不按 1 折入
    for (const group of bucket.groups) {
      const sub = group.subtotals.reduce((s, x) => s + x.value, 0) * eff.rate
      const existing = merged.get(group.type)
      if (existing) {
        existing.subtotals[0].value += sub
        existing.rows = existing.rows.concat(group.rows)
      } else {
        merged.set(group.type, {
          type: group.type,
          label: group.label,
          color: group.color,
          isLiability: group.isLiability,
          rows: [...group.rows],
          subtotals: [{ currency: base, value: sub }],
        })
      }
    }
  }
  return [...merged.values()]
}
