/**
 * 0021 定期存款年利率的双口径换算。
 *
 * wire(服务端/快照)存小数:0.0325 = 3.25%;
 * 表单输入框按人类口径:「3.25」= 3.25%。
 * 两边换算集中在这里,避免 panel 回显和 page 提交各写一份导致漂移
 * (曾出现的 bug 类型:一边 *100 一边 /100,利率放大 100 倍)。
 */

/** wire 小数 → 输入框百分比字符串。0 / 非法 → 空串。 */
export function ratePercentFromWire(rate: number): string {
  if (!Number.isFinite(rate) || rate <= 0) return ''
  return String(Math.round(rate * 10000) / 100)
}

/** 输入框百分比字符串 → wire 小数。空 / 非法 / 负值 → 0。 */
export function rateWireFromPercent(input: string): number {
  const raw = (input || '').trim()
  if (raw === '') return 0
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return 0
  return n / 100
}
