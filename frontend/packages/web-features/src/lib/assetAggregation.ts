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
  /** Signed debt; credit-card positive balances are counted in assets. */
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
  positiveAssetTotal?: number
  negativeAssetTotal?: number
}

/** 负债类账户类型:余额带符号计入净值(欠款为负扣减、溢缴为正增加),展示欠款时才取 abs。 */
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
 * 单币种净值汇总。资产、负债都**带符号**累加,净值 = 资产 + 负债,跟 mobile
 * `local_account_repository.getNetWorthBreakdown` 口径一致:负债账户余额通常为负
 * (欠款,扣减净值),为正表示溢缴款(还多了的钱,经济上是资产头寸,增加净值)。
 * 历史教训:这里曾逐账户 `Math.abs` 再做减法,正常数据(欠款为负)下结果碰巧相同,
 * 直到溢缴/正余额负债账户出现才暴露 —— 把多打进去的钱又反向当欠款扣了一遍。
 *
 * 入参**必须是同一币种**的账户(由 {@link splitByCurrency} 保证)—— 传混币种进来
 * 得到的就是那个错的合并数字,这正是本模块要避免的。
 */
export function computeCurrencySummary(rows: ReadAccount[]): AssetSummary {
  let assetTotal = 0
  let liabilityTotal = 0
  for (const row of rows) {
    const raw = accountBalance(row)
    if (LIABILITY_TYPES.has(row.account_type || '') && !(row.account_type === 'credit_card' && raw > 0)) liabilityTotal += raw
    else assetTotal += raw
  }
  return { assetTotal, liabilityTotal, netWorth: assetTotal + liabilityTotal }
}

/** UI credit amounts derived from the unchanged signed balance protocol. */
export function creditCardAmounts(balance: number, limit?: number | null) {
  return {
    debt: Math.max(-balance, 0),
    overpayment: Math.max(balance, 0),
    estimatedAvailable: limit != null && limit > 0 ? Math.max(limit + balance, 0) : null,
  }
}

/** Positive asset positions for charts; negative ordinary assets stay in summaries. */
export function convertPositiveAssetTotalsToBase(
  rows: ReadAccount[], base: string, auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[],
) {
  const positive = rows.filter((r) => accountBalance(r) > 0 &&
    (!LIABILITY_TYPES.has(r.account_type || '') || r.account_type === 'credit_card'))
    .map((r) => r.account_type === 'credit_card'
      ? { ...r, account_type: 'credit_card_overpayment' } : r)
  const negative = rows.filter((r) => accountBalance(r) < 0 &&
    !LIABILITY_TYPES.has(r.account_type || ''))
  const result = convertTypeTotalsToBase(positive, base, auto, overrides)
  const negativeResult = convertTypeTotalsToBase(negative, base, auto, overrides)
  return { ...result,
    missing: [...new Set([...result.missing, ...negativeResult.missing])].sort(),
    negativeAssets: [...negativeResult.totals.values()].reduce((a, b) => a + b, 0) }
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
      const positive = (group.positiveAssetTotal ?? (!group.isLiability ? Math.max(sub / eff.rate, 0) : 0)) * eff.rate
      const negative = (group.negativeAssetTotal ?? (!group.isLiability ? Math.min(sub / eff.rate, 0) : 0)) * eff.rate
      const existing = merged.get(group.type)
      if (existing) {
        existing.subtotals[0].value += sub
        existing.rows = existing.rows.concat(group.rows)
        existing.positiveAssetTotal = (existing.positiveAssetTotal ?? 0) + positive
        existing.negativeAssetTotal = (existing.negativeAssetTotal ?? 0) + negative
      } else {
        merged.set(group.type, {
          type: group.type,
          label: group.label,
          color: group.color,
          isLiability: group.isLiability,
          rows: [...group.rows],
          subtotals: [{ currency: base, value: sub }],
          positiveAssetTotal: positive,
          negativeAssetTotal: negative,
        })
      }
    }
  }
  return [...merged.values()]
}

/**
 * 首页「资产构成」donut 的跨币种折算聚合(#104)。
 *
 * 输入工作区全部账户(可能多账本多币种),按 {@link splitByCurrency} 切分后:
 *   - 每币种内按账户类型**带符号**累加(欠款为负、溢缴为正,同
 *     {@link computeCurrencySummary} 的负债符号口径,绝不逐账户 abs);
 *   - 每币种的类型小计 × {@link effectiveRateToBase} 折进主币种,按类型跨币种合并;
 *   - **铁律对齐**:缺失汇率的整币种剔除(missing 返回给展示层出脚注),
 *     绝不按 1 折入 —— 与净资产/资产/负债折算同口径。
 *
 * 返回 `totals`:key = 账户类型(原始 account_type,空归 'other'),value =
 * 主币种带符号合计;`missing` = 被剔除的币种(大写、排序)。负债类型也在
 * totals 里带符号出现,由调用方按自己的类型分组决定展示(donut 只画资产类)。
 */
export function convertTypeTotalsToBase(
  rows: ReadAccount[],
  base: string,
  auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[],
): { totals: Map<string, number>; missing: string[] } {
  const totals = new Map<string, number>()
  const missing = new Set<string>()
  for (const [cur, curRows] of splitByCurrency(rows)) {
    const eff = effectiveRateToBase(cur, base, auto, overrides)
    if (!eff) {
      missing.add(cur)
      continue
    }
    // 先按类型攒该币种小计,再整体 × rate —— 与 mergeGroupsToBase 的
    // 「subtotal 求和后折算」同构,避免逐行乘法的浮点噪声。
    const perType = new Map<string, number>()
    for (const row of curRows) {
      const type = row.account_type || 'other'
      perType.set(type, (perType.get(type) ?? 0) + accountBalance(row))
    }
    for (const [type, subtotal] of perType) {
      totals.set(type, (totals.get(type) ?? 0) + subtotal * eff.rate)
    }
  }
  return { totals, missing: [...missing].sort() }
}

/**
 * 持有中定期存款本金 → 主币种合计(#资产构成包含定期存款)。
 *
 * 口径:定期存款是「被锁定」的资产 —— 创建时后端已自动从原账户扣除本金(一笔
 * expense 关联交易,见 linked_transactions.reconcile_time_deposit_linked_tx),
 * 所以账户余额里**不含**本金;本金作为独立资产头寸在此计入。支取(withdrawn)后
 * 本息已通过 income 流回账户,**不能**再算(否则重复)。已收利息(通过「利息」按键
 * 生成的 income 交易)已进账户余额,也**不**在此重复加。
 *
 * 铁律对齐:`effectiveRateToBase` 缺失汇率的整币种直接剔除(不计入 total、返回
 * 给 missing 出脚注),绝不按 1 折入 —— 与净资产 / 资产 / 负债折算同口径。
 */
export type TimeDepositAssetInput = {
  id: string
  principal: number
  currency: string
  /** 仅 holding / matured(未支取)计入;withdrawn 本金已回账户,不计。 */
  status?: string | null
}

export function timeDepositTotalsToBase(
  deposits: TimeDepositAssetInput[],
  base: string,
  auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[],
): { total: number; missing: string[] } {
  const held = deposits.filter(
    (d) => (d.status || '') !== 'withdrawn' && Number(d.principal) > 0,
  )
  const byCur = new Map<string, number>()
  for (const d of held) {
    const cur = (d.currency || 'CNY').toUpperCase()
    byCur.set(cur, (byCur.get(cur) ?? 0) + Number(d.principal))
  }
  let total = 0
  const missing = new Set<string>()
  for (const [cur, subtotal] of byCur) {
    const eff = effectiveRateToBase(cur, base, auto, overrides)
    if (!eff) {
      missing.add(cur)
      continue
    }
    total += subtotal * eff.rate
  }
  return { total, missing: [...missing].sort() }
}
