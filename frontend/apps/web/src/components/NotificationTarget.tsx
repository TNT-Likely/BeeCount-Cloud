import { useEffect, useState, type ReactNode } from 'react'

import { ApiError } from '@beecount/api-client'
import { Button, useT } from '@beecount/ui'

type Status = 'loading' | 'missing' | 'error'

export function TargetStatus({ state, retry }: { state: Status; retry: () => void }) {
  const t = useT()
  return <div role="status" className="flex items-center justify-between gap-3 rounded-lg border p-3 text-sm text-muted-foreground">
    {t(`notifications.target.${state}`)}
    {state === 'error' && <Button size="sm" variant="outline" onClick={retry}>{t('notifications.retry')}</Button>}
  </div>
}

export function NotificationTarget<T>({ load, children }: {
  load: () => Promise<T>; children: (value: T, refresh: () => void) => ReactNode
}) {
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<{ state: Status } | { state: 'ready'; value: T }>({ state: 'loading' })
  useEffect(() => {
    let active = true
    setResult({ state: 'loading' })
    load().then(value => {
      if (active) setResult({ state: 'ready', value })
    }).catch(err => {
      if (active) setResult({ state: err instanceof ApiError && err.status === 404 ? 'missing' : 'error' })
    })
    return () => { active = false }
  }, [load, attempt])
  const refresh = () => setAttempt(value => value + 1)
  return result.state === 'ready' ? children(result.value, refresh) : <TargetStatus state={result.state} retry={refresh} />
}
