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
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b border-border/60 bg-muted/15 pb-4">
        <CardTitle className="text-base font-semibold tracking-tight">{title}</CardTitle>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </CardHeader>
      <CardContent className="pt-4">
        <div className="overflow-hidden rounded-xl border border-border/70 bg-background">{children}</div>
      </CardContent>
    </Card>
  )
}
