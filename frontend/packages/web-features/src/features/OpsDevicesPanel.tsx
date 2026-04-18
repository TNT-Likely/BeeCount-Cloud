import { useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  CardContent,
  useT
} from '@beecount/ui'

import type { AdminDevice } from '@beecount/api-client'

import { formatIsoDateTime } from '../format'

// 根据 platform 选一个图标 + 语义色。
function deviceIcon(row: AdminDevice): { glyph: string; color: string } {
  const platform = (row.platform || '').toLowerCase()
  if (platform === 'web') return { glyph: '🌐', color: '#3b82f6' }
  if (platform === 'ios') return { glyph: '📱', color: '#8b5cf6' }
  if (platform === 'android') return { glyph: '🤖', color: '#22c55e' }
  if (platform === 'macos' || platform === 'darwin') return { glyph: '💻', color: '#64748b' }
  if (platform === 'windows') return { glyph: '🪟', color: '#06b6d4' }
  return { glyph: '📟', color: '#94a3b8' }
}

// 相对时间。用闭包形式接受 i18n t() 做 lookup,这样组件切语言时自动跟随。
type TFn = (key: string) => string
function makeTimeAgo(t: TFn) {
  return (iso: string): string => {
    const ts = Date.parse(iso)
    if (!Number.isFinite(ts)) return '-'
    const diffSec = (Date.now() - ts) / 1000
    if (diffSec < 60) return t('time.justNow')
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}${t('time.minutesAgo')}`
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}${t('time.hoursAgo')}`
    if (diffSec < 86400 * 30) return `${Math.floor(diffSec / 86400)}${t('time.daysAgo')}`
    if (diffSec < 86400 * 365) return `${Math.floor(diffSec / 86400 / 30)}${t('time.monthsAgo')}`
    return `${Math.floor(diffSec / 86400 / 365)}${t('time.yearsAgo')}`
  }
}

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
  const timeAgo = useMemo(() => makeTimeAgo(t), [t])
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
        <CardContent className="p-4">
          {visibleRows.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">{t('table.empty')}</div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {visibleRows.map((row) => {
                const { glyph, color } = deviceIcon(row)
                return (
                  <div
                    key={row.id}
                    className="relative overflow-hidden rounded-xl border border-border/60 bg-card p-4 transition-shadow hover:shadow-md"
                    style={{ borderLeftColor: color, borderLeftWidth: 3 }}
                  >
                    <div
                      className="pointer-events-none absolute -right-10 -top-10 h-24 w-24 rounded-full blur-3xl"
                      style={{ background: color, opacity: 0.1 }}
                      aria-hidden
                    />
                    <div className="relative space-y-3">
                      <div className="flex items-start gap-3">
                        <div
                          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xl"
                          style={{ background: `${color}20` }}
                        >
                          {glyph}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-semibold">
                            {row.name || (row.device_model || row.platform || t('ops.device.unknownName'))}
                          </div>
                          <div className="truncate text-[11px] text-muted-foreground">
                            {row.user_email || row.user_id}
                          </div>
                        </div>
                        <Badge
                          variant={row.is_online ? 'default' : 'secondary'}
                          className="shrink-0 text-[10px]"
                        >
                          {row.is_online ? t('ops.devices.online') : t('ops.devices.offline')}
                        </Badge>
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-[11px]">
                        <div className="rounded-md bg-muted/30 px-2 py-1">
                          <div className="text-muted-foreground">{t('ops.device.header.platform')}</div>
                          <div className="font-medium">{row.platform || '-'}</div>
                        </div>
                        <div className="rounded-md bg-muted/30 px-2 py-1">
                          <div className="text-muted-foreground">{t('ops.device.header.version')}</div>
                          <div className="font-medium">{row.app_version || '-'}</div>
                        </div>
                        <div className="rounded-md bg-muted/30 px-2 py-1">
                          <div className="text-muted-foreground">{t('ops.device.header.device')}</div>
                          <div className="truncate font-medium">{row.device_model || '-'}</div>
                        </div>
                        <div className="rounded-md bg-muted/30 px-2 py-1">
                          <div className="text-muted-foreground">{t('ops.device.header.os')}</div>
                          <div className="truncate font-medium">{row.os_version || '-'}</div>
                        </div>
                      </div>

                      <div className="space-y-1 text-[11px] text-muted-foreground">
                        <div className="flex items-center justify-between gap-2">
                          <span>{t('ops.device.header.lastSeen')}</span>
                          <span
                            className="font-medium text-foreground"
                            title={formatIsoDateTime(row.last_seen_at)}
                          >
                            {timeAgo(row.last_seen_at)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between gap-2">
                          <span>{t('ops.device.header.firstSeen')}</span>
                          <span title={formatIsoDateTime(row.created_at)}>
                            {timeAgo(row.created_at)}
                          </span>
                        </div>
                        {row.last_ip ? (
                          <div className="flex items-center justify-between gap-2">
                            <span>IP</span>
                            <span className="font-mono text-[10px]">{row.last_ip}</span>
                          </div>
                        ) : null}
                        {!showAllSessions && row.session_count > 1 ? (
                          <div className="flex items-center justify-between gap-2">
                            <span>{t('ops.device.header.sessions')}</span>
                            <Badge variant="secondary" className="text-[10px]">
                              {t('ops.devices.sessionCount', { count: row.session_count })}
                            </Badge>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
