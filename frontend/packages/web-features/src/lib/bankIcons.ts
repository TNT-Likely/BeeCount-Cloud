/**
 * 内置银行/支付工具图标注册表(0026)。
 *
 * 设计取舍:不伪造各银行商标 SVG(版权风险且维护成本高),用「品牌色圆角徽章 +
 * 单个汉字/字母」的抽象图标 —— 这正是国内记账 App 常见的银行标识做法,
 * 识别度足够且完全自包含。
 *
 * 匹配规则:matchBankIcon 按关键词子串命中(大小写不敏感),**顺序敏感** ——
 * 具体的银行必须排在通用/易冲突条目之前(如 spdb「上海浦东发展」要先于
 * bosc「上海」)。
 */

export type BankIconGroup = 'bank' | 'pay' | 'other'

export type BankIcon = {
  /** 唯一 key,存进账户 icon 字段。 */
  key: string
  /** 展示名(选择器/提示用)。 */
  label: string
  /** 匹配关键词:银行常用名/别名/英文缩写,小写子串命中。 */
  keywords: string[]
  /** 品牌色(徽章背景)。 */
  color: string
  /** 徽章字符。 */
  glyph: string
  /** 徽章字符颜色,缺省白。 */
  fg?: string
  group: BankIconGroup
}

export const BANK_ICONS: readonly BankIcon[] = [
  // ---- 国有大行 / 股份制(具体条目优先)----
  { key: 'spdb', label: '浦发银行', keywords: ['浦发', 'spdb', '上海浦东发展'], color: '#0D5AA8', glyph: '浦', group: 'bank' },
  { key: 'icbc', label: '工商银行', keywords: ['工商', '工行', 'icbc'], color: '#C8161D', glyph: '工', group: 'bank' },
  { key: 'abc', label: '农业银行', keywords: ['农业', '农行', 'abc'], color: '#00875A', glyph: '农', group: 'bank' },
  { key: 'boc', label: '中国银行', keywords: ['中国银行', '中行', 'boc'], color: '#A6192E', glyph: '中', group: 'bank' },
  { key: 'ccb', label: '建设银行', keywords: ['建设', '建行', 'ccb'], color: '#0B5CA8', glyph: '建', group: 'bank' },
  { key: 'bocom', label: '交通银行', keywords: ['交通', '交行', 'bocom'], color: '#0665B3', glyph: '交', group: 'bank' },
  { key: 'psbc', label: '邮政储蓄银行', keywords: ['邮政', '邮储', 'psbc'], color: '#007A33', glyph: '邮', group: 'bank' },
  { key: 'cmb', label: '招商银行', keywords: ['招商', '招行', 'cmb'], color: '#D9231E', glyph: '招', group: 'bank' },
  { key: 'citic', label: '中信银行', keywords: ['中信', 'citic'], color: '#D71920', glyph: '信', group: 'bank' },
  { key: 'cmbc', label: '民生银行', keywords: ['民生', 'cmbc'], color: '#009B6B', glyph: '民', group: 'bank' },
  { key: 'cib', label: '兴业银行', keywords: ['兴业', 'cib'], color: '#005BAC', glyph: '兴', group: 'bank' },
  { key: 'ceb', label: '光大银行', keywords: ['光大', 'ceb'], color: '#7A1E7E', glyph: '光', group: 'bank' },
  { key: 'pab', label: '平安银行', keywords: ['平安', 'pab', '深发展', '深圳发展'], color: '#F26B21', glyph: '平', group: 'bank' },
  { key: 'hxb', label: '华夏银行', keywords: ['华夏', 'hxb'], color: '#E60012', glyph: '华', group: 'bank' },
  { key: 'cgb', label: '广发银行', keywords: ['广发', 'cgb', '广东发展'], color: '#D9231E', glyph: '广', group: 'bank' },
  { key: 'evergrowing', label: '浙商银行', keywords: ['浙商', 'czb'], color: '#005BAC', glyph: '浙', group: 'bank' },
  { key: 'cbhb', label: '渤海银行', keywords: ['渤海', 'cbhb'], color: '#005BAC', glyph: '渤', group: 'bank' },
  { key: 'egb', label: '恒丰银行', keywords: ['恒丰', 'egb'], color: '#005BAC', glyph: '恒', group: 'bank' },
  // ---- 城商行 ----
  { key: 'hsb', label: '徽商银行', keywords: ['徽商', 'hsb'], color: '#C8161D', glyph: '徽', group: 'bank' },
  { key: 'bob', label: '北京银行', keywords: ['北京银行', 'bob'], color: '#005BAC', glyph: '京', group: 'bank' },
  { key: 'bosc', label: '上海银行', keywords: ['上海银行', 'bosc'], color: '#005BAC', glyph: '沪', group: 'bank' },
  { key: 'jsb', label: '江苏银行', keywords: ['江苏银行', 'jsb'], color: '#005BAC', glyph: '苏', group: 'bank' },
  { key: 'njcb', label: '南京银行', keywords: ['南京银行', 'njcb'], color: '#005BAC', glyph: '宁', group: 'bank' },
  { key: 'nbcb', label: '宁波银行', keywords: ['宁波银行', 'nbcb'], color: '#D9231E', glyph: '甬', group: 'bank' },
  { key: 'hzb', label: '杭州银行', keywords: ['杭州银行', 'hzb'], color: '#005BAC', glyph: '杭', group: 'bank' },
  { key: 'gzb', label: '广州银行', keywords: ['广州银行', 'gzb'], color: '#C8161D', glyph: '穗', group: 'bank' },
  { key: 'szdb', label: '深圳银行', keywords: ['深圳银行', 'szdb'], color: '#005BAC', glyph: '深', group: 'bank' },
  { key: 'cqb', label: '重庆银行', keywords: ['重庆银行', 'cqb'], color: '#C8161D', glyph: '渝', group: 'bank' },
  { key: 'cdb', label: '成都银行', keywords: ['成都银行', 'cdb'], color: '#C8161D', glyph: '蓉', group: 'bank' },
  { key: 'xab', label: '西安银行', keywords: ['西安银行', 'xab'], color: '#005BAC', glyph: '秦', group: 'bank' },
  { key: 'cscb', label: '长沙银行', keywords: ['长沙银行', 'cscb'], color: '#005BAC', glyph: '湘', group: 'bank' },
  { key: 'qlb', label: '齐鲁银行', keywords: ['齐鲁', 'qlb'], color: '#005BAC', glyph: '齐', group: 'bank' },
  { key: 'xib', label: '厦门国际银行', keywords: ['厦门', 'xib'], color: '#005BAC', glyph: '厦', group: 'bank' },
  { key: 'fjhx', label: '福建海峡银行', keywords: ['海峡', 'fjhx'], color: '#005BAC', glyph: '海', group: 'bank' },
  { key: 'wzcb', label: '温州银行', keywords: ['温州银行', 'wzcb'], color: '#005BAC', glyph: '温', group: 'bank' },
  { key: 'tzcb', label: '台州银行', keywords: ['台州银行', 'tzcb'], color: '#005BAC', glyph: '台', group: 'bank' },
  { key: 'zyb', label: '中原银行', keywords: ['中原', 'zyb'], color: '#C8161D', glyph: '原', group: 'bank' },
  { key: 'lzcb', label: '兰州银行', keywords: ['兰州银行', 'lzcb'], color: '#005BAC', glyph: '兰', group: 'bank' },
  { key: 'hrcb', label: '天津银行', keywords: ['天津银行', 'hrcb'], color: '#005BAC', glyph: '津', group: 'bank' },
  // ---- 支付工具 ----
  { key: 'alipay', label: '支付宝', keywords: ['支付宝', 'alipay', '蚂蚁'], color: '#1677FF', glyph: '支', group: 'pay' },
  { key: 'wechat', label: '微信支付', keywords: ['微信', 'wechat', '财付通'], color: '#07C160', glyph: '微', group: 'pay' },
  { key: 'unionpay', label: '云闪付', keywords: ['云闪付', 'unionpay', '银联'], color: '#E60012', glyph: '闪', group: 'pay' },
  { key: 'jdpay', label: '京东支付', keywords: ['京东', 'jdpay', 'jd'], color: '#E1251B', glyph: '京', group: 'pay' },
  { key: 'meituan', label: '美团支付', keywords: ['美团', 'meituan'], color: '#FFC300', glyph: '美', fg: '#1F1F1F', group: 'pay' },
  { key: 'douyin', label: '抖音支付', keywords: ['抖音', 'douyin', '字节跳动'], color: '#161823', glyph: '抖', group: 'pay' },
  { key: 'dcep', label: '数字人民币', keywords: ['数字人民币', 'dcep', '数币'], color: '#C8161D', glyph: '数', group: 'pay' },
  // ---- 通用(放最后,匹配优先级最低)----
  { key: 'cash', label: '现金', keywords: ['现金', 'cash'], color: '#10B981', glyph: '现', group: 'other' },
  { key: 'bankcard', label: '银行卡', keywords: ['银行卡', '储蓄卡', '借记卡'], color: '#64748B', glyph: '卡', group: 'other' },
  { key: 'other', label: '其他', keywords: ['其他', '其它', 'other'], color: '#6B7280', glyph: '其', group: 'other' }
]

const _ICON_BY_KEY = new Map(BANK_ICONS.map((b) => [b.key, b]))

/** 按 key 取图标定义;未注册 key → null(调用方自行回退类型图标)。 */
export function bankIconOf(key: string | null | undefined): BankIcon | null {
  if (!key) return null
  return _ICON_BY_KEY.get(key) ?? null
}

/**
 * 按开户行名称自动匹配内置图标。命中返回 icon key,未识别返回 null
 * (由 UI 提示手动设置)。空名 → null。
 */
export function matchBankIcon(bankName: string | null | undefined): string | null {
  const name = (bankName || '').trim().toLowerCase()
  if (!name) return null
  // 去掉常见后缀再匹配一次,提高「XX银行股份」「XX银行有限责任公司」命中率
  const stripped = name
    .replace(/(股份)?(有限)?(责任)?公司/g, '')
    .replace(/银行/g, '')
    .trim()
  for (const icon of BANK_ICONS) {
    for (const kw of icon.keywords) {
      const k = kw.toLowerCase()
      if (name.includes(k) || (stripped.length >= 2 && stripped.includes(k))) {
        return icon.key
      }
    }
  }
  return null
}

/** 选择器分组标题 key → 分组。 */
export const BANK_ICON_GROUPS: { group: BankIconGroup; i18nKey: string }[] = [
  { group: 'bank', i18nKey: 'accounts.icon.groupBank' },
  { group: 'pay', i18nKey: 'accounts.icon.groupPay' },
  { group: 'other', i18nKey: 'accounts.icon.groupOther' }
]

/** 按关键词过滤选择器列表(匹配 label / keywords)。 */
export function filterBankIcons(query: string): readonly BankIcon[] {
  const q = query.trim().toLowerCase()
  if (!q) return BANK_ICONS
  return BANK_ICONS.filter(
    (b) =>
      b.label.toLowerCase().includes(q) ||
      b.keywords.some((k) => k.toLowerCase().includes(q))
  )
}
