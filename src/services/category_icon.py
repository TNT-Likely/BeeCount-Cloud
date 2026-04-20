"""分类图标解析 —— Flutter `getCategoryIconByName` 的 Python 复刻。

背景(为什么存在)
================

BeeCount mobile 的分类图标渲染有两级 fallback(lib/widgets/category_icon.dart):
  1. `category.icon` 非空 → 用 icon 值查 switch 渲染
  2. `category.icon` 为空 → **按分类名字模糊匹配** (`getCategoryIconByName`)

这个 name-based 推导只在移动端的渲染层存在。Web 端不走 Flutter,看不到 icon 字段
时就只能显兜底图。为了让 web 不重复一套 40 条中文正则,我们在**服务端**/**数据
层**做一次性 backfill:

  - alembic 0020 backfill:扫 `read_category_projection` + `sync_changes` 里的
    ledger_snapshot,icon IS NULL/空 的分类按本模块规则算一个,写回两处
  - sync push handler 兜底(src/routers/sync.py):老 App(3.0 及之前)推上来的
    category change 若 payload 里 icon 空,在落 projection + 合 snapshot 前调本
    模块填值,防止 null 重新污染

规则来源
========

完整 1:1 对齐 `lib/services/data/category_service.dart:8-201` 的 `getCategoryIconByName`。
顺序**严格按原文**—— 先匹配的胜出,`居家/家/家居/物业/维修` 规则会把"家庭/家人"
之外的"家"抢掉,这是 Flutter 的既定行为,不要擅自重排。

Flutter 返回 `Icons.xxx_outlined`,web 用的 Material Symbols Outlined 字体本身就是
outlined 版本,所以这里去掉 `_outlined` 后缀,直接返回基础名。

维护
====

Flutter 侧更新 `getCategoryIconByName` 时必须同步本模块。理想状态是 Flutter 3.0.1
做 write-time migration、去掉 `getCategoryIconByName` 调用,届时本模块也可以退役。
"""

from __future__ import annotations

# Flutter `CategoryService.getCategoryIcon` switch 里返回值跟 case 名不一致的条目
# (lib/services/data/category_service.dart 的 case-return 不一致分支)。stored
# 值自身不是 Material Symbols 名,要转成 Flutter 渲染用的 Icons 名再给字体。
FLUTTER_RENAMES: dict[str, str] = {
    "boat": "directions_boat",
    "compass": "explore",
    "energy_savings_leaf": "eco",
    "euro": "euro_symbol",
    "face_retouching": "face",
    "money": "attach_money",
    "oil_barrel": "propane_tank",
    "part_time": "schedule",
    "real_estate_agent": "home_work",
    "yen": "currency_yen",
}


def resolve_icon_by_name(name: str | None) -> str:
    """按分类名字模糊匹配推导图标。Flutter `getCategoryIconByName` 的 1:1 移植。

    返回 Material Symbols ligature 名(已剥 `_outlined` 后缀)。空 name 或都不命中
    → 返回 `circle`(跟 Flutter 的 `Icons.circle_outlined` 默认分支一致)。
    """
    if not name:
        return "circle"
    n = name

    # 餐饮
    if any(kw in n for kw in ("餐", "饭", "吃", "外卖")):
        return "restaurant"

    # 交通出行
    if "打车" in n:
        return "local_taxi"
    if "地铁" in n:
        return "subway"
    if "公交" in n:
        return "directions_bus"
    if "高铁" in n or "火车" in n:
        return "train"
    if "飞机" in n:
        return "flight"
    if "交通" in n or "出行" in n:
        return "directions_transit"

    # 车辆(未被上面交通覆盖的"车/车辆/车贷/购车/爱车")
    if n == "车" or any(kw in n for kw in ("车辆", "车贷", "购车", "爱车")):
        return "directions_car"

    # 购物
    if any(kw in n for kw in ("购物", "百货", "网购", "淘宝", "京东")):
        return "shopping_bag"

    # 社交
    if any(kw in n for kw in ("社交", "聚会", "朋友", "聚餐")):
        return "groups"

    # 服饰
    if "服饰" in n or any(kw in n for kw in ("衣", "鞋", "裤", "帽")):
        return "checkroom"

    # 超市/食材
    if any(kw in n for kw in ("超市", "生鲜", "菜", "粮油", "蔬菜", "水果")):
        return "local_grocery_store"

    # 娱乐
    if any(kw in n for kw in ("娱乐", "游戏", "电影", "影院")):
        return "sports_esports"

    # 家庭(先于 居家 规则 —— 注意:原 Flutter 顺序就是这样,"家庭"命中后不再
    # 进后面的"居家/家/家居/物业/维修")
    if any(kw in n for kw in ("家庭", "家人", "家属")):
        return "family_restroom"

    # 居家 / 物业 / 维修 —— 会拦截"家"这一广泛字符,后面"住房"带"房"才漏到下面
    if any(kw in n for kw in ("居家", "家", "家居", "物业", "维修")):
        return "chair"

    # 美容美妆
    if any(kw in n for kw in ("美妆", "化妆", "护肤", "美容")):
        return "brush"

    # 通讯
    if any(kw in n for kw in ("通讯", "话费", "宽带", "流量")):
        return "network_cell"

    # 订阅
    if any(kw in n for kw in ("订阅", "会员", "流媒体")):
        return "subscriptions"

    # 礼物/红包
    if any(kw in n for kw in ("礼物", "红包", "礼金", "请客", "人情")):
        return "card_giftcard"

    # 水电燃气
    if any(kw in n for kw in ("水", "电", "煤", "燃气")):
        return "water_drop"

    # 房贷/贷款
    if any(kw in n for kw in ("房贷", "按揭", "贷款", "信用卡")):
        return "account_balance"

    # 住房(排在房贷后面,"房贷"被前面抢走)
    if any(kw in n for kw in ("住房", "房租", "房", "租")):
        return "home"

    # 工资/收入
    if any(kw in n for kw in ("工资", "收入", "奖金", "报销", "兼职", "转账")):
        return "attach_money"

    # 理财/退款 —— 注意:"退款"在这里被匹配到 savings(跟 Flutter 行为一致,尽管
    # 直觉上应该是 undo;Flutter 作者当年就这么写的,咱照搬)
    if any(kw in n for kw in ("理财", "利息", "基金", "股票", "退款")):
        return "savings"

    # 教育
    if any(kw in n for kw in ("教育", "学习", "培训", "书")):
        return "menu_book"

    # 医疗
    if any(kw in n for kw in ("医疗", "医院", "药", "体检")):
        return "medical_services"

    # 宠物
    if "宠物" in n or "猫" in n or "狗" in n:
        return "pets"

    # 运动
    if any(kw in n for kw in ("运动", "健身", "球", "跑步")):
        return "fitness_center"

    # 数码
    if any(kw in n for kw in ("数码", "电子", "手机", "电脑")):
        return "devices_other"

    # 旅行
    if any(kw in n for kw in ("旅行", "旅游", "出差", "机票")):
        return "card_travel"

    # 酒店
    if any(kw in n for kw in ("酒店", "住宿", "民宿")):
        return "hotel"

    # 烟酒茶
    if any(kw in n for kw in ("烟", "酒", "茶")):
        return "local_bar"

    # 母婴
    if any(kw in n for kw in ("母婴", "孩子", "奶粉")):
        return "child_friendly"

    # 停车
    if "停车" in n:
        return "local_parking"
    if "加油" in n:
        return "local_gas_station"

    # 保养/维修(注意:上面"居家/维修"规则会先抢"维修",到这里主要是"保养")
    if "保养" in n or "维修" in n:
        return "build"

    # 汽车(跟前面"车/车辆"规则互补,主要覆盖"汽车"这个三字词)
    if "汽车" in n or "车辆" in n or n == "车":
        return "directions_car"

    # 过路费
    if "过路费" in n or "过桥费" in n:
        return "alt_route"

    # 快递
    if "快递" in n or "邮寄" in n:
        return "local_shipping"

    # 税/社保/公积金/罚款
    if any(kw in n for kw in ("税", "社保", "公积金", "罚款")):
        return "receipt_long"

    # 捐赠
    if "捐赠" in n or "公益" in n:
        return "volunteer_activism"

    # 工作
    if any(kw in n for kw in ("工作", "办公", "出差", "职场", "会议")):
        return "work"

    return "circle"


def resolve_category_icon(
    stored_icon: str | None,
    category_name: str | None,
) -> str:
    """给定 DB 里存的 icon + 分类 name,返回最终要填/渲染的 icon 值。

    逻辑跟 Flutter `getCategoryIconData` 一致:
      1. stored_icon 非空 → 直接返回(Flutter 端会走 switch,我们不关心渲染,只
         关心"icon 字段是否已填" —— 既然非空就不碰)
      2. stored_icon 空 + name 非空 → byName 推导
      3. 都空 → `circle`(跟 Flutter 默认分支一致)
    """
    if stored_icon and stored_icon.strip():
        return stored_icon.strip()
    return resolve_icon_by_name(category_name)


def needs_backfill(stored_icon: str | None) -> bool:
    """判断某条 category 是否需要走 byName backfill —— 即 icon 字段**空**。

    注意:`'category'` 字符串**不视为**需要 backfill。那是 Flutter 侧 category_edit_page
    的初始默认值,表示"用户显式或默认选择了 category 图标",我们不强行覆盖。
    """
    return stored_icon is None or not stored_icon.strip()
