import type { InvestmentFlow } from '@beecount/api-client'

/** 淨投入不為 0 的幣別(四捨五入到分之後),給首頁結餘/儲蓄率旁的小字用。 */
export function netInvestedRows(flow: InvestmentFlow | null | undefined): { currency: string; net: number }[] {
  return (flow?.by_currency ?? [])
    .map((r) => ({ currency: r.currency, net: Math.round(r.net_invested * 100) / 100 }))
    .filter((r) => Math.abs(r.net) >= 0.005)
}

/** 該幣別的股利收入(已含在首頁「收入」內);沒有或為 0 回 0。 */
export function dividendsOf(flow: InvestmentFlow | null | undefined, currency: string): number {
  const cur = currency.toUpperCase()
  return (flow?.by_currency ?? []).find((r) => r.currency.toUpperCase() === cur)?.dividends ?? 0
}

/**
 * 把各幣別淨投入組成一到兩段說明(轉入投資 / 由投資轉回),每段只出現一次句子,
 * 多幣別金額用 " + " 串起來:例「另有 476,495 + $2,001.00 淨投入投資…」。
 * `fmt` 負責金額格式;`texts` 是兩句 i18n 模板(用 {amount} 佔位)。
 */
export function describeNetInvested(
  rows: { currency: string; net: number }[],
  fmt: (value: number, currency: string) => string,
  texts: { out: (amount: string) => string; in: (amount: string) => string },
): string[] {
  const out = rows.filter((r) => r.net > 0).map((r) => fmt(r.net, r.currency))
  const back = rows.filter((r) => r.net < 0).map((r) => fmt(-r.net, r.currency))
  const parts: string[] = []
  if (out.length > 0) parts.push(texts.out(out.join(' + ')))
  if (back.length > 0) parts.push(texts.in(back.join(' + ')))
  return parts
}
