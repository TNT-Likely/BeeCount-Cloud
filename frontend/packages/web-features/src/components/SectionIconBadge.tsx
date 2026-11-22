import type { AppSection } from '../nav'
import { sectionBadgeStyle, sectionIcon } from '../lib/sectionIcons'

interface Props {
  /** 页面对应的 AppSection,决定图标与配色。 */
  section: AppSection | string
  /** 尺寸:'md' 页面标题(默认) / 'sm' 紧凑场景。 */
  size?: 'md' | 'sm'
  className?: string
}

/**
 * 页面标题的统一图标徽章 —— 渐变底 + 品牌色 Lucide 图标。
 *
 * 目的是让所有功能模块页面(分类/成员/标签/联系人/借款/定期/储蓄罐/
 * 报销/资产/物品…)的标题头部使用同一套视觉语言,替换此前"有的带图标、
 * 有的不带、带的样式还各不相同"的混乱状态。
 */
export function SectionIconBadge({ section, size = 'md', className = '' }: Props) {
  const { Icon } = sectionIcon(section)
  const box = size === 'md' ? 'h-11 w-11 rounded-xl' : 'h-8 w-8 rounded-lg'
  const iconSize = size === 'md' ? 'h-[22px] w-[22px]' : 'h-[18px] w-[18px]'
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center ${box} ${className}`}
      style={sectionBadgeStyle(section)}
      aria-hidden
    >
      <Icon className={iconSize} strokeWidth={2} />
    </span>
  )
}
