import { Badge, useT } from '@beecount/ui'

type StatusBadgeProps = {
  value: string
}

const variantMap: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  owner: 'default',
  editor: 'secondary',
  viewer: 'outline',
  active: 'default',
  left: 'outline'
}

export function StatusBadge({ value }: StatusBadgeProps) {
  const t = useT()
  const normalized =
    value === 'owner' || value === 'editor' || value === 'viewer'
      ? t(`enum.role.${value}`)
      : value === 'active' || value === 'left'
        ? t(`enum.member.${value}`)
        : value
  return <Badge variant={variantMap[value] || 'outline'}>{normalized}</Badge>
}
