import { useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'

import type { AdminDevice } from '@beecount/api-client'

import { formatIsoDateTime } from '../format'

type OpsDevicesPanelProps = {
  rows: AdminDevice[]
  onReload: () => void
}

type DeviceRow = AdminDevice & {
  session_count: number
}

function _normalizeFingerprintPart(value: string | null): string {
  return (value || '').trim().toLowerCase() || '__empty__'
}

function _deviceFingerprint(row: AdminDevice): string {
  return [
    _normalizeFingerprintPart(row.user_id),
    _normalizeFingerprintPart(row.name),
    _normalizeFingerprintPart(row.platform),
    _normalizeFingerprintPart(row.device_model),
    _normalizeFingerprintPart(row.os_version),
    _normalizeFingerprintPart(row.app_version),
  ].join('|')
}

function _safeTimestamp(value: string): number {
  const ts = Date.parse(value)
  return Number.isFinite(ts) ? ts : 0
}

export function OpsDevicesPanel({ rows, onReload }: OpsDevicesPanelProps) {
  const t = useT()
  const [showAllSessions, setShowAllSessions] = useState(false)

  const dedupedRows = useMemo<DeviceRow[]>(() => {
    const grouped = new Map<string, AdminDevice[]>()
    for (const row of rows) {
      const key = _deviceFingerprint(row)
      const bucket = grouped.get(key)
      if (bucket) {
        bucket.push(row)
      } else {
        grouped.set(key, [row])
      }
    }

    const out: DeviceRow[] = []
    for (const bucket of grouped.values()) {
      bucket.sort((a, b) => _safeTimestamp(b.last_seen_at) - _safeTimestamp(a.last_seen_at))
      const primary = bucket[0]
      out.push({
        ...primary,
        session_count: bucket.length,
      })
    }
    out.sort((a, b) => _safeTimestamp(b.last_seen_at) - _safeTimestamp(a.last_seen_at))
    return out
  }, [rows])

  const visibleRows = useMemo<DeviceRow[]>(
    () => (showAllSessions ? rows.map((row) => ({ ...row, session_count: 1 })) : dedupedRows),
    [showAllSessions, rows, dedupedRows]
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{t('ops.devices.title')}</h2>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant={showAllSessions ? 'outline' : 'default'}
            onClick={() => setShowAllSessions(false)}
          >
            {t('ops.devices.view.deduped')}
          </Button>
          <Button
            size="sm"
            variant={showAllSessions ? 'default' : 'outline'}
            onClick={() => setShowAllSessions(true)}
          >
            {t('ops.devices.view.allSessions')}
          </Button>
          <Button size="sm" variant="outline" onClick={onReload}>
            {t('shell.refresh')}
          </Button>
        </div>
      </div>

      <Card className="bc-panel">
        <CardContent className="p-0">
          {visibleRows.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">{t('table.empty')}</div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="bc-table-head">{t('ops.devices.user')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.table.device')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.table.platform')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.model')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.os')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.lastSeen')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.createdAt')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.table.sessions')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.ip')}</TableHead>
                    <TableHead className="bc-table-head">{t('ops.devices.id')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleRows.map((row) => (
                    <TableRow key={row.id} className="odd:bg-muted/20">
                      <TableCell>{row.user_email}</TableCell>
                      <TableCell>
                        <div className="min-w-[160px]">
                          <p className="font-medium">{row.name || '-'}</p>
                          <p className="text-xs text-muted-foreground">{row.device_model || '-'}</p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex min-w-[140px] items-center gap-2">
                          <span>{row.platform || '-'}</span>
                          <span className="text-xs text-muted-foreground">{row.app_version || '-'}</span>
                        </div>
                      </TableCell>
                      <TableCell>{row.device_model || '-'}</TableCell>
                      <TableCell>{row.os_version || '-'}</TableCell>
                      <TableCell>{formatIsoDateTime(row.last_seen_at)}</TableCell>
                      <TableCell>{formatIsoDateTime(row.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Badge variant={row.is_online ? 'default' : 'secondary'}>
                            {row.is_online ? t('ops.devices.online') : t('ops.devices.offline')}
                          </Badge>
                          {!showAllSessions && row.session_count > 1 ? (
                            <Badge variant="secondary">
                              {t('ops.devices.sessionCount', { count: row.session_count })}
                            </Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>{row.last_ip || '-'}</TableCell>
                      <TableCell>
                        <span className="font-mono text-[11px]">{row.id}</span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
