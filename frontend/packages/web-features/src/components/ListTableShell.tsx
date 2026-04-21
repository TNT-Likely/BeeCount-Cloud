import type { ReactNode } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@beecount/ui'

type ListTableShellProps = {
  title: string
  actions?: ReactNode
  children: ReactNode
}

export function ListTableShell({ title, actions, children }: ListTableShellProps) {
  return (
    <Card className="bc-panel overflow-hidden">
      {/* mobile: 标题独占一行,action 换行跟上,避免标题被挤成竖向一字排;
          sm+: 横排 + justify-between 回到原样式。 */}
      <CardHeader className="flex flex-col gap-3 border-b border-border/60 bg-muted/15 pb-4 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base font-semibold tracking-tight">{title}</CardTitle>
        {actions ? (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        ) : null}
      </CardHeader>
      <CardContent className="pt-4">
        <div className="overflow-hidden rounded-xl border border-border/70 bg-background">{children}</div>
      </CardContent>
    </Card>
  )
}
