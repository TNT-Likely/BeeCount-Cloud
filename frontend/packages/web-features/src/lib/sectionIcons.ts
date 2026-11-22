import type { CSSProperties } from 'react'

import {
  ArrowLeftRight,
  BookOpen,
  Building2,
  CalendarDays,
  CircleDollarSign,
  CreditCard,
  FolderTree,
  HandCoins,
  Landmark,
  LayoutDashboard,
  Package,
  Paperclip,
  PiggyBank,
  Receipt,
  ReceiptText,
  Settings,
  Shapes,
  Tag,
  Target,
  Upload,
  UserRound,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

import type { AppSection } from '../nav'

/**
 * 全站「功能模块图标」单一数据源。
 *
 * 背景:此前图标散落在各处硬编码 —— 顶部导航纯文字、头像下拉/命令面板/
 * 移动底栏各写各的、页面标题有的带图标有的不带,且同一模块在不同入口
 * 用了不同图标(例如「资产」在命令面板是 Wallet、头像下拉是 WalletCards)。
 *
 * 这里把每个 AppSection 的图标 + 品牌主色收敛成一份,所有入口共用:
 *   - 桌面顶部导航 / 头像下拉 / 命令面板 / 移动底栏(仅图标)
 *   - 各页面标题的 SectionIconBadge(图标 + 渐变底色)
 *
 * 图标全部来自 lucide-react(GitHub 25.8k★,项目已集成,无新依赖)。
 * 品牌色用于生成渐变徽章,色值刻意选高饱和度中间调,保证浅/深主题都清晰。
 */
export interface SectionIconMeta {
  /** Lucide 图标组件。 */
  Icon: LucideIcon
  /** 品牌主色(hex),用于图标徽章渐变 / 强调。 */
  color: string
}

const define = (Icon: LucideIcon, color: string): SectionIconMeta => ({ Icon, color })

export const SECTION_ICONS: Record<AppSection, SectionIconMeta> = {
  overview: define(LayoutDashboard, '#6366f1'), // 总览 · 靛蓝
  transactions: define(ArrowLeftRight, '#0ea5e9'), // 交易流水 · 天蓝
  calendar: define(CalendarDays, '#8b5cf6'), // 日历 · 紫
  attachments: define(Paperclip, '#64748b'), // 附件 · 石板灰
  accounts: define(Wallet, '#0d9488'), // 资产 · 青绿
  categories: define(FolderTree, '#f59e0b'), // 分类 · 琥珀
  tags: define(Tag, '#ec4899'), // 标签 · 粉
  members: define(Users, '#3b82f6'), // 成员 · 蓝
  contacts: define(UserRound, '#14b8a6'), // 联系人 · 青
  loans: define(HandCoins, '#f97316'), // 借款 · 橙
  deposits: define(Landmark, '#0284c7'), // 定期存款 · 深蓝
  'saving-plans': define(PiggyBank, '#10b981'), // 储蓄罐 · 翠绿
  reimbursements: define(ReceiptText, '#d946ef'), // 报销 · 洋红
  items: define(Package, '#a855f7'), // 物品 · 紫
  budgets: define(Target, '#e11d48'), // 预算 · 玫瑰
  ledgers: define(BookOpen, '#6366f1'), // 账本 · 靛蓝
  'settings-profile': define(Settings, '#64748b'), // 个人资料 · 石板灰
  'settings-ai': define(Shapes, '#8b5cf6'), // AI · 紫
  'settings-health': define(CircleDollarSign, '#10b981'), // 健康 · 绿
  'settings-devices': define(CreditCard, '#0ea5e9'), // 设备 · 天蓝
  'settings-developer': define(Settings, '#64748b'), // 开发者 · 灰
  'settings-appearance': define(Shapes, '#8b5cf6'), // 外观(兼容旧链接)
  'admin-users': define(Users, '#64748b'), // 用户管理 · 灰
  'admin-backup': define(Building2, '#64748b'), // 备份 · 灰
  'admin-data-cleanup': define(Paperclip, '#64748b'), // 数据清理 · 灰
  import: define(Upload, '#0d9488'), // 导入 · 青绿
}

/** 银行 / 资产类账户卡片的图标 —— 供账户页按 account_type 选用。 */
export const ACCOUNT_TYPE_ICONS: Record<string, LucideIcon> = {
  bank_card: Building2,
  credit_card: CreditCard,
  cash: CircleDollarSign,
  alipay: Wallet,
  wechat: Wallet,
  investment: Landmark,
}

/** 特殊流水图标(转账/借款四态/退款/利息)的 Lucide 候选 —— 与 flowIcons 的 Material 名并存,
 *  供设置页图标选择器展示「Lucide 风格」备选。key 与 FlowIconKey 对齐。 */
export const FLOW_LUCIDE_ICONS: Record<string, LucideIcon> = {
  deposit_interest: CircleDollarSign,
  transfer: ArrowLeftRight,
  loan_lend: HandCoins,
  loan_borrow: HandCoins,
  loan_collect: Receipt,
  loan_repay: ReceiptText,
  refund: ArrowLeftRight,
}

/** 取某 section 的图标元数据,未知 section 回落到交易图标。 */
export function sectionIcon(section: AppSection | string | null | undefined): SectionIconMeta {
  return SECTION_ICONS[section as AppSection] ?? SECTION_ICONS.transactions
}

/** 生成图标徽章的内联 style(渐变背景 + 品牌色文字)。 */
export function sectionBadgeStyle(section: AppSection | string | null | undefined): CSSProperties {
  const { color } = sectionIcon(section)
  return {
    background: `linear-gradient(135deg, ${color}26, ${color}0d)`,
    color,
    boxShadow: `inset 0 0 0 1px ${color}33`,
  }
}
