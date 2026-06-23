/**
 * 跟 mobile `lib/utils/currencies.dart` 保持同一份货币 code 列表(151 个,
 * 覆盖通行 ISO 4217;全部在汇率源 fawaz currency-api 有报价)。新增货币只需在
 * 此追加,前端两端都得同步更新。
 *
 * 不单独维护 symbol 表 —— web 上目前没有展示 symbol 的地方,需要时可从
 * [Intl.NumberFormat] 派生。币种名称同理走 [Intl.DisplayNames](见
 * [currencyDisplayName]),主流币种由 i18n key `currency.<CODE>` 覆盖。
 */

const CURRENCY_GROUPS: Array<{ region: string; codes: string[] }> = [
  { region: 'eastAsia', codes: ['CNY', 'JPY', 'KRW', 'HKD', 'TWD', 'MOP', 'MNT', 'KPW'] },
  { region: 'southeastAsia', codes: ['SGD', 'MYR', 'THB', 'IDR', 'PHP', 'VND', 'MMK', 'KHR', 'LAK', 'BND'] },
  { region: 'southAsia', codes: ['INR', 'PKR', 'BDT', 'LKR', 'NPR', 'BTN', 'MVR', 'AFN'] },
  { region: 'centralAsia', codes: ['KZT', 'UZS', 'TJS', 'TMT', 'KGS'] },
  { region: 'middleEast', codes: ['AED', 'SAR', 'ILS', 'TRY', 'QAR', 'KWD', 'BHD', 'OMR', 'JOD', 'LBP', 'IQD', 'IRR', 'YER', 'SYP', 'GEL', 'AMD', 'AZN'] },
  { region: 'europe', codes: ['EUR', 'GBP', 'CHF', 'SEK', 'NOK', 'DKK', 'PLN', 'CZK', 'HUF', 'RUB', 'BYN', 'UAH', 'RON', 'BGN', 'RSD', 'ISK', 'MDL', 'ALL', 'MKD', 'BAM', 'GIP'] },
  { region: 'northAmerica', codes: ['USD', 'CAD', 'MXN'] },
  { region: 'centralAmericaCaribbean', codes: ['GTQ', 'HNL', 'NIO', 'CRC', 'PAB', 'DOP', 'CUP', 'JMD', 'TTD', 'BSD', 'BBD', 'BZD', 'HTG', 'XCD', 'KYD', 'AWG', 'ANG', 'BMD'] },
  { region: 'southAmerica', codes: ['BRL', 'ARS', 'CLP', 'COP', 'PEN', 'UYU', 'PYG', 'BOB', 'VES', 'GYD', 'SRD'] },
  { region: 'oceania', codes: ['AUD', 'NZD', 'FJD', 'PGK', 'SBD', 'TOP', 'VUV', 'WST', 'XPF'] },
  { region: 'africa', codes: ['ZAR', 'EGP', 'NGN', 'KES', 'GHS', 'MAD', 'DZD', 'TND', 'LYD', 'ETB', 'UGX', 'TZS', 'RWF', 'XAF', 'XOF', 'MUR', 'BWP', 'NAD', 'ZMW', 'MWK', 'MZN', 'AOA', 'CDF', 'GMD', 'GNF', 'LRD', 'SLE', 'SDG', 'SSP', 'SOS', 'DJF', 'ERN', 'BIF', 'CVE', 'STN', 'SCR', 'KMF', 'LSL', 'SZL', 'MGA', 'MRU'] },
]

export const CURRENCY_CODES: readonly string[] = CURRENCY_GROUPS.flatMap((g) => g.codes)

export const CURRENCY_REGION_GROUPS = CURRENCY_GROUPS

/**
 * 用 [Intl.DisplayNames] 按 locale 本地化任意 ISO 货币名(en→"US Dollar",
 * zh-CN→"美元")。环境不支持 / 未知 code 时回退 code 本身。
 *
 * 组件层优先用 i18n key `currency.<CODE>` 覆盖(主流币种保留人工译名),
 * 仅在无覆盖时调用本函数 —— 这样长尾币种也能自动按当前语言显示名称。
 */
export function currencyDisplayName(code: string, locale: string): string {
  const upper = code.toUpperCase()
  try {
    const dn = new Intl.DisplayNames([locale], { type: 'currency' })
    return dn.of(upper) || upper
  } catch {
    return upper
  }
}
