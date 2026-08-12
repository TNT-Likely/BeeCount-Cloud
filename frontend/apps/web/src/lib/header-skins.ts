/**
 * 头部皮肤目录 —— 与 mobile 的 `lib/styles/header_skins.dart` 中 `kHeaderSkins`
 * 一一对齐。web 自身不渲染皮肤,这份目录只用来：
 *   1. 渲染设置页的皮肤下拉(分组 + 动态/自带配色标记);
 *   2. 选中「自带配色」的皮肤时，把主题色一并写回 server。
 *
 * **第 2 点是必须的。** 这些皮肤在 mobile 上会强制把主题色锁成自己的配色，
 * 而 theme_primary_color 和 appearance 是 server 上两个独立字段、两次独立
 * 广播。web 只改 appearance 的话，server 上的颜色会停在旧值，对端 app 收到
 * 两次广播时颜色先跳旧色再跳回绑定色 —— 表现为主题色在两个颜色之间闪。
 * 所以 web 改皮肤时也要按同样的顺序：先 theme_primary_color，后 appearance。
 *
 * 新增皮肤时 mobile 与这里要同步改，否则 app 选的皮肤在 web 下拉里显示为空。
 */

export type HeaderSkinGroup = 'anniversary' | 'classic'

export interface HeaderSkinMeta {
  /** 与 mobile `HeaderSkin.id` 完全一致，也是 appearance.header_skin 的存值。 */
  id: string
  group: HeaderSkinGroup
  /** 有动效（mobile 上是 CustomPainter 动画）。 */
  animated?: boolean
  /** 自带配色：选中后主题色被锁成这个值，hex 大写含 #。 */
  boundPrimary?: string
}

/** 顺序 = 下拉里的展示顺序，与 mobile 皮肤页保持一致（周年置顶）。 */
export const HEADER_SKINS: HeaderSkinMeta[] = [
  // 一周年纪念款(2025.9.10—2026.9.10)
  { id: 'anniversary', group: 'anniversary', animated: true },
  { id: 'anniv_cake', group: 'anniversary', animated: true, boundPrimary: '#FF7A45' },
  // 渐变
  { id: 'aurora', group: 'classic' },
  { id: 'mountains', group: 'classic' },
  { id: 'bokeh', group: 'classic' },
  { id: 'waves', group: 'classic' },
  { id: 'silk', group: 'classic' },
  { id: 'bubbles', group: 'classic' },
  // 场景
  { id: 'sunset', group: 'classic' },
  { id: 'clouds', group: 'classic' },
  { id: 'skyline', group: 'classic' },
  { id: 'galaxy', group: 'classic' },
  // 图案
  { id: 'honeycomb', group: 'classic' },
  { id: 'starry', group: 'classic' },
  { id: 'stripes', group: 'classic' },
  { id: 'sakura', group: 'classic' },
  { id: 'meteor', group: 'classic' },
  { id: 'memphis', group: 'classic' },
  // 几何 / 艺术
  { id: 'lowpoly', group: 'classic' },
  { id: 'prism', group: 'classic' },
  { id: 'terrazzo', group: 'classic' },
]

export const HEADER_SKIN_GROUP_ORDER: HeaderSkinGroup[] = [
  'anniversary',
  'classic',
]

const BY_ID = new Map(HEADER_SKINS.map((s) => [s.id, s]))

export function headerSkinById(id: string): HeaderSkinMeta | undefined {
  return BY_ID.get(id)
}

/** 该皮肤自带的主题色；跟随主题色的皮肤返回 undefined。 */
export function boundPrimaryOf(id: string): string | undefined {
  return BY_ID.get(id)?.boundPrimary
}

/** i18n key，沿用既有的 `profile.sync.headerSkin.<id>` 命名。 */
export function headerSkinLabelKey(id: string): string {
  return `profile.sync.headerSkin.${id}`
}

export function headerSkinGroupLabelKey(group: HeaderSkinGroup): string {
  return `profile.sync.headerSkin.group.${group}`
}
