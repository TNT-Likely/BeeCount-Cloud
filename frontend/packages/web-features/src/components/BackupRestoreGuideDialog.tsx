import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'

import type { BackupRestore } from '@beecount/api-client'

type Props = {
  open: boolean
  onClose: () => void
  restore: BackupRestore | null
  liveProgress?: { phase: string; bytesTransferred?: number; bytesTotal?: number } | null
  /** 用户已点过「准备恢复」时的进度查看;首次触发用 onTrigger。 */
  onTrigger?: () => Promise<void>
  onCleanup?: () => Promise<void>
  onDownloadConfig?: () => Promise<void>
}

function fmtBytes(n: number | null | undefined): string {
  if (!n) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 2)} ${units[i]}`
}

/**
 * Restore 恢复指引 dialog —— 三段式:
 * 1. 服务端准备:点「准备恢复」→ 后台下载 + 解包到隔离目录 + 实时进度
 * 2. 完成后展示绝对路径 + 5 步 shell 片段(用户自己跑)
 * 3. CLI 自助路径(折叠区,跨机器迁移用)
 */
export function BackupRestoreGuideDialog({
  open,
  onClose,
  restore,
  liveProgress,
  onTrigger,
  onCleanup,
  onDownloadConfig,
}: Props) {
  const t = useT()
  const [showCli, setShowCli] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [cleaning, setCleaning] = useState(false)

  const phase = liveProgress?.phase || restore?.phase
  const isRunning = phase === 'downloading' || phase === 'extracting'
  const isDone = phase === 'done'
  const isFailed = phase === 'failed'

  const path = restore?.extracted_path || '/data/restore/<run_id>/extracted'

  const shellScript = `# 1. 停服(Docker)
docker compose stop beecount-cloud

# 2. 备一份当前数据(以防回滚)
mv /path/to/data/beecount.db /path/to/data/beecount.db.before-restore.bak

# 3. 替换 SQLite + 附件 + JWT 密钥
cp ${path}/db.sqlite3 /path/to/data/beecount.db
rsync -a ${path}/attachments/ /path/to/data/attachments/
[ -f ${path}/.jwt_secret ] && cp ${path}/.jwt_secret /path/to/data/.jwt_secret

# 4. 启动服务
docker compose start beecount-cloud

# 5. 验证一切正常后清理 restore 目录
# (或在 Web UI 点「清理」按钮)
rm -rf ${path}/..`

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(shellScript)
    } catch {
      // ignore
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('backup.restore.title')}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto text-xs">
          {/* 状态栏 */}
          <div className="rounded-md border border-border/60 bg-muted/30 p-3">
            {!restore && !isRunning ? (
              <div>
                <p className="mb-2 text-muted-foreground">
                  {t('backup.restore.intro')}
                </p>
                <Button
                  size="sm"
                  disabled={triggering || !onTrigger}
                  onClick={async () => {
                    if (!onTrigger) return
                    setTriggering(true)
                    try {
                      await onTrigger()
                    } finally {
                      setTriggering(false)
                    }
                  }}
                >
                  {t('backup.restore.button.trigger')}
                </Button>
              </div>
            ) : null}

            {isRunning ? (
              <div>
                <div className="mb-1 flex items-center gap-2 font-medium">
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                  {t(`backup.restore.phase.${phase}`)}
                </div>
                {liveProgress?.bytesTotal ? (
                  <>
                    <div className="text-[11px] text-muted-foreground">
                      {fmtBytes(liveProgress.bytesTransferred)} /{' '}
                      {fmtBytes(liveProgress.bytesTotal)}
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-blue-500/20">
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
                  </>
                ) : null}
              </div>
            ) : null}

            {isFailed ? (
              <div>
                <div className="font-medium text-red-500">
                  ✗ {t('backup.restore.failed')}
                </div>
                {restore?.error_message ? (
                  <div className="mt-1 text-[11px] text-red-500">
                    {restore.error_message}
                  </div>
                ) : null}
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    disabled={triggering || !onTrigger}
                    onClick={async () => {
                      if (!onTrigger) return
                      setTriggering(true)
                      try {
                        await onTrigger()
                      } finally {
                        setTriggering(false)
                      }
                    }}
                  >
                    {t('backup.restore.button.retry')}
                  </Button>
                </div>
              </div>
            ) : null}

            {isDone ? (
              <div>
                <div className="font-medium text-emerald-600 dark:text-emerald-400">
                  ✓ {t('backup.restore.done')}
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {t('backup.restore.path')}:{' '}
                  <span className="font-mono">{path}</span>
                </div>
              </div>
            ) : null}
          </div>

          {/* 用户手动替换的指引 */}
          {isDone ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-medium">
                  {t('backup.restore.steps.title')}
                </span>
                <Button size="sm" variant="outline" onClick={() => void copyAll()}>
                  {t('backup.restore.button.copyAll')}
                </Button>
              </div>
              <pre className="overflow-x-auto rounded-md border border-border/60 bg-muted/40 p-3 font-mono text-[10px] leading-relaxed">
{shellScript}
              </pre>
              <p className="text-[11px] text-amber-600 dark:text-amber-400">
                ⚠ {t('backup.restore.warn.replace')}
              </p>
            </div>
          ) : null}

          {/* CLI 自助路径(折叠) */}
          <div className="rounded-md border border-border/40 bg-muted/20 p-2">
            <button
              type="button"
              onClick={() => setShowCli((v) => !v)}
              className="text-left text-[11px] font-medium text-muted-foreground hover:text-foreground"
            >
              {showCli ? '▼' : '▶'} {t('backup.restore.cli.title')}
            </button>
            {showCli ? (
              <div className="mt-2 space-y-2 text-[11px]">
                <p className="text-muted-foreground">
                  {t('backup.restore.cli.desc')}
                </p>
                <ol className="list-inside list-decimal space-y-1 pl-2">
                  <li>{t('backup.restore.cli.step1')}</li>
                  <li>
                    {t('backup.restore.cli.step2')}{' '}
                    {onDownloadConfig ? (
                      <Button
                        size="sm"
                        variant="outline"
                        className="ml-1 h-5 px-1.5 text-[10px]"
                        onClick={() => void onDownloadConfig()}
                      >
                        {t('backup.runs.downloadConf')}
                      </Button>
                    ) : null}
                  </li>
                  <li>{t('backup.restore.cli.step3')}</li>
                  <li>
                    {t('backup.restore.cli.step4')}
                    <pre className="mt-1 overflow-x-auto rounded bg-muted/40 p-1 font-mono text-[10px]">
                      rclone ls {restore?.source_remote_name || '<remote>'}:
                    </pre>
                  </li>
                  <li>
                    {t('backup.restore.cli.step5')}
                    <pre className="mt-1 overflow-x-auto rounded bg-muted/40 p-1 font-mono text-[10px]">
                      rclone copy{' '}
                      {restore?.source_remote_name || '<remote>'}:
                      {restore?.backup_filename || '<filename>'} ./
                    </pre>
                  </li>
                  <li>
                    {t('backup.restore.cli.step6')}
                    <pre className="mt-1 overflow-x-auto rounded bg-muted/40 p-1 font-mono text-[10px]">
                      {restore?.backup_filename?.endsWith('.zip')
                        ? `unzip ${restore.backup_filename}    # 或双击,系统弹密码框`
                        : `tar -xzf ${restore?.backup_filename || '<filename>.tar.gz'}`}
                    </pre>
                  </li>
                  <li>{t('backup.restore.cli.step7')}</li>
                </ol>
              </div>
            ) : null}
          </div>
        </div>
        <DialogFooter>
          {onCleanup ? (
            <Button
              variant="outline"
              disabled={cleaning || !restore || isRunning}
              onClick={async () => {
                setCleaning(true)
                try {
                  await onCleanup()
                } finally {
                  setCleaning(false)
                }
              }}
            >
              {t('backup.restore.button.cleanup')}
            </Button>
          ) : null}
          <Button onClick={onClose}>{t('dialog.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
