import { useCallback, useEffect, useState } from 'react'

import {
  createBackupRemote,
  createBackupSchedule,
  deleteBackupRemote,
  deleteBackupRestore,
  deleteBackupSchedule,
  downloadRcloneConfig,
  getBackupRestore,
  listBackupRemotes,
  listBackupRuns,
  listBackupSchedules,
  prepareRestore,
  revealBackupRemote,
  runBackupNow,
  testBackupRemote,
  updateBackupRemote,
  updateBackupSchedule,
  type BackupRemote,
  type BackupRemoteCreatePayload,
  type BackupRestore,
  type BackupRun,
  type BackupSchedule,
  type BackupSchedulePayload,
} from '@beecount/api-client'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useT,
  useToast,
} from '@beecount/ui'
import {
  BackupRemotesPanel,
  BackupRestoreGuideDialog,
  BackupRunsPanel,
  BackupSchedulesPanel,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useSyncEvent } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'

type LiveProgress = {
  phase: string
  bytesTransferred?: number
  bytesTotal?: number
  speed?: number
  remoteName?: string
}

type Tab = 'remotes' | 'schedules' | 'runs'

/**
 * 备份 admin 页 —— 三个 tab。WebSocket 'backup_progress' 事件实时更新当前
 * 进度条;'backup_status' 终态后清掉进度并 refresh runs 列表。
 */
export function AdminBackupPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  // tab 选中状态本地持久化 — 用户在 schedules 配完任务后切到 runs 看进度,
  // 切去别的 page 再回来期望停在 runs 而不是 reset。
  const TAB_STORAGE_KEY = 'beecount.web.adminBackup.tab'
  const [tab, setTabRaw] = useState<Tab>(() => {
    if (typeof window === 'undefined') return 'runs'
    const saved = window.localStorage.getItem(TAB_STORAGE_KEY)
    if (saved === 'runs' || saved === 'schedules' || saved === 'remotes') {
      return saved
    }
    return 'runs'
  })
  const setTab = (next: Tab) => {
    setTabRaw(next)
    try {
      window.localStorage.setItem(TAB_STORAGE_KEY, next)
    } catch {
      // localStorage 满 / private mode 可能抛,忽略
    }
  }
  const [remotes, setRemotes] = useState<BackupRemote[]>([])
  const [schedules, setSchedules] = useState<BackupSchedule[]>([])
  const [runs, setRuns] = useState<BackupRun[]>([])
  const [liveProgress, setLiveProgress] = useState<LiveProgress | null>(null)

  // Restore 相关 state
  const [restoreRun, setRestoreRun] = useState<BackupRun | null>(null)
  const [restoreStatus, setRestoreStatus] = useState<BackupRestore | null>(null)
  const [restoreLive, setRestoreLive] = useState<{
    phase: string
    bytesTransferred?: number
    bytesTotal?: number
  } | null>(null)

  const notifyError = (err: unknown) =>
    toast.error(localizeError(err, t), t('notice.error'))
  const notifySuccess = (msg: string) =>
    toast.success(msg, t('notice.success'))

  const loadAll = useCallback(async () => {
    try {
      const [r, s, runResult] = await Promise.all([
        listBackupRemotes(token),
        listBackupSchedules(token),
        listBackupRuns(token, { limit: 50 }),
      ])
      setRemotes(r)
      setSchedules(s)
      setRuns(runResult.items)
    } catch (err) {
      notifyError(err)
    }
  }, [token])

  const refreshRuns = useCallback(async () => {
    try {
      const result = await listBackupRuns(token, { limit: 50 })
      setRuns(result.items)
    } catch (err) {
      notifyError(err)
    }
  }, [token])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  // ============ WebSocket 实时进度 ============

  useSyncEvent('backup_progress', (event) => {
    const data = event as {
      phase?: string
      bytesTransferred?: number
      bytesTotal?: number
      speed?: number
      remoteName?: string
    }
    setLiveProgress({
      phase: data.phase || 'unknown',
      bytesTransferred: data.bytesTransferred,
      bytesTotal: data.bytesTotal,
      speed: data.speed,
      remoteName: data.remoteName,
    })
  })

  useSyncEvent('backup_status', (event) => {
    const data = event as { status?: string }
    setLiveProgress(null)
    if (data.status) {
      if (data.status === 'succeeded') {
        notifySuccess(t('backup.notice.runSucceeded'))
      } else if (data.status === 'partial') {
        toast.warning?.(t('backup.notice.runPartial'), t('notice.warning'))
      } else {
        notifyError(new Error(t('backup.notice.runFailed')))
      }
    }
    void refreshRuns()
  })

  // Restore 进度事件
  useSyncEvent('restore_progress', (event) => {
    const data = event as {
      runId?: number
      phase?: string
      bytesTransferred?: number
      bytesTotal?: number
      errorMessage?: string
    }
    if (!restoreRun || data.runId !== restoreRun.id) return
    if (data.phase === 'done' || data.phase === 'failed') {
      setRestoreLive(null)
      // 拉最新 status 刷新 dialog
      void getBackupRestore(token, restoreRun.id)
        .then(setRestoreStatus)
        .catch(notifyError)
      if (data.phase === 'done') {
        notifySuccess(t('backup.restore.notice.ready'))
      } else {
        notifyError(new Error(data.errorMessage || t('backup.restore.failed')))
      }
    } else {
      setRestoreLive({
        phase: data.phase || 'unknown',
        bytesTransferred: data.bytesTransferred,
        bytesTotal: data.bytesTotal,
      })
    }
  })

  // ============ Remote handlers ============

  const onCreateRemote = async (payload: BackupRemoteCreatePayload) => {
    try {
      await createBackupRemote(token, payload)
      notifySuccess(t('backup.remote.notice.created'))
      await loadAll()
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onUpdateRemote = async (
    id: number,
    payload: Parameters<typeof updateBackupRemote>[2],
  ) => {
    try {
      await updateBackupRemote(token, id, payload)
      notifySuccess(t('backup.remote.notice.updated'))
      await loadAll()
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onTestRemote = async (id: number) => {
    try {
      const res = await testBackupRemote(token, id)
      if (res.ok) {
        notifySuccess(t('backup.remote.notice.testOk'))
      } else {
        notifyError(new Error(res.error || t('backup.remote.notice.testFail')))
      }
      await loadAll()
    } catch (err) {
      notifyError(err)
    }
  }

  const onDeleteRemote = async (id: number) => {
    try {
      await deleteBackupRemote(token, id)
      notifySuccess(t('backup.remote.notice.deleted'))
      await loadAll()
    } catch (err) {
      notifyError(err)
    }
  }

  // ============ Schedule handlers ============

  const onCreateSchedule = async (payload: BackupSchedulePayload) => {
    try {
      await createBackupSchedule(token, payload)
      notifySuccess(t('backup.schedule.notice.created'))
      await loadAll()
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onUpdateSchedule = async (
    id: number,
    payload: Partial<BackupSchedulePayload>,
  ) => {
    try {
      await updateBackupSchedule(token, id, payload)
      notifySuccess(t('backup.schedule.notice.updated'))
      await loadAll()
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onDeleteSchedule = async (id: number) => {
    try {
      await deleteBackupSchedule(token, id)
      notifySuccess(t('backup.schedule.notice.deleted'))
      await loadAll()
    } catch (err) {
      notifyError(err)
    }
  }

  const onRunNow = async (id: number) => {
    try {
      await runBackupNow(token, id)
      notifySuccess(t('backup.schedule.notice.triggered'))
      // run-now 是异步的,server 立即返回 placeholder。等 WS 推 backup_status
      // 到达时会 refreshRuns;这里也手动刷一次。
      setTimeout(() => void refreshRuns(), 500)
    } catch (err) {
      notifyError(err)
    }
  }

  // ============ Restore handlers ============

  const onRestoreRun = async (run: BackupRun) => {
    setRestoreRun(run)
    setRestoreLive(null)
    try {
      // 看一眼当前是否已经有 restore 结果(再次打开 dialog 时直接展示)
      const existing = await getBackupRestore(token, run.id).catch(() => null)
      setRestoreStatus(existing)
    } catch {
      setRestoreStatus(null)
    }
  }

  const onTriggerRestore = async () => {
    if (!restoreRun) return
    try {
      const result = await prepareRestore(token, restoreRun.id)
      setRestoreStatus(result)
      setRestoreLive({ phase: 'downloading' })
    } catch (err) {
      notifyError(err)
    }
  }

  const onCleanupRestore = async () => {
    if (!restoreRun) return
    try {
      await deleteBackupRestore(token, restoreRun.id)
      setRestoreStatus(null)
      setRestoreLive(null)
      notifySuccess(t('backup.restore.notice.cleaned'))
    } catch (err) {
      notifyError(err)
    }
  }

  // ============ Download rclone.conf ============

  const onDownloadConfig = async () => {
    try {
      const text = await downloadRcloneConfig(token)
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'rclone.conf'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      notifySuccess(t('backup.runs.notice.confDownloaded'))
    } catch (err) {
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('backup.title')}</CardTitle>
      </CardHeader>
      <CardContent>
        <Tabs>
          <TabsList>
            <TabsTrigger active={tab === 'runs'} onClick={() => setTab('runs')}>
              {t('backup.tab.runs')}
            </TabsTrigger>
            <TabsTrigger
              active={tab === 'schedules'}
              onClick={() => setTab('schedules')}
            >
              {t('backup.tab.schedules')}
            </TabsTrigger>
            <TabsTrigger
              active={tab === 'remotes'}
              onClick={() => setTab('remotes')}
            >
              {t('backup.tab.remotes')}
            </TabsTrigger>
          </TabsList>
          {tab === 'runs' ? (
            <TabsContent>
              <BackupRunsPanel
                runs={runs}
                liveProgress={liveProgress}
                onDownloadConfig={onDownloadConfig}
                onRestoreRun={(run) => void onRestoreRun(run)}
              />
            </TabsContent>
          ) : null}
          {tab === 'schedules' ? (
            <TabsContent>
              <BackupSchedulesPanel
                schedules={schedules}
                remotes={remotes}
                onCreate={onCreateSchedule}
                onUpdate={onUpdateSchedule}
                onDelete={onDeleteSchedule}
                onRunNow={onRunNow}
              />
            </TabsContent>
          ) : null}
          {tab === 'remotes' ? (
            <TabsContent>
              <BackupRemotesPanel
                remotes={remotes}
                onCreate={onCreateRemote}
                onUpdate={onUpdateRemote}
                onTest={onTestRemote}
                onDelete={onDeleteRemote}
                onReveal={async (id) => {
                  const data = await revealBackupRemote(token, id)
                  // 把 encrypted + age_passphrase 字段塞到 config 里一起传
                  // (panel 回填表单时用),用下划线前缀 key 防跟 backend 字段冲突
                  return {
                    ...data.config,
                    __encrypted__: data.encrypted ? '1' : '',
                    age_passphrase: data.age_passphrase || '',
                  }
                }}
              />
            </TabsContent>
          ) : null}
        </Tabs>
      </CardContent>

      <BackupRestoreGuideDialog
        open={restoreRun !== null}
        onClose={() => {
          setRestoreRun(null)
          setRestoreStatus(null)
          setRestoreLive(null)
        }}
        restore={restoreStatus}
        liveProgress={restoreLive}
        onTrigger={onTriggerRestore}
        onCleanup={onCleanupRestore}
        onDownloadConfig={onDownloadConfig}
      />
    </Card>
  )
}
