import { AlertTriangle, ArrowRight, Bell, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { fetchNotifications, markNotificationsRead, type CloudNotification } from '@beecount/api-client'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  useT,
  useToast,
} from '@beecount/ui'

import { useAuth } from '../context/AuthContext'
import { useSyncEvent, type SyncChangeBatchPayload } from '../context/SyncSocketContext'

export function NotificationCenter() {
  const { token, sessionUserId } = useAuth()
  return <PersonalNotifications key={sessionUserId ?? token} token={token} />
}

function PersonalNotifications({ token }: { token: string }) {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<CloudNotification[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [saving, setSaving] = useState(false)
  const [showHint, setShowHint] = useState(false)
  const announced = useRef(new Set<string>())
  const hintTimer = useRef<number | undefined>(undefined)
  const latestRequest = useRef(0)
  const unreadIds = items.filter((item) => item.unread).map((item) => item.id)
  const unreadCount = unreadIds.length

  const refresh = useCallback(async () => {
    const request = ++latestRequest.current
    try {
      const notices = await fetchNotifications(token)
      if (request !== latestRequest.current) return
      setItems(notices)
      setStatus('ready')
    } catch {
      if (request === latestRequest.current) setStatus('error')
    }
  }, [token])

  useEffect(() => {
    void refresh()
    const onFocus = () => { void refresh() }
    window.addEventListener('focus', onFocus)
    return () => {
      latestRequest.current++
      window.removeEventListener('focus', onFocus)
    }
  }, [refresh])

  useSyncEvent('sync_change', (event) => {
    if ('ledgerId' in event && event.ledgerId === '__user_global__') void refresh()
  })
  useSyncEvent('sync_change_batch', (event) => {
    if ((event as SyncChangeBatchPayload).changes.some((change) => change.entity_type === 'category')) {
      void refresh()
    }
  })
  useSyncEvent('backup_restore', () => { void refresh() })

  useEffect(() => {
    const fresh = items.filter((item) => item.unread && !announced.current.has(item.id))
    if (fresh.length === 0) return
    fresh.forEach((item) => announced.current.add(item.id))
    setShowHint(true)
    window.clearTimeout(hintTimer.current)
    hintTimer.current = window.setTimeout(() => setShowHint(false), 5000)
  }, [items])

  useEffect(() => () => window.clearTimeout(hintTimer.current), [])

  const markAllRead = async () => {
    setSaving(true)
    try {
      await markNotificationsRead(token, unreadIds)
      await refresh()
    } catch {
      toast.error(t('notifications.markFailed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="relative flex">
      <DropdownMenu open={open} modal={false} onOpenChange={(next) => {
        setOpen(next)
        if (next) {
          setShowHint(false)
          void refresh()
        }
      }}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title={t('notifications.title')}
            aria-label={unreadCount ? t('notifications.unreadLabel', { count: unreadCount }) : t('notifications.title')}
            className="relative flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-primary/15 hover:text-primary"
          >
            <Bell className="h-4 w-4" aria-hidden="true" />
            {unreadCount > 0 ? <span aria-hidden="true" className="absolute right-0 top-0 min-w-3.5 rounded-full bg-amber-700 px-0.5 text-center text-[9px] font-bold leading-[14px] text-white">{unreadCount}</span> : null}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" sideOffset={10} collisionPadding={8}
          className="z-[60] w-[min(24rem,calc(100vw-1rem))] max-h-[var(--radix-dropdown-menu-content-available-height)] overflow-y-auto rounded-xl bg-card p-0">
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <DropdownMenuLabel className="text-base font-semibold">{t('notifications.title')}</DropdownMenuLabel>
            <div className="flex items-center gap-1">
              <DropdownMenuItem
                className="text-xs text-primary"
                disabled={unreadCount === 0 || saving}
                onSelect={(event) => { event.preventDefault(); void markAllRead() }}
              >{t('notifications.markAllRead')}</DropdownMenuItem>
              <DropdownMenuItem aria-label={t('notifications.close')}><X className="h-4 w-4" aria-hidden="true" /></DropdownMenuItem>
            </div>
          </div>
          <DropdownMenuSeparator className="m-0" />
          <div className="space-y-2 p-3">
            {status === 'error' ? (
              <div className="space-y-2 px-2 py-5 text-sm text-muted-foreground">
                <p>{t('notifications.loadFailed')}</p>
                <DropdownMenuItem onSelect={(event) => { event.preventDefault(); setStatus('loading'); void refresh() }}>
                  {t('notifications.retry')}
                </DropdownMenuItem>
              </div>
            ) : status === 'loading' || items.length === 0 ? (
              <p role="status" className="px-2 py-6 text-center text-sm text-muted-foreground">
                {t(status === 'loading' ? 'notifications.loading' : 'notifications.empty')}
              </p>
            ) : items.map((item) => (
              <div key={item.id} className={`flex items-start gap-2.5 rounded-xl border border-border p-3 ${item.unread ? 'bg-accent/35' : ''}`}>
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                  <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold">{t('notifications.categoryTitle')}</h2>
                    {item.unread ? <span aria-label={t('notifications.unread')} className="h-2 w-2 rounded-full bg-amber-600" /> : null}
                  </div>
                  <p className="mb-2 mt-1 text-xs text-muted-foreground">{t('notifications.categorySummary', { count: item.count })}</p>
                  <ul className="divide-y divide-border rounded-lg border border-border bg-card">
                    {item.categories.map((category) => (
                      <li key={category.id} className="break-words px-2.5 py-2 text-xs">
                        <div className="font-medium">{category.name || t('notifications.unnamedCategory')}</div>
                        {category.parent_name ? <div className="mt-0.5 text-muted-foreground">{t('notifications.parentName', { name: category.parent_name })}</div> : null}
                      </li>
                    ))}
                  </ul>
                  {item.count > item.categories.length ? <p className="mt-1 text-xs text-muted-foreground">{t('notifications.moreCategories', { count: item.count - item.categories.length })}</p> : null}
                  <DropdownMenuItem className="mt-1 w-fit gap-1 px-1 text-xs font-medium text-primary" onSelect={() => navigate('/app/categories')}>
                    {t('notifications.openCategories')}<ArrowRight className="h-3 w-3" aria-hidden="true" />
                  </DropdownMenuItem>
                </div>
              </div>
            ))}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
      {!open && showHint && unreadCount > 0 ? (
        <div role="status" className="pointer-events-none absolute right-0 top-full z-50 mt-3 w-max max-w-[calc(100vw-2rem)] rounded-lg border border-amber-600/25 bg-amber-50 px-3 py-2 text-sm text-amber-950 shadow-soft dark:bg-amber-950 dark:text-amber-100">
          {t('notifications.arrival', { count: unreadCount })}
        </div>
      ) : null}
    </div>
  )
}
