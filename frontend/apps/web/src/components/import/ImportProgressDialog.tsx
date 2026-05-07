import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import {
  type ImportSseEvent,
  cancelImport,
  streamExecuteImport,
} from '@beecount/api-client'

import { useAuth } from '../../context/AuthContext'

type StageProgress = {
  stage: 'accounts' | 'categories' | 'tags' | 'transactions'
  done: number
  total: number
  skipped?: number
}

interface Props {
  open: boolean
  importToken: string | null
  onClose: () => void
  onSuccess?: (data: { created_tx_count: number; skipped_count: number }) => void
}

export function ImportProgressDialog({ open, importToken, onClose, onSuccess }: Props) {
  const t = useT()
  const { token } = useAuth()
  const [progress, setProgress] = useState<Record<string, StageProgress>>({})
  const [phase, setPhase] = useState<'running' | 'complete' | 'error'>('running')
  const [completeData, setCompleteData] =
    useState<{ created_tx_count: number; skipped_count: number } | null>(null)
  const [errorData, setErrorData] = useState<
    | { code: string; row_number: number; message: string; raw_line?: string; field_name?: string | null }
    | null
  >(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    if (!open || !importToken) return
    cancelledRef.current = false
    setProgress({})
    setPhase('running')
    setCompleteData(null)
    setErrorData(null)

    let aborted = false

    void (async () => {
      try {
        for await (const ev of streamExecuteImport(token, importToken)) {
          if (aborted) break
          handleEvent(ev)
        }
      } catch (err) {
        if (!aborted) {
          setPhase('error')
          setErrorData({
            code: 'IMPORT_NETWORK',
            row_number: 0,
            message: err instanceof Error ? err.message : String(err),
          })
        }
      }
    })()

    return () => {
      aborted = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, importToken])

  const handleEvent = (ev: ImportSseEvent) => {
    if (ev.event === 'stage') {
      setProgress((prev) => ({ ...prev, [ev.data.stage]: { ...ev.data } }))
    } else if (ev.event === 'complete') {
      setPhase('complete')
      setCompleteData({
        created_tx_count: ev.data.created_tx_count,
        skipped_count: ev.data.skipped_count,
      })
      onSuccess?.({
        created_tx_count: ev.data.created_tx_count,
        skipped_count: ev.data.skipped_count,
      })
    } else if (ev.event === 'error') {
      setPhase('error')
      setErrorData(ev.data)
    }
  }

  const onCancel = async () => {
    if (!importToken) return
    cancelledRef.current = true
    try {
      await cancelImport(token, importToken)
    } catch {
      // 静默 — 用户已经按了取消
    }
    onClose()
  }

  const txProgress = progress.transactions
  const overallPercent = computeOverallPercent(progress)

  return (
    <Dialog open={open} onOpenChange={(v) => !v && phase !== 'running' && onClose()}>
      <DialogContent className="max-w-md gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-2 text-base">
            {phase === 'running' ? (
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            ) : phase === 'complete' ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-destructive" />
            )}
            {phase === 'running'
              ? t('import.progress.running')
              : phase === 'complete'
                ? t('import.progress.complete')
                : t('import.progress.failed')}
          </DialogTitle>
        </DialogHeader>

        <div className="px-6 py-5 text-sm">
          {phase === 'running' ? (
            <>
              <div className="mb-3 h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${overallPercent}%` }}
                />
              </div>
              <ul className="space-y-1.5 text-xs">
                <StageLine progress={progress.accounts} stageLabel={t('import.progress.stage.accounts')} />
                <StageLine progress={progress.categories} stageLabel={t('import.progress.stage.categories')} />
                <StageLine progress={progress.tags} stageLabel={t('import.progress.stage.tags')} />
                <StageLine progress={progress.transactions} stageLabel={t('import.progress.stage.transactions')} />
              </ul>
              {txProgress?.skipped ? (
                <p className="mt-3 text-[11px] text-muted-foreground">
                  {t('import.progress.skippedDedup', { count: txProgress.skipped })}
                </p>
              ) : null}
            </>
          ) : phase === 'complete' && completeData ? (
            <p>
              {t('import.progress.completeBody', {
                created: completeData.created_tx_count,
                skipped: completeData.skipped_count,
              })}
            </p>
          ) : phase === 'error' && errorData ? (
            <div className="space-y-2">
              <p className="text-foreground">
                {t('import.progress.failedBody')}
              </p>
              <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-[11px]">
                <p className="font-medium text-destructive">
                  {errorData.code}
                  {errorData.row_number > 0 ? ` · L${errorData.row_number}` : ''}
                  {errorData.field_name ? ` · ${errorData.field_name}` : ''}
                </p>
                <p className="mt-1 text-muted-foreground">{errorData.message}</p>
                {errorData.raw_line ? (
                  <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px]">
                    {errorData.raw_line}
                  </pre>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>

        <DialogFooter className="border-t border-border/60 bg-muted/20 px-6 py-3">
          {phase === 'running' ? (
            <Button variant="outline" size="sm" onClick={onCancel}>
              <X className="mr-1 h-3 w-3" />
              {t('import.progress.cancel')}
            </Button>
          ) : (
            <Button size="sm" onClick={onClose}>
              {t('common.close')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StageLine({ progress, stageLabel }: { progress?: StageProgress; stageLabel: string }) {
  if (!progress) {
    return (
      <li className="flex items-center gap-2 text-muted-foreground/60">
        <span className="h-1.5 w-1.5 rounded-full bg-muted" />
        {stageLabel}
      </li>
    )
  }
  const done = progress.done >= progress.total && progress.total > 0
  return (
    <li className="flex items-center gap-2">
      {done ? (
        <CheckCircle2 className="h-3 w-3 text-emerald-500" />
      ) : (
        <Loader2 className="h-3 w-3 animate-spin text-primary" />
      )}
      <span>{stageLabel}</span>
      <span className="text-muted-foreground">
        {progress.done} / {progress.total}
      </span>
    </li>
  )
}

function computeOverallPercent(progress: Record<string, StageProgress>): number {
  // 简单按 transactions 阶段的进度作为主进度,其它三个阶段权重小
  const tx = progress.transactions
  if (tx && tx.total > 0) return Math.min(100, Math.round((tx.done / tx.total) * 100))
  // 没到 tx 阶段时按已完成 stage 数估算
  const stages = ['accounts', 'categories', 'tags']
  const completed = stages.filter((s) => {
    const p = progress[s]
    return p && p.total > 0 && p.done >= p.total
  }).length
  return Math.min(15, completed * 5)
}
