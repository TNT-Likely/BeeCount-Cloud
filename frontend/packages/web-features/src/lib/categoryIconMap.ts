/**
 * 分类图标名解析 & Material Symbols 字体子集清单。
 *
 * BeeCount 移动端分类表的 `icon` 字段存的是 Material Icons 名字(例如
 * `'restaurant'` / `'build'` / `'handyman'`)—— 见 `lib/services/data/seed_service.dart`
 * 的 `expenseIcons` / `incomeIcons` 字面量。Material Symbols 是 Material Icons
 * 的超集,绝大多数名字 1:1 通用,**web 端直接把 stored 值透传给字体** 就能
 * 渲染出正确图标。
 *
 * 真正需要"重映射"的只有 Flutter 侧 `getCategoryIcon` switch 里做的**内部
 * 重命名**(例如 `money → Icons.attach_money` / `boat → Icons.directions_boat`)。
 * 这些 stored 值本身不是合法 Material Symbols 名,必须转成 Flutter 实际渲
 * 染的 Icons 名再喂给字体。全部列在下面 {@link FLUTTER_RENAMES}。
 *
 * 安全网:stored 值既不在 {@link KNOWN_NAMES} 也不在重命名源 → 返回
 * `category` 兜底(跟 Flutter `getCategoryIcon` 的 default 分支一致)。
 *
 * 性能:字体通过 Google Fonts 的 `icon_names=` 参数子集化,只下载 {@link
 * MATERIAL_SYMBOLS_SUBSET} 里的 ~290 个字形(~45KB WOFF2),完整字体要 500KB+。
 * `apps/web/index.html` 的 `<link>` URL 必须跟本文件的 SUBSET 同步。
 *
 * 维护:Flutter 侧 `seed_service.dart` / `icon_picker_page.dart` / `category_service.dart`
 * 三处新增图标时,`scripts/gen-category-icons.mjs`(随后补)可一键重扫 + 输出本
 * 文件。手工维护也可以,但要保证 KNOWN_NAMES 涵盖所有可能出现的 stored 值。
 */

/**
 * Flutter 内部重命名(stored 值 → Flutter 实际渲染的 `Icons.xxx` 名)。
 * 来源:`lib/services/data/category_service.dart` 的 `getCategoryIcon` switch
 * 里返回值跟 case 名不一致的分支。
 */
const FLUTTER_RENAMES: Record<string, string> = {
  boat: 'directions_boat', // Icons.directions_boat
  compass: 'explore', // Icons.explore
  energy_savings_leaf: 'eco', // Icons.eco(Flutter 故意把"节能叶子"渲成"eco")
  euro: 'euro_symbol', // Icons.euro_symbol
  face_retouching: 'face', // Icons.face
  money: 'attach_money', // Icons.attach_money
  oil_barrel: 'propane_tank', // Icons.propane_tank(Flutter 故意替换)
  part_time: 'schedule', // Icons.schedule
  real_estate_agent: 'home_work', // Icons.home_work
  yen: 'currency_yen', // Icons.currency_yen
}

/**
 * 数据库里可能出现的所有合法 stored 值 —— 来自扫描:
 *   - `lib/services/data/seed_service.dart` 的 `expenseIcons` / `incomeIcons` 全部 value
 *   - `lib/pages/category/icon_picker_page.dart` 的 `_IconItem` 第一参数
 *   - `lib/services/data/category_service.dart` 的 `getCategoryIcon` switch 全部 case
 *
 * 这些值**除了 FLUTTER_RENAMES 里的 key** 之外,理论上都是合法 Material Symbols
 * 名 → 可以直接透传。不在集合里的 stored 值走 `category` 兜底。
 *
 * Material Symbols 新增图标时需要同步扩充(或者重跑扫描脚本)。
 */
const KNOWN_NAMES: ReadonlySet<string> = new Set([
  'access_time',
  'accessibility',
  'accessible',
  'account_balance',
  'account_balance_wallet',
  'add_shopping_cart',
  'agriculture',
  'air',
  'airport_shuttle',
  'analytics',
  'apartment',
  'apple',
  'attach_money',
  'attractions',
  'auto_awesome',
  'auto_stories',
  'autorenew',
  'back_hand',
  'bakery_dining',
  'balance',
  'bathtub',
  'beach_access',
  'bed',
  'biotech',
  'blender',
  'book',
  'bookmark',
  'breakfast_dining',
  'brush',
  'bubble_chart',
  'bug_report',
  'build',
  'business',
  'business_center',
  'cable',
  'cake',
  'calculate',
  'call_made',
  'call_received',
  'camera_alt',
  'candy',
  'car_rental',
  'card_giftcard',
  'casino',
  'category',
  'celebration',
  'chair',
  'checkroom',
  'child_care',
  'chocolate',
  'circle',
  'cleaning_services',
  'cloud',
  'code',
  'coffee',
  'compare_arrows',
  'computer',
  'confirmation_number',
  'construction',
  'content_cut',
  'cookie',
  'coronavirus',
  'create',
  'credit_card',
  'cruelty_free',
  'currency_exchange',
  'currency_yen',
  'delivery_dining',
  'description',
  'design_services',
  'desktop_windows',
  'developer_mode',
  'devices',
  'diamond',
  'dinner_dining',
  'directions_bike',
  'directions_boat',
  'directions_bus',
  'directions_car',
  'directions_railway',
  'directions_subway',
  'directions_walk',
  'dry_cleaning',
  'eco',
  'edit',
  'elderly',
  'electric_bolt',
  'electric_scooter',
  'electrical_services',
  'emoji_events',
  'emoji_nature',
  'engineering',
  'euro_symbol',
  'explore',
  'face',
  'face_retouching_natural',
  'factory',
  'family_restroom',
  'fastfood',
  'favorite',
  'fitness_center',
  'flash_on',
  'flight',
  'forest',
  'foundation',
  'free_breakfast',
  'games',
  'gavel',
  'gesture',
  'grade',
  'grain',
  'grass',
  'group',
  'handshake',
  'handyman',
  'headphones',
  'headset',
  'healing',
  'health_and_safety',
  'help',
  'hiking',
  'home',
  'home_repair_service',
  'home_work',
  'house',
  'hvac',
  'icecream',
  'input',
  'inventory',
  'inventory_2',
  'iron',
  'juice',
  'keyboard',
  'kitchen',
  'label',
  'language',
  'laptop',
  'library_books',
  'lightbulb',
  'liquor',
  'local_activity',
  'local_bar',
  'local_cafe',
  'local_car_wash',
  'local_dining',
  'local_florist',
  'local_gas_station',
  'local_grocery_store',
  'local_hospital',
  'local_laundry_service',
  'local_mall',
  'local_offer',
  'local_parking',
  'local_pharmacy',
  'local_pizza',
  'local_post_office',
  'local_shipping',
  'local_taxi',
  'location_on',
  'loyalty',
  'lunch_dining',
  'mail',
  'map',
  'medical_information',
  'medical_services',
  'medication',
  'menu_book',
  'mic',
  'military_tech',
  'model_training',
  'monetization_on',
  'money_off',
  'monitor_heart',
  'motorcycle',
  'mouse',
  'move_down',
  'movie',
  'music_note',
  'music_video',
  'new_releases',
  'nightlife',
  'paid',
  'palette',
  'park',
  'party_mode',
  'payment',
  'payments',
  'pedal_bike',
  'pet_supplies',
  'pets',
  'phone',
  'phone_android',
  'phone_iphone',
  'photo_camera',
  'piano',
  'pie_chart',
  'place',
  'play_circle',
  'plumbing',
  'pool',
  'price_change',
  'price_check',
  'print',
  'propane_tank',
  'psychology',
  'public',
  'published_with_changes',
  'quiz',
  'ramen_dining',
  'receipt',
  'receipt_long',
  'redeem',
  'refresh',
  'report_problem',
  'request_quote',
  'restaurant',
  'restaurant_menu',
  'ring_volume',
  'roofing',
  'router',
  'savings',
  'schedule',
  'school',
  'science',
  'security',
  'self_improvement',
  'sell',
  'set_meal',
  'shopping_bag',
  'shopping_basket',
  'shopping_cart',
  'show_chart',
  'shower',
  'smartphone',
  'smoking_rooms',
  'solar_power',
  'south',
  'spa',
  'sports',
  'sports_basketball',
  'sports_cricket',
  'sports_esports',
  'sports_martial_arts',
  'sports_soccer',
  'sports_tennis',
  'star',
  'store',
  'storefront',
  'subscriptions',
  'support_agent',
  'swap_horiz',
  'sync',
  'table_restaurant',
  'tablet',
  'theater_comedy',
  'toll',
  'traffic',
  'train',
  'translate',
  'trending_down',
  'trending_up',
  'undo',
  'update',
  'vaccines',
  'verified',
  'videocam',
  'volunteer_activism',
  'wallet',
  'watch',
  'watch_later',
  'water_drop',
  'weekend',
  'wifi',
  'wine_bar',
  'work',
  'work_outline',
  'workspace_premium',
  'yard',
])

/**
 * stored 值 → Material Symbols ligature 名。
 *
 * 逻辑:
 *   1. 空/null → `category`(正常不应命中:服务端 alembic 0002 迁移 + sync push
 *      handler 兜底已保证 DB 里 icon 字段永远非空)
 *   2. 在 {@link FLUTTER_RENAMES} 里 → 返回重命名 target
 *   3. 在 {@link KNOWN_NAMES} 里 → 直接透传
 *   4. 都不在 → `category` 兜底(避免字体渲出字面量文本)
 *
 * 注意:不再做 name-based 模糊匹配 —— 那套 40 条中文正则只存在于 Flutter
 * 移动端(lib/services/data/category_service.dart 的 getCategoryIconByName),
 * 服务端已经通过 migration + push handler 把该填的 icon 都填进 DB 了,web 这
 * 边保持纯透传就够。
 */
export function resolveMaterialIconName(stored: string | null | undefined): string {
  const s = (stored || '').trim()
  if (!s) return 'category'
  const target = FLUTTER_RENAMES[s] ?? s
  return KNOWN_NAMES.has(target) ? target : 'category'
}

/**
 * Google Fonts 子集下载清单 —— 包含所有可能被渲染的 Material Symbols 名字,
 * 字典序排好。= `KNOWN_NAMES` ∪ `FLUTTER_RENAMES` 的 target(大多已在 KNOWN
 * 里,去重)。
 */
export const MATERIAL_SYMBOLS_SUBSET: readonly string[] = Object.freeze(
  Array.from(new Set([...KNOWN_NAMES, ...Object.values(FLUTTER_RENAMES)])).sort()
)

/**
 * 生成 Google Fonts CSS URL(Material Symbols Outlined,子集化)。
 * axes:`opsz=24`、`wght=400`、`FILL=0`、`GRAD=0`。
 *
 * `display=block`:ligature 方案下,字体未加载完时备用字体会把 span 内文本
 * (如 "restaurant")当普通字渲出来。block 让它短暂不可见(~3s)再显示真图
 * 标,避免"分类名文字一闪而过"。
 */
export function buildMaterialSymbolsFontUrl(
  names: readonly string[] = MATERIAL_SYMBOLS_SUBSET
): string {
  const iconNames = [...names].sort().join(',')
  return (
    'https://fonts.googleapis.com/css2' +
    '?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0' +
    `&icon_names=${iconNames}` +
    '&display=block'
  )
}
