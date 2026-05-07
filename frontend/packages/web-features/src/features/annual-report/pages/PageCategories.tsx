import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import { InsightLine } from '../widgets/InsightLine'
import { AnimatedBars } from '../widgets/AnimatedBar'
import { categoryInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 分类排行:Top 5 支出 + 副位 Top 5 收入(右侧)。
 */
export function PageCategories({ data }: { data: AnnualReportData }) {
  const t = useT()
  const insight = categoryInsight(data)

  // 蜂蜜橙 5 阶配色,用于 5 个 bar
  const palette = ['#F4A82B', '#E58E1A', '#D17612', '#B85F0A', '#8E4A05']

  const items = data.topExpenseCategories.map((c, i) => ({
    label: c.name,
    value: c.total,
    percent: c.percent,
    color: palette[i] || palette[palette.length - 1],
    emoji: emojiForCategory(c.name),
  }))

  const empty = items.length === 0

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={28} />
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-start justify-center px-8 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page5Title)}
        </h2>
        {empty ? (
          <p className="text-lg text-white/50">{t(TKEY.page5Empty)}</p>
        ) : (
          <div className="w-full">
            <AnimatedBars items={items} formatValue={(v) => Math.round(v).toLocaleString()} />
          </div>
        )}
        {!empty && (
          <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
            <InsightLine text={t(insight.textKey, insight.args)} delay={1.4} />
          </div>
        )}
      </div>
    </div>
  )
}

function emojiForCategory(name: string): string {
  const map: Record<string, string> = {
    餐饮: '🍔', 食物: '🍔', 早餐: '☕', 咖啡: '☕',
    交通: '🚗', '打车': '🚕', 公交: '🚌',
    购物: '🛍️', 服装: '👗', 电子: '💻',
    娱乐: '🎮', 电影: '🎬', 旅行: '✈️',
    住房: '🏠', '房租': '🏠', 水电: '💡',
    医疗: '🏥', 健康: '💊', 健身: '🏋️',
    教育: '📚', 学习: '📚',
    礼物: '🎁', 红包: '🧧',
    宠物: '🐾',
  }
  for (const k of Object.keys(map)) {
    if (name.includes(k)) return map[k]
  }
  return '💰'
}
