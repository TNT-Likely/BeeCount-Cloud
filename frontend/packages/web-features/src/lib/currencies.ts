/**
 * 跟 mobile `lib/utils/currencies.dart` 保持同一份货币 code 列表(~40 个)。
 * 新增货币只需在此追加,前端两端都得同步更新。
 *
 * 不再单独维护 symbol 表 —— web 上目前没有展示 symbol 的地方,需要时
 * 可以从 [Intl.NumberFormat] 派生(`new Intl.NumberFormat('zh-CN',
 * { style: 'currency', currency: code }).formatToParts(0).find(p => p.type === 'currency')?.value`),
 * 不必在这里再硬编码一份。
 */

const CURRENCY_GROUPS: Array<{ region: string; codes: string[] }> = [
  { region: 'eastAsia', codes: ['CNY', 'JPY', 'KRW', 'HKD', 'TWD'] },
  { region: 'southeastAsia', codes: ['SGD', 'MYR', 'THB', 'IDR', 'PHP', 'VND', 'MMK'] },
  { region: 'southAsia', codes: ['INR', 'PKR', 'BDT', 'LKR'] },
  { region: 'centralAsia', codes: ['KZT'] },
  { region: 'middleEast', codes: ['AED', 'SAR', 'ILS', 'TRY'] },
  { region: 'europe', codes: ['EUR', 'GBP', 'CHF', 'SEK', 'NOK', 'DKK', 'PLN', 'CZK', 'HUF', 'RUB', 'BYN', 'UAH'] },
  { region: 'northAmerica', codes: ['USD', 'CAD', 'MXN'] },
  { region: 'southAmerica', codes: ['BRL', 'ARS', 'CLP', 'COP', 'PEN'] },
  { region: 'oceania', codes: ['AUD', 'NZD'] },
  { region: 'africa', codes: ['ZAR', 'EGP', 'NGN'] },
]

export const CURRENCY_CODES: readonly string[] = CURRENCY_GROUPS.flatMap((g) => g.codes)

export const CURRENCY_REGION_GROUPS = CURRENCY_GROUPS
