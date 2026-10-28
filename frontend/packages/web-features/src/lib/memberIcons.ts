/**
 * 成员图标集 —— 账本内自定义成员(爸爸/妈妈/我)的头像图标。
 *
 * 为什么单独一份、不复用分类图标:
 * 成员是「人」,语义窄而固定(人物 / 家庭 / 宠物),给一份 14 个的小集合,
 * 网格里一眼能挑完;分类图标几百个,在这里只会让人挑花眼。
 *
 * 存的仍是 Material Symbols 的 ligature 名(跟分类图标同款机制),前端渲染
 * 直接用 `<span className="material-symbols-outlined">{name}</span>`。
 * 所以这里的每个名字**必须**同时出现在字体子集里 —— 见
 * `categoryIconMap.MATERIAL_SYMBOLS_SUBSET`(本模块的 MEMBER_ICONS 已被合并
 * 进子集),漏一个就会在页面上渲成字面量文字,由 memberIcons.test.ts 守住。
 */

/** 默认成员图标:中性人形,任何成员没挑图标时都用它。 */
export const MEMBER_ICON_DEFAULT = 'person'

/**
 * 可选成员图标(顺序 = 选择器里的展示顺序)。
 *
 * 全部是 Material Symbols Outlined 的合法 ligature 名:
 * person / person_2 / person_3 通用人形,man / woman / boy / girl 按性别年龄,
 * child_care / elderly / baby_changing_station 老少,groups / family_restroom
 * 多人,face / sentiment_satisfied 表情,pets 宠物。
 */
export const MEMBER_ICONS: readonly string[] = Object.freeze([
  'person',
  'person_2',
  'person_3',
  'man',
  'woman',
  'boy',
  'girl',
  'child_care',
  'baby_changing_station',
  'elderly',
  'groups',
  'family_restroom',
  'face',
  'sentiment_satisfied',
  'pets',
])

const MEMBER_ICON_SET: ReadonlySet<string> = new Set(MEMBER_ICONS)

/**
 * 成员图标名 → 可安全喂给字体的图标名。
 *
 * 空 / 空白 / 不在集合里的值一律回退 {@link MEMBER_ICON_DEFAULT} —— 老数据
 * (没有图标字段)或别端写入的陌生值都不会在页面上变成裸文本。
 */
export function resolveMemberIcon(icon: string | null | undefined): string {
  const name = (icon || '').trim()
  return MEMBER_ICON_SET.has(name) ? name : MEMBER_ICON_DEFAULT
}
