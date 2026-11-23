import { AlertTriangle, ArrowRight, HardDrive, RefreshCw, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { CloudNotification, NotificationKind } from '@beecount/api-client'
import { DialogClose, useT } from '@beecount/ui'
import { routePath } from '../state/router'

type Context = { t: ReturnType<typeof useT>; admin: boolean | null }
type Presentation = {
  icon: LucideIcon
  title: string
  summary: string
  details?: ReactNode
  action?: { label: string; to: string }
  hint?: string
}
type Presenters = { [K in NotificationKind]: (item: CloudNotification<K>, ctx: Context) => Presentation }

const presenters: Presenters = {
  category_missing_parent: (item, { t, admin }) => ({
    icon: AlertTriangle,
    title: t('notifications.categoryTitle'),
    summary: t('notifications.categorySummary', { count: item.count }),
    details: <>
      <ul className="mt-2 divide-y divide-border rounded-lg border border-border bg-card">
        {item.categories.map(category => (
          <li key={category.id} className="break-words px-2.5 py-2 text-xs">
            <div className="font-medium">{category.name || t('notifications.unnamedCategory')}</div>
            {category.parent_name && <div className="mt-0.5 text-muted-foreground">{t('notifications.parentName', { name: category.parent_name })}</div>}
          </li>
        ))}
      </ul>
      {item.count > item.categories.length && <p className="mt-1 text-xs text-muted-foreground">{t('notifications.moreCategories', { count: item.count - item.categories.length })}</p>}
    </>,
    action: admin ? { label: t('notifications.openDataCleanup'), to: routePath({ kind: 'app', ledgerId: '', section: 'admin-data-cleanup' }) } : undefined,
    hint: admin === false ? t('notifications.contactAdmin') : undefined,
  }),
  backup_schedule_issue: (item, { t, admin }) => {
    const path = routePath({ kind: 'app', ledgerId: '', section: 'admin-backup' })
    return {
      icon: HardDrive,
      title: t(`notifications.backup.${item.reason}`),
      summary: t(`notifications.backup.${item.reason}Summary`, {
        name: item.schedule_name, count: item.count, time: new Date(item.at).toLocaleString(),
      }),
      action: admin ? item.run_id === null
        ? { label: t('notifications.openSchedule'), to: `${path}?schedule=${item.schedule_id}` }
        : { label: t('notifications.openRun'), to: `${path}?run=${item.run_id}` } : undefined,
    }
  },
  device_sync_failure: (item, { t }) => ({
    icon: RefreshCw,
    title: t('notifications.sync.title'),
    summary: item.device_name,
    details: <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
      {item.failures.map(f => <li key={f.direction}>{t(`notifications.sync.${f.direction}Count`, { count: f.count })}</li>)}
    </ul>,
    action: {
      label: t('notifications.openDevice'),
      to: `${routePath({ kind: 'app', ledgerId: '', section: 'settings-devices' })}?device=${encodeURIComponent(item.device_id)}`,
    },
  }),
}

export function supportsNotification(item: CloudNotification): boolean {
  return Object.prototype.hasOwnProperty.call(presenters, item.type)
}

export function NotificationCard<K extends NotificationKind>({ item, admin }: {
  item: CloudNotification<K>; admin: boolean | null
}) {
  const t = useT()
  const { icon: Icon, title, summary, details, action, hint } = presenters[item.type](item, { t, admin })
  return (
    <article className={`flex items-start gap-2.5 rounded-xl border p-3 ${item.unread ? 'border-amber-500/25 bg-amber-500/5' : 'border-border'}`}>
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
        <Icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          {item.unread && <span aria-label={t('notifications.unread')} className="h-2 w-2 shrink-0 rounded-full bg-amber-600" />}
        </div>
        <p className="mt-1 break-words text-xs text-muted-foreground">{summary}</p>
        {details}
        {action && <DialogClose asChild>
          <Link to={action.to} className="mt-2 inline-flex items-center gap-1 rounded px-1 py-1 text-xs font-medium text-amber-800 hover:bg-primary/10 dark:text-amber-300">
            {action.label}<ArrowRight className="h-3 w-3" aria-hidden="true" />
          </Link>
        </DialogClose>}
        {hint && <p className="mt-2 text-xs text-muted-foreground">{hint}</p>}
      </div>
    </article>
  )
}
