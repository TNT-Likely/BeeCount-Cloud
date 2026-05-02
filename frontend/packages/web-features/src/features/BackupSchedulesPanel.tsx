import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  useT,
} from '@beecount/ui'

import type {
  BackupRemote,
  BackupSchedule,
  BackupSchedulePayload,
} from '@beecount/api-client'

import { ConfirmDialog } from '../components/ConfirmDialog'

const CRON_PRESETS: Array<{ label: string; value: string }> = [
  { label: '每天 04:00', value: '0 4 * * *' },
  { label: '每天 02:00', value: '0 2 * * *' },
  { label: '每 6 小时', value: '0 */6 * * *' },
  { label: '每周日 04:00', value: '0 4 * * 0' },
  { label: '每月 1 号 04:00', value: '0 4 1 * *' },
]

type Props = {
  schedules: BackupSchedule[]
  remotes: BackupRemote[]
  onCreate: (payload: BackupSchedulePayload) => Promise<boolean>
  onUpdate: (id: number, payload: Partial<BackupSchedulePayload>) => Promise<boolean>
  onDelete: (id: number) => Promise<void>
  onRunNow: (id: number) => Promise<void>
}

export function BackupSchedulesPanel({
  schedules,
  remotes,
  onCreate,
  onUpdate,
  onDelete,
  onRunNow,
}: Props) {
  const t = useT()
  const [editing, setEditing] = useState<BackupSchedule | null | 'new'>(null)
  const [pendingDelete, setPendingDelete] = useState<BackupSchedule | null>(null)
  const [deleting, setDeleting] = useState(false)

  const remoteById = new Map(remotes.map((r) => [r.id, r] as const))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {t('backup.schedule.desc')}
        </p>
        <Button
          size="sm"
          disabled={remotes.length === 0}
          onClick={() => setEditing('new')}
        >
          {t('backup.schedule.button.create')}
        </Button>
      </div>

      {schedules.length === 0 ? (
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
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
          }
          title={t('backup.schedule.empty.title')}
          description={
            remotes.length === 0
              ? t('backup.schedule.empty.needsRemote')
              : t('backup.schedule.empty.desc')
          }
        />
      ) : (
        <div className="space-y-2">
          {schedules.map((s) => (
            <div
              key={s.id}
              className="rounded-md border border-border/60 bg-card p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{s.name}</span>
                    {s.enabled ? (
                      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                        {t('backup.schedule.enabled')}
                      </span>
                    ) : (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {t('backup.schedule.disabled')}
                      </span>
                    )}
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono">
                      {s.cron_expr}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                    {s.remote_ids.map((rid) => {
                      const r = remoteById.get(rid)
                      return (
                        <span
                          key={rid}
                          className="rounded bg-primary/10 px-1.5 py-0.5 text-primary"
                        >
                          {r?.name || `#${rid}`}
                        </span>
                      )
                    })}
                    <span>·</span>
                    <span>
                      {t('backup.schedule.retentionLabel', {
                        days: s.retention_days,
                      })}
                    </span>
                    {s.next_run_at ? (
                      <>
                        <span>·</span>
                        <span>
                          {t('backup.schedule.nextRun')}:{' '}
                          {new Date(s.next_run_at).toLocaleString()}
                        </span>
                      </>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => void onRunNow(s.id)}
                    className="h-7 px-2 text-xs"
                  >
                    {t('backup.schedule.button.runNow')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEditing(s)}
                    className="h-7 px-2 text-xs"
                  >
                    {t('common.edit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setPendingDelete(s)}
                    className="h-7 px-2 text-xs text-red-500"
                  >
                    {t('common.delete')}
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <ScheduleEditDialog
        open={editing !== null}
        existing={editing === 'new' ? null : editing}
        remotes={remotes}
        onClose={() => setEditing(null)}
        onCreate={onCreate}
        onUpdate={onUpdate}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        loading={deleting}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={async () => {
          if (!pendingDelete) return
          setDeleting(true)
          try {
            await onDelete(pendingDelete.id)
            setPendingDelete(null)
          } finally {
            setDeleting(false)
          }
        }}
        title={t('backup.schedule.delete.title')}
        description={t('backup.schedule.delete.confirm', { name: pendingDelete?.name || '' })}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function ScheduleEditDialog({
  open,
  existing,
  remotes,
  onClose,
  onCreate,
  onUpdate,
}: {
  open: boolean
  existing: BackupSchedule | null
  remotes: BackupRemote[]
  onClose: () => void
  onCreate: (payload: BackupSchedulePayload) => Promise<boolean>
  onUpdate: (id: number, payload: Partial<BackupSchedulePayload>) => Promise<boolean>
}) {
  const t = useT()
  // 用 key 强制重置 dialog state(open + existing 改变都重新初始化)
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        {open ? (
          <ScheduleEditForm
            key={existing ? `edit-${existing.id}` : 'new'}
            existing={existing}
            remotes={remotes}
            onClose={onClose}
            onCreate={onCreate}
            onUpdate={onUpdate}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function ScheduleEditForm({
  existing,
  remotes,
  onClose,
  onCreate,
  onUpdate,
}: {
  existing: BackupSchedule | null
  remotes: BackupRemote[]
  onClose: () => void
  onCreate: (payload: BackupSchedulePayload) => Promise<boolean>
  onUpdate: (id: number, payload: Partial<BackupSchedulePayload>) => Promise<boolean>
}) {
  const t = useT()
  const [name, setName] = useState(existing?.name || '')
  const [cron, setCron] = useState(existing?.cron_expr || '0 4 * * *')
  const [retention, setRetention] = useState(String(existing?.retention_days ?? 30))
  const [includeAttachments, setIncludeAttachments] = useState(
    existing?.include_attachments ?? true,
  )
  const [enabled, setEnabled] = useState(existing?.enabled ?? true)
  const [selectedRemoteIds, setSelectedRemoteIds] = useState<number[]>(
    existing?.remote_ids || [],
  )
  const [submitting, setSubmitting] = useState(false)

  const toggleRemote = (id: number) => {
    setSelectedRemoteIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const payload: BackupSchedulePayload = {
        name: name.trim(),
        cron_expr: cron.trim(),
        retention_days: Math.max(1, Math.round(Number(retention) || 30)),
        include_attachments: includeAttachments,
        enabled,
        remote_ids: selectedRemoteIds,
      }
      let ok = false
      if (existing) {
        ok = await onUpdate(existing.id, payload)
      } else {
        ok = await onCreate(payload)
      }
      if (ok) onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {existing
            ? t('backup.schedule.edit.title')
            : t('backup.schedule.create.title')}
        </DialogTitle>
      </DialogHeader>
      <div className="-mx-6 max-h-[70vh] space-y-3 overflow-y-auto px-6 [scrollbar-gutter:stable]">
        <div className="space-y-1">
          <Label>{t('backup.schedule.field.name')}</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label>{t('backup.schedule.field.cron')}</Label>
          <div className="mb-1 flex flex-wrap gap-1">
            {CRON_PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => setCron(p.value)}
                className={`rounded border px-1.5 py-0.5 text-[11px] transition-colors ${
                  cron === p.value
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border/60 hover:bg-accent/40'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <Input
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            className="font-mono text-xs"
          />
          <p className="text-[10px] text-muted-foreground">
            {t('backup.schedule.field.cronHint')}
          </p>
        </div>
        <div className="space-y-1">
          <Label>{t('backup.schedule.field.retention')}</Label>
          <Input
            type="number"
            min={1}
            value={retention}
            onChange={(e) => setRetention(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label>{t('backup.schedule.field.targets')}</Label>
          <p className="text-[11px] text-muted-foreground">
            {t('backup.schedule.field.targetsHint')}
          </p>
          <div className="flex flex-wrap gap-1.5 rounded-md border border-border/60 bg-muted/20 p-2">
            {remotes.map((r) => {
              const sel = selectedRemoteIds.includes(r.id)
              return (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => toggleRemote(r.id)}
                  className={`rounded px-2 py-1 text-xs transition-colors ${
                    sel
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-card text-muted-foreground hover:bg-accent/40'
                  }`}
                >
                  {r.name}
                  {r.encrypted ? ' 🔒' : ''}
                </button>
              )
            })}
          </div>
        </div>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={includeAttachments}
            onChange={(e) => setIncludeAttachments(e.target.checked)}
          />
          {t('backup.schedule.field.includeAttachments')}
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          {t('backup.schedule.field.enabled')}
        </label>
      </div>
      <DialogFooter>
        <Button variant="outline" disabled={submitting} onClick={onClose}>
          {t('dialog.cancel')}
        </Button>
        <Button
          disabled={
            submitting ||
            !name.trim() ||
            !cron.trim() ||
            selectedRemoteIds.length === 0
          }
          onClick={() => void handleSubmit()}
        >
          {existing ? t('common.save') : t('backup.schedule.button.create')}
        </Button>
      </DialogFooter>
    </>
  )
}
