import type { HTMLAttributes } from 'react'
import { cn } from '../lib/cn'

/** 通用 Skeleton 块：接受任意 tailwind 宽高/圆角 class。 */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('animate-pulse rounded-md bg-muted/70', className)}
      {...props}
    />
  )
}

/**
 * 表格行级骨架屏：默认 6 列 5 行，模拟 data table 加载中的视觉骨架。
 */
export function TableSkeleton({ rows = 5, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="w-full">
      <div className="grid items-center gap-4 border-b border-border/60 bg-muted/20 px-4 py-3"
           style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className="h-3 w-16" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, r) => (
        <div
          key={r}
          className={cn(
            'grid items-center gap-4 border-b border-border/30 px-4 py-3',
            r % 2 === 0 ? 'bg-background' : 'bg-muted/10'
          )}
          style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={cn('h-4', c === 0 ? 'w-24' : 'w-16')} />
          ))}
        </div>
      ))}
    </div>
  )
}

/**
 * 空状态：大图标 + 标题 + 副标题 + 可选行动按钮。代替"暂无数据"纯文字。
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className
}: {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 px-6 py-12 text-center', className)}>
      {icon ? (
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-muted/40 text-muted-foreground">
          {icon}
        </div>
      ) : null}
      <div className="space-y-1">
        <div className="text-sm font-semibold text-foreground">{title}</div>
        {description ? (
          <div className="text-xs text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}
