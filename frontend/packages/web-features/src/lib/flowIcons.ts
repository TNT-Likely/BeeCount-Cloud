/**
 * 特殊流水「功能图标」—— 转账 / 借款四态 / 退款 / 存款利息的专属图标偏好。
 *
 * 为什么独立于分类
 * ================
 * 这些流水没有用户可编辑的分类:转账没有分类,借款流水 / 存款利息由后端引擎
 * 按固定分类名(借出/借入/收回借款/偿还借款/存款利息)自动生成,退款则挂在
 * 原单分类上。它们的图标此前在 TransactionRow 里硬编码 lucide 组件
 * (Undo2 / ArrowLeftRight / HandCoins),用户不能改、也不跟随分类图标体系
 * (Material Symbols)。本模块把「用哪个图标 + 什么配色」收敛成一份可持久化
 * 的用户偏好(`appearance.flow_icons`),同时给所有渲染点一个统一入口。
 *
 * 存储形态
 * ========
 * 偏好存在 user profile 的 `appearance_json` 里(跟 header_skin /
 * compact_amount 同级),key 为 `flow_icons`,值为 `{ [flowKey: iconKey] }`。
 * iconKey 用的是**分类图标同一套 stored 值**(Material Symbols 名,如
 * `swap_horiz` / `undo`),这样:
 *   - 偏好选择 UI 可以直接复用分类图标选择器的图标网格;
 *   - 渲染端统一走 CategoryIcon(material),不再需要 lucide 专用分支。
 * 未设置的 key 回落到 DEFAULT_FLOW_ICONS,保证老用户零配置也有图标。
 *
 * 调用方式
 * ========
 * 渲染点(交易行 / 借款时间轴 / 往来时间轴等)各自从 `useAuth().profileMe`
 * 读 `appearance.flow_icons` 传进来即可 —— 本模块保持无 React 依赖,纯函数
 * 方便在 web-features / apps 两侧复用与单测。web-features 的
 * `TransactionRow` 等组件通过新增的 `flowIconOverrides` prop 接收。
 */

/** 七类可自定义图标的特殊流水。 */
export type FlowIconKey =
  | 'deposit_interest' // 存款利息(income,计入统计)
  | 'transfer' // 转账(含存取款的划转)
  | 'loan_lend' // 借出
  | 'loan_borrow' // 借入
  | 'loan_collect' // 收回借款
  | 'loan_repay' // 偿还借款
  | 'refund' // 退款

/**
 * 默认 Material Symbols 图标名(均来自 categoryIconGroups 的图标集,
 * 保证 resolveMaterialIconName 一定认得,不会渲染成字面文字)。
 */
export const DEFAULT_FLOW_ICONS: Record<FlowIconKey, string> = {
  deposit_interest: 'savings',
  transfer: 'swap_horiz',
  loan_lend: 'trending_up',
  loan_borrow: 'trending_down',
  loan_collect: 'download',
  loan_repay: 'upload',
  refund: 'undo',
}

/** 默认底色(浅色 15% 透明 + 深色文字),与旧硬编码 lucide 分支的配色一致。 */
export const DEFAULT_FLOW_ICON_COLORS: Record<FlowIconKey, string> = {
  deposit_interest: '#10b981',
  transfer: '#0ea5e9',
  loan_lend: '#14b8a6',
  loan_borrow: '#f97316',
  loan_collect: '#14b8a6',
  loan_repay: '#f59e0b',
  refund: '#a855f7',
}

/** 七类 key 的有序列表(设置页逐行渲染用)。 */
export const FLOW_ICON_KEYS: readonly FlowIconKey[] = [
  'deposit_interest',
  'transfer',
  'loan_lend',
  'loan_borrow',
  'loan_collect',
  'loan_repay',
  'refund',
]

/** 用户存在 appearance.flow_icons 里的原始形态(未知 key / 空值都合法入参)。 */
export type FlowIconPrefs = Record<string, string | null | undefined> | null | undefined

/** 用户存在 appearance.flow_icon_colors 里的原始形态。 */
export type FlowIconColorPrefs = Record<string, string | null | undefined> | null | undefined

/**
 * 归一化用户图标偏好:只认白名单 key,值 trim 后非空才收;其余一律丢弃。
 * 脏数据(远端别的客户端写入的奇怪 key)不会污染渲染。
 */
export function normalizeFlowIconPrefs(prefs: FlowIconPrefs): Record<FlowIconKey, string> {
  const out: Record<FlowIconKey, string> = { ...DEFAULT_FLOW_ICONS }
  if (!prefs || typeof prefs !== 'object') return out
  for (const key of FLOW_ICON_KEYS) {
    const raw = (prefs as Record<string, unknown>)[key]
    if (typeof raw === 'string' && raw.trim()) {
      out[key] = raw.trim()
    }
  }
  return out
}

/** 归一化用户颜色偏好:只认白名单 key,且值必须是合法 3/6 位十六进制色值。 */
export function normalizeFlowIconColors(
  prefs: FlowIconColorPrefs,
): Partial<Record<FlowIconKey, string>> {
  if (!prefs || typeof prefs !== 'object') return {}
  const out: Partial<Record<FlowIconKey, string>> = {}
  for (const key of FLOW_ICON_KEYS) {
    const raw = (prefs as Record<string, unknown>)[key]
    if (typeof raw === 'string' && /^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(raw.trim())) {
      out[key] = raw.trim()
    }
  }
  return out
}

/**
 * 取某一类特殊流水最终应渲染的 Material Symbols 图标名。
 *
 * @param prefs appearance.flow_icons 原值(可以是整个 appearance dict,
 *              也可以直接是 flow_icons 子 dict——调用方传哪个都行,内部只挑 key)
 */
export function resolveFlowIcon(key: FlowIconKey, prefs: FlowIconPrefs): string {
  return normalizeFlowIconPrefs(prefs)[key]
}

/**
 * 取某一类特殊流水最终应渲染的底色(十六进制色值)。
 * 优先用户偏好(flow_icon_colors),否则回落到内置默认色。
 */
export function resolveFlowIconColor(
  key: FlowIconKey,
  colorPrefs: FlowIconColorPrefs,
): string {
  return normalizeFlowIconColors(colorPrefs)[key] ?? DEFAULT_FLOW_ICON_COLORS[key]
}

/** 取默认图标(偏好未设置时的值,设置页「恢复默认」按钮用)。 */
export function defaultFlowIcon(key: FlowIconKey): string {
  return DEFAULT_FLOW_ICONS[key]
}

/** 默认底色(设置页图标色块 / 渲染兜底用)。 */
export function defaultFlowIconColor(key: FlowIconKey): string {
  return DEFAULT_FLOW_ICON_COLORS[key]
}

/**
 * 从「一行流水」的字段推导它属于哪类特殊流水;普通收支返回 null。
 *
 * 判定口径与后端分类名生成规则严格对齐:
 *   - 借款流水(contacts_loans._build_loan_flow_tx):分类名 借出/借入/
 *     收回借款/偿还借款,且 exclude_from_stats=true
 *   - 存款利息(deposits._create_interest_income):分类名 存款利息 /
 *     存款利息(转存),计入统计(不排除)
 *   - 转账:tx_type=transfer(无分类)
 *   - 退款:tx_type=refund(优先于分类判定)
 */
export function flowIconKeyForTx(tx: {
  tx_type?: string | null
  category_name?: string | null
  exclude_from_stats?: boolean | null
}): FlowIconKey | null {
  if (tx?.tx_type === 'refund') return 'refund'
  if (tx?.tx_type === 'transfer') return 'transfer'
  const name = (tx?.category_name || '').trim()
  if (!name) return null
  if (name === '借出') return 'loan_lend'
  if (name === '借入') return 'loan_borrow'
  if (name === '收回借款') return 'loan_collect'
  if (name === '偿还借款') return 'loan_repay'
  if (name === '存款利息' || name === '存款利息(转存)' || name === '借款利息') return 'deposit_interest'
  return null
}

/**
 * 借款/往来页面的「本金流水 / 还款流水」图标归类 —— 与 flowIconKeyForTx 同源,
 * 供借款详情、按人时间轴等**结构化数据**(不是 ReadTransaction)直接调用。
 *
 * @param direction 借款方向('lend' 借出 / 'borrow' 借入)
 * @param kind      'principal' 本金流水 / 'repayment' 还款流水
 */
export function flowIconKeyForLoan(
  direction: 'lend' | 'borrow' | string | null | undefined,
  kind: 'principal' | 'repayment',
): FlowIconKey {
  const isLend = direction === 'lend'
  if (kind === 'principal') return isLend ? 'loan_lend' : 'loan_borrow'
  // 还款:借出方向是「收回借款」,借入方向是「偿还借款」
  return isLend ? 'loan_collect' : 'loan_repay'
}