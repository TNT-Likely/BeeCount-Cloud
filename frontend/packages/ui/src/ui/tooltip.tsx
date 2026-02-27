import type { PropsWithChildren } from 'react'

type TooltipProps = PropsWithChildren<{
  content: string
}>

export function Tooltip({ content, children }: TooltipProps) {
  return (
    <span title={content} className="inline-flex">
      {children}
    </span>
  )
}
