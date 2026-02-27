import type { ButtonHTMLAttributes, HTMLAttributes } from 'react'

import { cn } from '../lib/cn'

export function Tabs({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('space-y-4', className)} {...props} />
}

export function TabsList({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'inline-flex h-auto w-full flex-wrap items-center gap-2 rounded-md bg-muted p-1 text-muted-foreground',
        className
      )}
      {...props}
    />
  )
}

type TabsTriggerProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean
}

export function TabsTrigger({ className, active = false, ...props }: TabsTriggerProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center rounded-sm px-3 py-1.5 text-sm font-medium transition-colors',
        active ? 'bg-card text-card-foreground shadow-sm' : 'hover:bg-card/60',
        className
      )}
      {...props}
    />
  )
}

export function TabsContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mt-2', className)} {...props} />
}
