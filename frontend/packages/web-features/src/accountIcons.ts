/**
 * 账户图标库 —— 银行卡 / 充值卡 / 常用账户图标 + 背景预设(issue #icon)。
 *
 * 设计要点:
 * - 图标来自 GitHub 开源项目(SVG,单色或彩色)。存放于
 *   `public/icons/banks/<key>.svg`,通过 /icons/banks/... 绝对路径引用,
 *   由 vite 静态托管,不打包进 bundle。
 * - key 稳定且短(如 'icbc'),持久化到账户的 icon_key 字段;前端按 key
 *   取 URL,后端无需理解 key 的含义。
 * - icon_type:'bank' | 'card' | 'custom'。'custom' 时前端读
 *   `/icons/account/custom/<key>.svg`(用户上传的)。
 */

export type AccountIconKind = 'bank' | 'card' | 'custom'

export interface AccountIconDef {
  key: string
  kind: AccountIconKind
  /** 图标显示名(用于选择器与 aria-label)。 */
  label: string
  /** 图标文件相对 public 的路径。 */
  src: string
}

/** 中国主要银行 —— SVG 来源:GitHub 开源银行 logo 集合(整理至 public/icons/banks/cn)。 */
export const BANK_ICON_KEYS_CN = [
  'icbc', 'ccb', 'boc', 'abc', 'bocom', 'cmb', 'citic', 'spdb', 'pingan',
  'merchants_cn', 'cgb', 'hxb', 'bccb', 'crcb', 'psbc', 'beijing', 'shanghai',
  'jiangsu', 'ningbo', 'sdb', 'bohaic', 'ceb', 'cmbc', 'suning', 'huaxia',
  'shengjing', 'bea', 'bocb', 'bowei', 'mingzhu', 'minfeng', 'dalian', 'changsha',
  'guangfa', 'lianfa', 'jinshi', 'changan', 'guomao', 'ruifeng', 'zhongxin',
] as const

/**
 * 银行图标定义。label 用于选择器显示;src 为 public 相对路径。
 * 单色 SVG 统一用 currentColor 以便在深/浅底色上自适应。
 */
export const BANK_ICONS: Record<string, AccountIconDef> = {
  icbc: { key: 'icbc', kind: 'bank', label: '中国工商银行', src: '/icons/banks/cn/icbc.svg' },
  ccb: { key: 'ccb', kind: 'bank', label: '中国建设银行', src: '/icons/banks/cn/ccb.svg' },
  boc: { key: 'boc', kind: 'bank', label: '中国银行', src: '/icons/banks/cn/boc.svg' },
  abc: { key: 'abc', kind: 'bank', label: '中国农业银行', src: '/icons/banks/cn/abc.svg' },
  bocom: { key: 'bocom', kind: 'bank', label: '交通银行', src: '/icons/banks/cn/bocom.svg' },
  cmb: { key: 'cmb', kind: 'bank', label: '招商银行', src: '/icons/banks/cn/cmb.svg' },
  citic: { key: 'citic', kind: 'bank', label: '中信银行', src: '/icons/banks/cn/citic.svg' },
  spdb: { key: 'spdb', kind: 'bank', label: '浦发银行', src: '/icons/banks/cn/spdb.svg' },
  pingan: { key: 'pingan', kind: 'bank', label: '平安银行', src: '/icons/banks/cn/pingan.svg' },
  guangfa: { key: 'guangfa', kind: 'bank', label: '广发银行', src: '/icons/banks/cn/guangfa.svg' },
  hxb: { key: 'hxb', kind: 'bank', label: '华夏银行', src: '/icons/banks/cn/hxb.svg' },
  psbc: { key: 'psbc', kind: 'bank', label: '邮政储蓄银行', src: '/icons/banks/cn/psbc.svg' },
  cgb: { key: 'cgb', kind: 'bank', label: '中国光大银行', src: '/icons/banks/cn/cgb.svg' },
  ceb: { key: 'ceb', kind: 'bank', label: '中国光大银行', src: '/icons/banks/cn/ceb.svg' },
  cmbc: { key: 'cmbc', kind: 'bank', label: '中国民生银行', src: '/icons/banks/cn/cmbc.svg' },
  beijing: { key: 'beijing', kind: 'bank', label: '北京银行', src: '/icons/banks/cn/beijing.svg' },
  shanghai: { key: 'shanghai', kind: 'bank', label: '上海银行', src: '/icons/banks/cn/shanghai.svg' },
  bea: { key: 'bea', kind: 'bank', label: '东亚银行', src: '/icons/banks/cn/bea.svg' },
  bocb: { key: 'bocb', kind: 'bank', label: '中国银行(香港)', src: '/icons/banks/cn/bocb.svg' },
  changsha: { key: 'changsha', kind: 'bank', label: '长沙银行', src: '/icons/banks/cn/changsha.svg' },
  changan: { key: 'changan', kind: 'bank', label: '长安银行', src: '/icons/banks/cn/changan.svg' },
  dongguan: { key: 'dongguan', kind: 'bank', label: '东莞银行', src: '/icons/banks/cn/dongguan.svg' },
  fudian: { key: 'fudian', kind: 'bank', label: '富滇银行', src: '/icons/banks/cn/fudian.svg' },
  guangdong: { key: 'guangdong', kind: 'bank', label: '广发银行', src: '/icons/banks/cn/guangdong.svg' },
  hangzhou: { key: 'hangzhou', kind: 'bank', label: '杭州银行', src: '/icons/banks/cn/hangzhou.svg' },
  harbin: { key: 'harbin', kind: 'bank', label: '哈尔滨银行', src: '/icons/banks/cn/harbin.svg' },
  hengfeng: { key: 'hengfeng', kind: 'bank', label: '恒丰银行', src: '/icons/banks/cn/hengfeng.svg' },
  huaxia: { key: 'huaxia', kind: 'bank', label: '华夏银行', src: '/icons/banks/cn/huaxia.svg' },
  jiangsu: { key: 'jiangsu', kind: 'bank', label: '江苏银行', src: '/icons/banks/cn/jiangsu.svg' },
  jilin: { key: 'jilin', kind: 'bank', label: '吉林银行', src: '/icons/banks/cn/jilin.svg' },
  lianfa: { key: 'lianfa', kind: 'bank', label: '联发银行', src: '/icons/banks/cn/lianfa.svg' },
  luwang: { key: 'luwang', kind: 'bank', label: '鲁旺银行', src: '/icons/banks/cn/luwang.svg' },
  mingzhu: { key: 'mingzhu', kind: 'bank', label: '明珠银行', src: '/icons/banks/cn/mingzhu.svg' },
  minfeng: { key: 'minfeng', kind: 'bank', label: '民丰银行', src: '/icons/banks/cn/minfeng.svg' },
  ningbo: { key: 'ningbo', kind: 'bank', label: '宁波银行', src: '/icons/banks/cn/ningbo.svg' },
  qingdao: { key: 'qingdao', kind: 'bank', label: '青岛银行', src: '/icons/banks/cn/qingdao.svg' },
  sdb: { key: 'sdb', kind: 'bank', label: '深圳发展银行', src: '/icons/banks/cn/sdb.svg' },
  shengjing: { key: 'shengjing', kind: 'bank', label: '盛京银行', src: '/icons/banks/cn/shengjing.svg' },
  suzhou: { key: 'suzhou', kind: 'bank', label: '苏州银行', src: '/icons/banks/cn/suzhou.svg' },
  tianjin: { key: 'tianjin', kind: 'bank', label: '天津银行', src: '/icons/banks/cn/tianjin.svg' },
  xiamen: { key: 'xiamen', kind: 'bank', label: '厦门银行', src: '/icons/banks/cn/xiamen.svg' },
  zhongjin: { key: 'zhongjin', kind: 'bank', label: '中金银行', src: '/icons/banks/cn/zhongjin.svg' },
  // —— 支付/网联/钱包 ——
  alipay: { key: 'alipay', kind: 'card', label: '支付宝', src: '/icons/banks/cn/alipay.svg' },
  wechat: { key: 'wechat', kind: 'card', label: '微信支付', src: '/icons/banks/cn/wechat.svg' },
  unionpay: { key: 'unionpay', kind: 'card', label: '银联', src: '/icons/banks/cn/unionpay.svg' },
  // —— 国际卡组织 ——
  visa: { key: 'visa', kind: 'card', label: 'Visa', src: '/icons/banks/intl/visa.svg' },
  mastercard: { key: 'mastercard', kind: 'card', label: 'MasterCard', src: '/icons/banks/intl/mastercard.svg' },
  amex: { key: 'amex', kind: 'card', label: 'American Express', src: '/icons/banks/intl/amex.svg' },
  jcb: { key: 'jcb', kind: 'card', label: 'JCB', src: '/icons/banks/intl/jcb.svg' },
  discover: { key: 'discover', kind: 'card', label: 'Discover', src: '/icons/banks/intl/discover.svg' },
  maestro: { key: 'maestro', kind: 'card', label: 'Maestro', src: '/icons/banks/intl/maestro.svg' },
  diners: { key: 'diners', kind: 'card', label: '大来卡', src: '/icons/banks/intl/diners.svg' },
  elo: { key: 'elo', kind: 'card', label: 'Elo', src: '/icons/banks/intl/elo.svg' },
  mir: { key: 'mir', kind: 'card', label: 'Mir', src: '/icons/banks/intl/mir.svg' },
  paypal: { key: 'paypal', kind: 'card', label: 'PayPal', src: '/icons/banks/intl/paypal.svg' },
  // —— 充值卡 / 交通卡 / 会员卡 ——
  subway_card: { key: 'subway_card', kind: 'card', label: '交通卡', src: '/icons/account/subway_card.svg' },
  bus_card: { key: 'bus_card', kind: 'card', label: '公交卡', src: '/icons/account/bus_card.svg' },
  oil_card: { key: 'oil_card', kind: 'card', label: '加油卡', src: '/icons/account/oil_card.svg' },
  shopping_card: { key: 'shopping_card', kind: 'card', label: '购物卡', src: '/icons/account/shopping_card.svg' },
  meal_card: { key: 'meal_card', kind: 'card', label: '饭卡/餐卡', src: '/icons/account/meal_card.svg' },
  gift_card: { key: 'gift_card', kind: 'card', label: '礼品卡', src: '/icons/account/gift_card.svg' },
  phone_card: { key: 'phone_card', kind: 'card', label: '话费充值卡', src: '/icons/account/phone_card.svg' },
  game_card: { key: 'game_card', kind: 'card', label: '游戏点卡', src: '/icons/account/game_card.svg' },
  movie_card: { key: 'movie_card', kind: 'card', label: '观影卡', src: '/icons/account/movie_card.svg' },
  // —— 泛用账户图标 ——
  cash: { key: 'cash', kind: 'card', label: '现金', src: '/icons/account/cash.svg' },
  bank_card: { key: 'bank_card', kind: 'card', label: '银行卡', src: '/icons/account/bank_card.svg' },
  credit_card: { key: 'credit_card', kind: 'card', label: '信用卡', src: '/icons/account/credit_card.svg' },
  wallet: { key: 'wallet', kind: 'card', label: '钱包', src: '/icons/account/wallet.svg' },
  piggy: { key: 'piggy', kind: 'card', label: '存钱罐', src: '/icons/account/piggy.svg' },
  crypto: { key: 'crypto', kind: 'card', label: '数字钱包', src: '/icons/account/crypto.svg' },
}

/** 背景预设 —— CSS 渐变,避免二进制背景文件,深浅色主题都能自适应。 */
export interface AccountBackgroundDef {
  key: string
  label: string
  /** 应用于账户卡片的渐变。 */
  css: string
  /** 卡片上的正文颜色。 */
  textColor: string
  /** 卡片上的次级文字颜色。 */
  subColor: string
}

export const ACCOUNT_BACKGROUNDS: AccountBackgroundDef[] = [
  { key: 'default', label: '默认', css: 'linear-gradient(135deg, #1e293b 0%, #334155 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.82)' },
  { key: 'indigo', label: '靛蓝', css: 'linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.82)' },
  { key: 'blue', label: '宝蓝', css: 'linear-gradient(135deg, #1d4ed8 0%, #0ea5e9 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'teal', label: '青碧', css: 'linear-gradient(135deg, #0d9488 0%, #14b8a6 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'green', label: '翠绿', css: 'linear-gradient(135deg, #15803d 0%, #22c55e 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'amber', label: '琥珀', css: 'linear-gradient(135deg, #b45309 0%, #f59e0b 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.88)' },
  { key: 'rose', label: '玫红', css: 'linear-gradient(135deg, #be123c 0%, #f43f5e 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'purple', label: '紫罗', css: 'linear-gradient(135deg, #6d28d9 0%, #c026d3 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'dark', label: '曜石', css: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.8)' },
  { key: 'mist', label: '雾蓝', css: 'linear-gradient(135deg, #475569 0%, #64748b 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'cream', label: '暖棕', css: 'linear-gradient(135deg, #92400e 0%, #b45309 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.88)' },
  { key: 'ocean', label: '海洋', css: 'linear-gradient(135deg, #0c4a6e 0%, #0891b2 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'forest', label: '森林', css: 'linear-gradient(135deg, #14532d 0%, #4d7c0f 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.85)' },
  { key: 'sunset', label: '落日', css: 'linear-gradient(135deg, #7c2d12 0%, #ea580c 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.88)' },
  { key: 'mono', label: '石墨', css: 'linear-gradient(135deg, #27272a 0%, #52525b 100%)', textColor: '#ffffff', subColor: 'rgba(255,255,255,0.8)' },
]

export const BACKGROUND_MAP: Record<string, AccountBackgroundDef> = Object.fromEntries(
  ACCOUNT_BACKGROUNDS.map((b) => [b.key, b]),
)

/** 账户 → 默认图标推导(未设置 icon_key 时按 bank_name/account_type 猜)。 */
export function inferAccountIconKey(
  bankName: string | null | undefined,
  accountType: string | null | undefined,
): { key: string; kind: AccountIconKind } {
  const text = String(bankName || '')
  for (const [needle, key] of BANK_NAME_HINTS as Array<[string, string]>) {
    if (text.includes(needle)) return { key, kind: 'bank' }
  }
  const type = String(accountType || '')
  if (type === 'alipay') return { key: 'alipay', kind: 'card' }
  if (type === 'wechat') return { key: 'wechat', kind: 'card' }
  if (type === 'credit_card') return { key: 'credit_card', kind: 'card' }
  if (type === 'bank_card') return { key: 'bank_card', kind: 'card' }
  if (type === 'cash') return { key: 'cash', kind: 'card' }
  return { key: 'bank_card', kind: 'card' }
}

const BANK_NAME_HINTS: Array<[string, string]> = [
  ['工商', 'icbc'], ['工行', 'icbc'], ['ICBC', 'icbc'],
  ['建设', 'ccb'], ['建行', 'ccb'], ['CCB', 'ccb'],
  ['中国银行', 'boc'], ['中行', 'boc'], ['BOC', 'boc'],
  ['农业', 'abc'], ['农行', 'abc'], ['ABC', 'abc'],
  ['交通', 'bocom'], ['交行', 'bocom'],
  ['招商', 'cmb'], ['招行', 'cmb'], ['CMB', 'cmb'],
  ['中信', 'citic'], ['CITIC', 'citic'],
  ['浦发', 'spdb'], ['SPDB', 'spdb'],
  ['平安', 'pingan'],
  ['广发', 'guangfa'],
  ['华夏', 'huaxia'], ['HXB', 'huaxia'],
  ['邮政', 'psbc'], ['邮储', 'psbc'],
  ['光大', 'cgb'],
  ['民生', 'cmbc'], ['CMBC', 'cmbc'],
  ['北京', 'beijing'],
  ['上海', 'shanghai'],
  ['东亚', 'bea'], ['BEA', 'bea'],
  ['江苏', 'jiangsu'],
  ['宁波', 'ningbo'],
  ['盛京', 'shengjing'],
  ['支付宝', 'alipay'], ['Alipay', 'alipay'],
  ['微信', 'wechat'], ['WeChat', 'wechat'],
  ['银联', 'unionpay'], ['UnionPay', 'unionpay'],
  ['Visa', 'visa'], ['VISA', 'visa'],
  ['Master', 'mastercard'], ['万事达', 'mastercard'],
  ['运通', 'amex'], ['Amex', 'amex'], ['AMEX', 'amex'],
]

/** 图标 key → URL。取不到回退通用银行卡图标。 */
export function accountIconSrc(
  iconKey: string | null | undefined,
  iconType?: string | null,
): string {
  if (!iconKey) return '/icons/account/bank_card.svg'
  const def = BANK_ICONS[iconKey]
  if (def) return def.src
  if (iconType === 'custom') return `/icons/account/custom/${iconKey}.svg`
  // 已知的国际卡组织 key 直接落到 intl 目录。
  if (/^(visa|mastercard|amex|jcb|discover|maestro|diners|elo|mir|unionpay|paypal)$/.test(iconKey)) {
    return `/icons/banks/intl/${iconKey}.svg`
  }
  return `/icons/banks/cn/${iconKey}.svg`
}

/** 按用途分组的图标清单,供选择器渲染。 */
export const ACCOUNT_ICON_GROUPS: Array<{ key: string; label: string; icons: AccountIconDef[] }> = [
  {
    key: 'bank',
    label: '银行',
    icons: Object.values(BANK_ICONS).filter((d) => d.kind === 'bank'),
  },
  {
    key: 'payment',
    label: '支付 / 卡组织',
    icons: Object.values(BANK_ICONS).filter(
      (d) => d.kind === 'card' && !d.key.endsWith('_card'),
    ),
  },
  {
    key: 'stored',
    label: '充值卡 / 会员卡',
    icons: Object.values(BANK_ICONS).filter(
      (d) => d.kind === 'card' && d.key.endsWith('_card'),
    ),
  },
  {
    key: 'general',
    label: '通用',
    icons: Object.values(BANK_ICONS).filter((d) =>
      ['cash', 'bank_card', 'credit_card', 'wallet', 'piggy', 'crypto'].includes(d.key),
    ),
  },
]

/** 背景 → 内联 CSS(含自定义色兜底)。 */
export function accountBackgroundCss(backgroundKey: string | null | undefined): AccountBackgroundDef {
  return BACKGROUND_MAP[backgroundKey || ''] ?? ACCOUNT_BACKGROUNDS[0]
}