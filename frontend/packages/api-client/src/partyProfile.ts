/** 联系人 / 成员共用的「关联人资料」。后端存成 camelCase JSON(profile_json)。 */
export type ContactMethodType = 'phone' | 'wechat' | 'alipay' | 'email' | 'qq' | 'other'

export const CONTACT_METHOD_TYPES: ContactMethodType[] = [
  'phone', 'wechat', 'alipay', 'email', 'qq', 'other',
]

export interface PartyContactMethod {
  type: ContactMethodType
  value: string
  /** 可选备注，例如「工作号」「家人」。 */
  label: string
}

export interface PartyBankCard {
  bank: string
  cardNo: string
  holder: string
  note: string
}

export interface PartyProfile {
  avatarFileId: string | null
  contactMethods: PartyContactMethod[]
  bankCards: PartyBankCard[]
}

export function emptyPartyProfile(): PartyProfile {
  return { avatarFileId: null, contactMethods: [], bankCards: [] }
}

/** 后端返回 / 历史数据 → 完整结构：缺字段补齐、脏类型过滤。 */
export function normalizePartyProfile(value: unknown): PartyProfile | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Partial<PartyProfile>
  const methods = (Array.isArray(raw.contactMethods) ? raw.contactMethods : [])
    .filter((m): m is PartyContactMethod =>
      Boolean(m && typeof m.value === 'string' && m.value.trim()))
    .map((m) => ({
      type: CONTACT_METHOD_TYPES.includes(m.type) ? m.type : ('other' as ContactMethodType),
      value: m.value.trim(),
      label: typeof m.label === 'string' ? m.label : '',
    }))
  const cards = (Array.isArray(raw.bankCards) ? raw.bankCards : [])
    .filter((c): c is PartyBankCard => Boolean(c && typeof c === 'object'))
    .map((c) => ({
      bank: typeof c.bank === 'string' ? c.bank : '',
      cardNo: typeof c.cardNo === 'string' ? c.cardNo : '',
      holder: typeof c.holder === 'string' ? c.holder : '',
      note: typeof c.note === 'string' ? c.note : '',
    }))
    .filter((c) => Boolean(c.bank || c.cardNo || c.holder || c.note))
  const avatarFileId = typeof raw.avatarFileId === 'string' && raw.avatarFileId.trim()
    ? raw.avatarFileId.trim()
    : null
  if (!avatarFileId && methods.length === 0 && cards.length === 0) return null
  return { avatarFileId, contactMethods: methods, bankCards: cards }
}

/** 卡号脱敏：保留前 4 后 4，短号只留后 4。编辑态展示原值，只读态用它。 */
export function maskCardNo(value: string): string {
  const v = (value || '').replace(/\s+/g, '')
  if (!v) return ''
  if (v.length <= 8) return `**** ${v.slice(-4)}`
  return `${v.slice(0, 4)} **** **** ${v.slice(-4)}`
}
