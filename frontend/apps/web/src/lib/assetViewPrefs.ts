/**
 * 资产页「走势 / 构成」切换的设备级持久化 —— 单一来源。
 *
 * 两处共享同一份持久化状态:NetWorthOrCompositionCard(非折算态那张独立卡)
 * 与 AccountsPage 折算卡内的 tab。各自的默认值不同(前者默认 'trend',后者
 * 默认 'composition'),是有意的,故默认值留在各自调用处,这里只统一 key 与类型。
 */
export const ASSET_VIEW_KEY = 'beecount:web:accounts:trendOrComposition'

export type AssetView = 'trend' | 'composition'
