import { useCallback } from 'react'

import { fetchDeviceSyncHealth } from '@beecount/api-client'
import { Button, Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { NotificationTarget } from '../../components/NotificationTarget'
import { useToken } from '../../context/AuthContext'
import { formatIsoDateTimeLocal } from '../../i18n/format'

export function DeviceSyncDetails({ deviceId, onBack }: { deviceId: string; onBack: () => void }) {
  const token = useToken()
  const t = useT()
  const load = useCallback(() => fetchDeviceSyncHealth(token, deviceId), [token, deviceId])
  return <Card className="bc-panel">
    <CardHeader className="flex-row items-center justify-between gap-2">
      <CardTitle>{t('notifications.sync.detail')}</CardTitle>
      <Button size="sm" variant="ghost" onClick={onBack}>{t('notifications.allDevices')}</Button>
    </CardHeader>
    <CardContent>
      <NotificationTarget load={load}>{(data, refresh) => <div className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="break-words text-sm font-semibold">{data.device_name}</h2>
          <Button size="sm" variant="outline" onClick={refresh}>{t('notifications.refresh')}</Button>
        </div>
        {data.failures.length === 0 && <p role="status" className="rounded-lg border px-3 py-4 text-sm">{t('notifications.sync.recovered')}</p>}
        {data.failures.map(f => <section key={f.direction} className="space-y-2 rounded-lg border p-3">
          <h3 className="text-sm font-medium">{t(`notifications.sync.${f.direction}Count`, { count: f.count })}</h3>
          <p className="text-sm">{t(`notifications.sync.${f.reason}`)}</p>
          <dl className="grid gap-1 text-xs text-muted-foreground">
            <div className="flex flex-wrap gap-x-2"><dt>{t('notifications.sync.first')}</dt><dd>{formatIsoDateTimeLocal(f.first_failed_at)}</dd></div>
            <div className="flex flex-wrap gap-x-2"><dt>{t('notifications.sync.last')}</dt><dd>{formatIsoDateTimeLocal(f.last_failed_at)}</dd></div>
          </dl>
        </section>)}
      </div>}</NotificationTarget>
    </CardContent>
  </Card>
}
