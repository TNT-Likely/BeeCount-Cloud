import type { ReactNode } from 'react'

import type { AppSection } from '../nav'
import { SectionIconBadge } from './SectionIconBadge'

interface Props {
  /** 页面对应的 AppSection,决定图标与配色。 */
  section: AppSection | string
  title: string
  subtitle?: string
  /** 右侧操作区(按钮等)。 */
  actions?: ReactNode
  /** 覆盖默认图标徽章尺寸。 */
  badgeSize?: 'md' | 'sm'
  className?: string
}

/**
 * 功能模块页面统一标题头 —— 左侧「渐变图标徽章 + 标题 + 副标题」,
 * 右侧操作按钮。所有业务页共用,保证一致的视觉语言。
 *
 * 替代此前各页手写的 `<h1>`(有的带 lucide 图标、有的不带、样式各异)。
 */
export function SectionPageHeader({
  section,
  title,
  subtitle,
  actions,
  badgeSize = 'md',
  className = '',
}: Props) {
  return (
    <div
      className={`flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between ${className}`}
    >
      <div className="flex min-w-0 items-center gap-3">
        <SectionIconBadge section={section} size={badgeSize} />
        <div className="min-w-0">
          <h1 className="text-xl font-semibold leading-tight">{title}</h1>
          {subtitle ? (
            <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}
