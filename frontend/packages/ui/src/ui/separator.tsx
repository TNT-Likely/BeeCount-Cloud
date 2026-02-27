import type { HTMLAttributes } from 'react'

import { cn } from '../lib/cn'

export function Separator({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('h-px w-full bg-border', className)} {...props} />
}
