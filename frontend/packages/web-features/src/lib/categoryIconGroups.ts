/**
 * 分类图标分组 —— 跟 app 端 `lib/pages/category/icon_picker_page.dart` 对齐。
 *
 * 每个 item 的 `key` 是 **stored 值**(app DB 的 `categories.icon` 字段),
 * web 渲染时用 `resolveMaterialIconName` 走 FLUTTER_RENAMES 映射拿到真正的
 * Material Symbols 名;app 端读这个 key 走 `category_service.getCategoryIcon`
 * switch 拿 IconData。两端 stored 值完全一致,跨端兼容。
 *
 * 维护:动这个文件之前先看 app 端 icon_picker_page.dart 是不是也改了,两边
 * 必须同步。新增图标项必须保证 key 在 categoryIconMap.ts 的 KNOWN_NAMES 或
 * FLUTTER_RENAMES 里有定义,否则 web 渲染会 fallback 到 'category' 字面图标。
 */

export type CategoryIconItem = {
  /** stored 值,跟 app categories.icon 字段对齐 */
  key: string
  /** 中文显示标签(picker grid 单元格底部的小字) */
  label: string
}

export type CategoryIconGroup = {
  /** i18n key,用 t() 翻译 group tab 的标题 */
  labelKey: string
  icons: CategoryIconItem[]
}

/** 支出类目分组(8 组,每组 8-10 个图标) */
export const EXPENSE_ICON_GROUPS: readonly CategoryIconGroup[] = [
  {
    labelKey: 'categories.iconGroup.dining',
    icons: [
      { key: 'restaurant', label: '餐厅' },
      { key: 'local_dining', label: '用餐' },
      { key: 'fastfood', label: '快餐' },
      { key: 'local_cafe', label: '咖啡' },
      { key: 'local_bar', label: '酒吧' },
      { key: 'cake', label: '蛋糕' },
      { key: 'local_pizza', label: '披萨' },
      { key: 'icecream', label: '冰淇淋' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.transport',
    icons: [
      { key: 'directions_car', label: '汽车' },
      { key: 'directions_bus', label: '公交' },
      { key: 'directions_subway', label: '地铁' },
      { key: 'local_taxi', label: '出租车' },
      { key: 'flight', label: '飞机' },
      { key: 'train', label: '火车' },
      { key: 'directions_bike', label: '自行车' },
      { key: 'directions_walk', label: '步行' },
      { key: 'local_gas_station', label: '加油' },
      { key: 'local_parking', label: '停车' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.shopping',
    icons: [
      { key: 'shopping_cart', label: '购物车' },
      { key: 'shopping_bag', label: '购物袋' },
      { key: 'store', label: '商店' },
      { key: 'local_mall', label: '商场' },
      { key: 'local_grocery_store', label: '超市' },
      { key: 'checkroom', label: '服装' },
      { key: 'watch', label: '手表' },
      { key: 'diamond', label: '珠宝' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.entertainment',
    icons: [
      { key: 'movie', label: '电影' },
      { key: 'music_note', label: '音乐' },
      { key: 'sports_esports', label: '游戏' },
      { key: 'sports_soccer', label: '足球' },
      { key: 'sports_basketball', label: '篮球' },
      { key: 'theater_comedy', label: '娱乐' },
      { key: 'camera_alt', label: '摄影' },
      { key: 'palette', label: '艺术' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.life',
    icons: [
      { key: 'home', label: '居家' },
      { key: 'local_laundry_service', label: '洗衣' },
      { key: 'cleaning_services', label: '清洁' },
      { key: 'plumbing', label: '维修' },
      { key: 'electrical_services', label: '电工' },
      { key: 'handyman', label: '维护' },
      { key: 'pets', label: '宠物' },
      { key: 'child_care', label: '母婴' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.health',
    icons: [
      { key: 'local_hospital', label: '医院' },
      { key: 'medical_services', label: '医疗' },
      { key: 'local_pharmacy', label: '药店' },
      { key: 'fitness_center', label: '健身' },
      { key: 'spa', label: '美容' },
      { key: 'psychology', label: '心理' },
      // app 用 'face' 但 FLUTTER_RENAMES 里 face_retouching → face,这里直接用
      // face 跟 app 行为对齐
      { key: 'face', label: '护肤' },
      { key: 'content_cut', label: '理发' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.education',
    icons: [
      { key: 'school', label: '学校' },
      { key: 'library_books', label: '书籍' },
      { key: 'computer', label: '电脑' },
      { key: 'phone', label: '通讯' },
      { key: 'language', label: '语言' },
      { key: 'science', label: '科学' },
      { key: 'calculate', label: '计算' },
      { key: 'brush', label: '绘画' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.other',
    icons: [
      { key: 'business', label: '商务' },
      { key: 'work', label: '工作' },
      { key: 'flash_on', label: '水电' },
      { key: 'wifi', label: '网络' },
      { key: 'phone_android', label: '手机' },
      { key: 'smoking_rooms', label: '烟酒' },
      { key: 'favorite', label: '捐赠' },
      { key: 'category', label: '其他' },
    ],
  },
] as const

/** 收入类目分组(4 组) */
export const INCOME_ICON_GROUPS: readonly CategoryIconGroup[] = [
  {
    labelKey: 'categories.iconGroup.workIncome',
    icons: [
      { key: 'work', label: '工资' },
      { key: 'business_center', label: '商务' },
      { key: 'engineering', label: '技术' },
      { key: 'design_services', label: '设计' },
      { key: 'agriculture', label: '农业' },
      { key: 'construction', label: '建筑' },
      { key: 'local_shipping', label: '物流' },
      { key: 'restaurant_menu', label: '餐饮' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.finance',
    icons: [
      { key: 'account_balance', label: '银行' },
      { key: 'savings', label: '储蓄' },
      { key: 'trending_up', label: '投资' },
      { key: 'paid', label: '利息' },
      { key: 'currency_exchange', label: '汇率' },
      { key: 'wallet', label: '钱包' },
      { key: 'credit_card', label: '信用卡' },
      { key: 'account_balance_wallet', label: '余额' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.reward',
    icons: [
      { key: 'card_giftcard', label: '红包' },
      { key: 'redeem', label: '奖金' },
      { key: 'emoji_events', label: '奖励' },
      { key: 'star', label: '评级' },
      { key: 'grade', label: '等级' },
      { key: 'loyalty', label: '积分' },
      { key: 'volunteer_activism', label: '礼金' },
      { key: 'celebration', label: '庆祝' },
    ],
  },
  {
    labelKey: 'categories.iconGroup.other',
    icons: [
      { key: 'receipt_long', label: '报销' },
      // app 用 'part_time' stored,FLUTTER_RENAMES 映射到 schedule。这里
      // 保留 'part_time' 以跟 app stored 值一致,渲染走 resolveMaterialIconName
      { key: 'part_time', label: '兼职' },
      { key: 'undo', label: '退款' },
      // 同理 app 用 'money' → attach_money
      { key: 'money', label: '现金' },
      { key: 'apartment', label: '租金' },
      { key: 'handshake', label: '合作' },
      { key: 'category', label: '其他' },
      { key: 'help', label: '未分类' },
    ],
  },
] as const

/** 拍平后的所有 stored 值集合,搜索时用。 */
export const ALL_GROUPED_ICON_KEYS: readonly string[] = Object.freeze(
  Array.from(
    new Set([
      ...EXPENSE_ICON_GROUPS.flatMap((g) => g.icons.map((i) => i.key)),
      ...INCOME_ICON_GROUPS.flatMap((g) => g.icons.map((i) => i.key)),
    ]),
  ),
)

/** 按 kind 取分组数据 */
export function getIconGroupsByKind(
  kind: 'expense' | 'income' | string,
): readonly CategoryIconGroup[] {
  return kind === 'income' ? INCOME_ICON_GROUPS : EXPENSE_ICON_GROUPS
}
