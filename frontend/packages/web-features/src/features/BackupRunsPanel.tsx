import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  EmptyState,
  useT,
} from '@beecount/ui'

import type { BackupRun } from '@beecount/api-client'

type LiveProgress = {
  phase: string
  bytesTransferred?: number
  bytesTotal?: number
  speed?: number
  remoteName?: string
}

type Props = {
  runs: BackupRun[]
  liveProgress: LiveProgress | null
  onDownloadConfig: () => Promise<void>
  /** 点某个 run 的「准备恢复」按钮 — 触发 restore guide dialog。 */
  onRestoreRun?: (run: BackupRun) => void
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  succeeded: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  partial: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  failed: 'bg-red-500/15 text-red-600 dark:text-red-400',
  canceled: 'bg-muted text-muted-foreground',
}

function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 2)} ${units[i]}`
}

function fmtDuration(start: string, end: string | null): string {
  if (!end) return '-'
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  const sec = Math.max(0, Math.round((e - s) / 1000))
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
}

export function BackupRunsPanel({
  runs,
  liveProgress,
  onDownloadConfig,
  onRestoreRun,
}: Props) {
  const t = useT()
  const [detailRun, setDetailRun] = useState<BackupRun | null>(null)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('backup.runs.desc')}</p>
        <Button size="sm" variant="outline" onClick={() => void onDownloadConfig()}>
          {t('backup.runs.downloadConf')}
        </Button>
      </div>

      {liveProgress ? (
        <div className="rounded-md border border-blue-500/40 bg-blue-500/5 p-3">
          <div className="flex items-center justify-between text-xs font-medium">
            <span>
              {t('backup.runs.live')} ·{' '}
              <span className="font-mono">{liveProgress.phase}</span>
              {liveProgress.remoteName ? (
                <>
                  {' '}
                  → <span className="font-mono">{liveProgress.remoteName}</span>
                </>
              ) : null}
            </span>
            {liveProgress.bytesTotal ? (
              <span className="text-muted-foreground">
                {fmtBytes(liveProgress.bytesTransferred)} /{' '}
                {fmtBytes(liveProgress.bytesTotal)}
                {liveProgress.speed
                  ? ` · ${fmtBytes(liveProgress.speed)}/s`
                  : ''}
              </span>
            ) : null}
          </div>
          {liveProgress.bytesTotal ? (
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-blue-500/20">
              <div
                className="h-full bg-blue-500 transition-all"
                style={{
                  width: `${Math.min(
                    100,
                    ((liveProgress.bytesTransferred || 0) /
                      (liveProgress.bytesTotal || 1)) *
                      100,
                  )}%`,
                }}
              />
            </div>
          ) : null}
        </div>
      ) : null}

      {runs.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              <polyline points="21 4 21 12 13 12" />
            </svg>
          }
          title={t('backup.runs.empty.title')}
          description={t('backup.runs.empty.desc')}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-border/60">
          <table className="w-full text-xs">
            <thead className="bg-muted/40">
              <tr>
                <th className="px-3 py-2 text-left font-medium">
                  {t('backup.runs.col.startedAt')}
                </th>
                <th className="px-3 py-2 text-left font-medium">
                  {t('backup.runs.col.schedule')}
                </th>
                <th className="px-3 py-2 text-left font-medium">
                  {t('backup.runs.col.duration')}
                </th>
                <th className="px-3 py-2 text-right font-medium">
                  {t('backup.runs.col.size')}
                </th>
                <th className="px-3 py-2 text-left font-medium">
                  {t('backup.runs.col.status')}
                </th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {runs.map((r) => (
                <tr key={r.id} className="hover:bg-muted/30">
                  <td className="px-3 py-2 font-mono text-[11px]">
                    {new Date(r.started_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2">
                    {r.schedule_name || (
                      <span className="text-muted-foreground">
                        {t('backup.runs.adhoc')}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {fmtDuration(r.started_at, r.finished_at)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {fmtBytes(r.bytes_total)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        STATUS_COLORS[r.status] || ''
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setDetailRun(r)}
                      className="h-6 px-2 text-[11px]"
                    >
                      {t('backup.runs.button.detail')}
                    </Button>
                    {onRestoreRun && (r.status === 'succeeded' || r.status === 'partial') ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onRestoreRun(r)}
                        className="h-6 px-2 text-[11px]"
                      >
                        {t('backup.runs.button.restore')}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog
        open={detailRun !== null}
        onOpenChange={(v) => !v && setDetailRun(null)}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {t('backup.runs.detail.title')} #{detailRun?.id}
            </DialogTitle>
          </DialogHeader>
          {detailRun ? (
            <div className="-mx-6 max-h-[70vh] space-y-3 overflow-y-auto px-6 text-xs [scrollbar-gutter:stable]">
              <div className="grid grid-cols-2 gap-2 rounded-md border border-border/60 bg-muted/30 p-3">
                <div>
                  <div className="text-muted-foreground">
                    {t('backup.runs.col.startedAt')}
                  </div>
                  <div className="font-mono">
                    {new Date(detailRun.started_at).toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">
                    {t('backup.runs.col.duration')}
                  </div>
                  <div className="font-mono">
                    {fmtDuration(detailRun.started_at, detailRun.finished_at)}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">
                    {t('backup.runs.col.status')}
                  </div>
                  <div>
                    <span
                      className={`rounded px-1.5 py-0.5 ${
                        STATUS_COLORS[detailRun.status] || ''
                      }`}
                    >
                      {detailRun.status}
                    </span>
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">
                    {t('backup.runs.col.size')}
                  </div>
                  <div className="font-mono">
                    {fmtBytes(detailRun.bytes_total)}
                  </div>
                </div>
              </div>

              {detailRun.targets.length > 0 ? (
                <div className="space-y-1">
                  <div className="text-muted-foreground">
                    {t('backup.runs.detail.targets')}
                  </div>
                  <div className="space-y-1">
                    {detailRun.targets.map((tg) => (
                      <div
                        key={tg.id}
                        className="flex items-center justify-between rounded border border-border/60 px-2 py-1.5"
                      >
                        <div>
                          <span className="font-medium">
                            {tg.remote_name || `#${tg.remote_id}`}
                          </span>
                          {tg.error_message ? (
                            <span className="ml-2 text-red-500">
                              {tg.error_message}
                            </span>
                          ) : null}
                        </div>
                        <div className="flex items-center gap-2">
                          {tg.bytes_transferred ? (
                            <span className="font-mono text-muted-foreground">
                              {fmtBytes(tg.bytes_transferred)}
                            </span>
                          ) : null}
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] ${
                              STATUS_COLORS[tg.status] || ''
                            }`}
                          >
                            {tg.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {detailRun.error_message ? (
                <div className="rounded-md border border-red-500/40 bg-red-500/5 p-2 text-red-500">
                  {detailRun.error_message}
                </div>
              ) : null}

              {detailRun.log_text ? (
                <div className="space-y-1">
                  <div className="text-muted-foreground">
                    {t('backup.runs.detail.log')}
                  </div>
                  <pre className="max-h-64 overflow-auto rounded bg-muted/40 p-2 font-mono text-[10px]">
                    {detailRun.log_text}
                  </pre>
                </div>
              ) : null}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
