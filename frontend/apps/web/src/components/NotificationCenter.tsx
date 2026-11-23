import { Bell, CircleAlert, Inbox, RotateCw, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchNotifications, markNotificationsRead, type CloudNotification } from '@beecount/api-client'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
  DialogTrigger,
  Skeleton,
  useT,
  useToast,
} from '@beecount/ui'

import { useAuth } from '../context/AuthContext'
import { useSyncEvent, type SyncChangeBatchPayload } from '../context/SyncSocketContext'
import { NotificationCard, supportsNotification } from './NotificationCard'

type LoadStatus = 'loading' | 'ready' | 'error'

export function NotificationCenter() {
  const { token, sessionUserId } = useAuth()
  return <PersonalNotifications key={sessionUserId ?? token} />
}

function PersonalNotifications() {
  const { token, isAdmin, isAdminResolved } = useAuth()
  const t = useT()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<CloudNotification[]>([])
  const [status, setStatus] = useState<LoadStatus>('loading')
  const [saving, setSaving] = useState(false)
  const [showHint, setShowHint] = useState(false)
  const announced = useRef(new Set<string>())
  const hintTimer = useRef<number | undefined>(undefined)
  const latestRequest = useRef(0)
  const closeButton = useRef<HTMLButtonElement>(null)
  const unreadIds = items.filter((item) => item.unread).map((item) => item.id)
  const unreadCount = unreadIds.length

  const refresh = useCallback(async () => {
    const request = ++latestRequest.current
    try {
      const notices = await fetchNotifications(token)
      if (request !== latestRequest.current) return
      setItems(notices.filter(supportsNotification))
      setStatus('ready')
    } catch {
      if (request === latestRequest.current) setStatus('error')
    }
  }, [token])

  useEffect(() => {
    void refresh()
    const onFocus = () => { void refresh() }
    // Missed cron deadlines and sync failures may have no WebSocket event.
    const timer = window.setInterval(() => { if (!document.hidden) void refresh() }, 60_000)
    window.addEventListener('focus', onFocus)
    return () => {
      latestRequest.current++
      window.clearInterval(timer)
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
  useSyncEvent('backup_status', () => { void refresh() })
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
    closeButton.current?.focus()
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
      <Dialog open={open} modal={false} onOpenChange={(next) => {
        setOpen(next)
        if (next) {
          setShowHint(false)
          void refresh()
        }
      }}>
        <DialogTrigger asChild>
          <button
            type="button"
            title={t('notifications.title')}
            aria-label={unreadCount ? t('notifications.unreadLabel', { count: unreadCount }) : t('notifications.title')}
            className="relative flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-primary/15 hover:text-primary"
          >
            <Bell className="h-4 w-4" aria-hidden="true" />
            {unreadCount > 0 ? <span aria-hidden="true" className="absolute right-0 top-0 min-w-3.5 rounded-full bg-amber-700 px-0.5 text-center text-[9px] font-bold leading-[14px] text-white">{unreadCount}</span> : null}
          </button>
        </DialogTrigger>
        <DialogContent showCloseButton={false} aria-describedby={undefined}
          onOpenAutoFocus={(event) => { event.preventDefault(); closeButton.current?.focus() }}
          className="left-2 right-2 top-[4.25rem] z-[60] block max-h-[calc(100dvh-5rem)] w-auto max-w-none translate-x-0 translate-y-0 overflow-y-auto p-0 sm:left-auto sm:right-6 sm:w-96 md:top-[4.5rem]">
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <DialogTitle className="px-2">{t('notifications.title')}</DialogTitle>
            <div className="flex items-center gap-1">
              {items.length > 0 ? (
                <button type="button"
                  className="rounded-md px-2 py-1.5 text-xs text-foreground hover:bg-primary/10 disabled:opacity-50"
                  disabled={unreadCount === 0 || saving || status !== 'ready'}
                  onClick={() => { void markAllRead() }}
                >{t('notifications.markAllRead')}</button>
              ) : null}
              <DialogClose asChild>
                <button type="button" ref={closeButton} className="grid h-8 w-8 place-items-center rounded-md hover:bg-primary/10" aria-label={t('notifications.close')}>
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </DialogClose>
            </div>
          </div>
          <div className="space-y-2 border-t border-border p-3">
            <NotificationState status={status} hasItems={items.length > 0} onRetry={() => {
              closeButton.current?.focus()
              setStatus('loading')
              void refresh()
            }} />
            {items.map((item) => (
              <NotificationCard key={item.id} item={item} admin={isAdminResolved ? isAdmin : null} />
            ))}
          </div>
        </DialogContent>
      </Dialog>
      {!open && showHint && unreadCount > 0 ? (
        <div role="status" className="pointer-events-none absolute right-0 top-full z-50 mt-3 w-max max-w-[calc(100vw-2rem)] rounded-lg border border-amber-600/25 bg-amber-50 px-3 py-2 text-sm text-amber-950 shadow-soft dark:bg-amber-950 dark:text-amber-100">
          {t('notifications.arrival', { count: unreadCount })}
        </div>
      ) : null}
    </div>
  )
}

function NotificationState({ status, hasItems, onRetry }: {
  status: LoadStatus
  hasItems: boolean
  onRetry: () => void
}) {
  const t = useT()
  const retry = (
    <button type="button"
      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-amber-100 px-2.5 py-1.5 text-xs font-medium text-amber-950 hover:bg-amber-200 dark:bg-amber-900 dark:text-amber-100 dark:hover:bg-amber-800"
      onClick={onRetry}
    >
      <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
      {t('notifications.retry')}
    </button>
  )

  if (hasItems) {
    if (status === 'ready') return null
    return status === 'error' ? (
      <div className="flex items-center justify-between gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-900 dark:text-amber-200">
        <p role="status">{t('notifications.stale')}</p>
        {retry}
      </div>
    ) : <p role="status" className="px-1 py-1.5 text-xs text-muted-foreground">{t('notifications.loading')}</p>
  }

  if (status === 'loading') {
    return (
      <div role="status" className="min-h-40 px-0.5 py-2">
        <p className="mb-2.5 text-xs text-muted-foreground">{t('notifications.loading')}</p>
        <div aria-hidden="true" className="flex gap-2.5 rounded-xl border border-border p-3">
          <Skeleton className="h-8 w-8 shrink-0 rounded-lg motion-reduce:animate-none" />
          <div className="flex-1 space-y-2.5 pt-0.5">
            <Skeleton className="h-3 w-2/5 motion-reduce:animate-none" />
            <Skeleton className="h-2.5 w-3/4 motion-reduce:animate-none" />
            <Skeleton className="h-11 w-full rounded-lg motion-reduce:animate-none" />
          </div>
        </div>
      </div>
    )
  }

  const failed = status === 'error'
  const Icon = failed ? CircleAlert : Inbox
  return (
    <div className="flex min-h-44 flex-col items-center justify-center gap-1.5 px-3 py-4 text-center">
      <span className={`mb-1 grid h-11 w-11 place-items-center rounded-xl ${failed ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300' : 'bg-muted text-muted-foreground'}`}>
        <Icon className="h-5 w-5" aria-hidden="true" />
      </span>
      <div role="status">
        <h2 className="text-sm font-semibold">{t(failed ? 'notifications.loadFailed' : 'notifications.empty')}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{t(failed ? 'notifications.loadFailedHint' : 'notifications.emptyHint')}</p>
      </div>
      {failed ? <div className="mt-1.5">{retry}</div> : null}
    </div>
  )
}
