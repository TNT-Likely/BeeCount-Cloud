import type { ExchangeRateOverride, ExchangeRatesResponse } from '@beecount/api-client'
import {
  fetchExchangeRateOverrides,
  fetchExchangeRates,
} from '@beecount/api-client'
import { useEffect, useMemo } from 'react'

import { useAuth } from '../context/AuthContext'
import { usePageCache } from '../context/PageDataCacheContext'

/**
 * 主币种折算的汇率数据源(#104):给首页「资产构成」donut 这类工作区级跨币种
 * 聚合用,口径与资产页 AccountsPage 一致 —— base 取 profile.primary_currency,
 * auto 汇率 + 手动 override 并行拉、任一失败不阻塞(置 null / 空数组)。
 *
 * 缓存 key 与 AccountsPage 完全相同(`accounts:rates:${base}`),两边共享
 * PageDataCache:资产页拉过的汇率首页直接命中,不重复请求。
 *
 * 口径决策:
 *   - **单币种账户永远折算到自己**(effectiveBase = 该唯一币种):折算率恒 1、
 *     零误差,也不需要拉汇率 —— 即使主币种已设且不同(否则单币种外币用户的
 *     汇率永远拉不到,余额会被整币种剔除成 0)。
 *   - 多币种但未设主币种:needsBase=true,调用方出「设置主币种」引导,
 *     绝不猜测币种按 1 折算。
 *   - loading:多币种且 rates 还没就绪(首次拉取中)。调用方在 loading 期间
 *     不应渲染折算结果 —— 宁可短暂空态,也不能把 $ 当 ¥ 闪一下旧口径。
 */
export function usePrimaryCurrencyRates(
  accounts: ReadonlyArray<{ currency?: string | null }>,
): {
  base: string
  /** 实际折算目标:单币种 = 该唯一币种;多币种 = 主币种;无法确定 = '' */
  effectiveBase: string
  singleCurrency: boolean
  /** 多币种但未设主币种 —— 调用方出引导态 */
  needsBase: boolean
  rates: ExchangeRatesResponse | null
  rateOverrides: ExchangeRateOverride[]
  /** 多币种折算所需的汇率尚在拉取中(单币种恒 false) */
  loading: boolean
} {
  const { token, profileMe } = useAuth()
  const base = profileMe?.primary_currency || ''

  // 币种签名:只在"涉及哪些币种"变化时才触发重拉,账户数组每次刷新换引用
  // 不影响。空/未填币种不参与统计(splitByCurrency 兜底 CNY 是展示口径,
  // 这里先按原始语义算 distinct,空币种由 splitByCurrency 归一)。
  const currencySig = useMemo(
    () =>
      [
        ...new Set(
          accounts
            .map((a) => (a.currency || '').trim().toUpperCase())
            .filter(Boolean),
        ),
      ]
        .sort()
        .join(','),
    [accounts],
  )
  const distinct = useMemo(
    () => new Set(currencySig ? currencySig.split(',') : []),
    [currencySig],
  )
  const singleCurrency = distinct.size <= 1
  const effectiveBase = singleCurrency ? (currencySig || '') : base
  const needsBase = !singleCurrency && !effectiveBase

  // key 带 base 维度:切换主币种后不会复用旧 base 的汇率缓存(同 AccountsPage)。
  const [rates, setRates] = usePageCache<ExchangeRatesResponse | null>(
    base ? `accounts:rates:${base}` : 'accounts:rates:',
    null,
  )
  const [rateOverrides, setRateOverrides] = usePageCache<ExchangeRateOverride[]>(
    base ? `accounts:rateOverrides:${base}` : 'accounts:rateOverrides:',
    [],
  )

  useEffect(() => {
    // 单币种 / 未设主币种:不需要汇率,清掉本组件态(不动其它 base 的缓存)。
    if (!base || distinct.size < 2) {
      setRates(null)
      setRateOverrides([])
      return
    }
    let cancelled = false
    void Promise.all([
      fetchExchangeRates(token, base).catch(() => null),
      fetchExchangeRateOverrides(token).catch(
        () => [] as ExchangeRateOverride[],
      ),
    ]).then(([r, o]) => {
      if (cancelled) return
      setRates(r)
      setRateOverrides(o)
    })
    return () => {
      cancelled = true
    }
    // setRates / setRateOverrides 为 usePageCache 的稳定 dispatch,distinct 由
    // currencySig 派生,直接依赖 sig 防引用抖动。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, base, currencySig])

  const loading = !singleCurrency && !needsBase && rates === null
  return { base, effectiveBase, singleCurrency, needsBase, rates, rateOverrides, loading }
}
