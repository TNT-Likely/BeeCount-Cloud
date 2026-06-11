/**
 * 资产页「走势 / 构成」切换的设备级持久化 —— 单一来源。
 *
 * 当前唯一消费者是 AccountsPage 资产汇总卡内的 tab(默认 'composition')。
 * key/类型抽到这里集中管理,默认值留在调用处。
 */
export const ASSET_VIEW_KEY = 'beecount:web:accounts:trendOrComposition'

export type AssetView = 'trend' | 'composition'
