import { useEffect, useState } from 'react'
import { Lock, LockOpen, ShieldAlert } from 'lucide-react'

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  useT,
  useToast,
} from '@beecount/ui'
import { patchLockSettings } from '@beecount/api-client'

import { DatePicker } from '../../components/DatePicker'
import { useAuth } from '../../context/AuthContext'
import { lockDayToIso, useLock } from '../../context/LockContext'
import { localizeError } from '../../i18n/errors'

/**
 * 用户级锁账设置(0067)。
 *
 * 锁定期内的交易与账户初始金额由后端强制冻结(423);这里只负责开关与日期,
 * 保存后立即刷新全局 LockContext,让各页面的按钮禁用状态同步生效。
 */
export function LockSettingsSection() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { isLocked, lockDay, refresh } = useLock()
  const [draftDay, setDraftDay] = useState(lockDay ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => { setDraftDay(lockDay ?? '') }, [lockDay])

  const save = async (nextLocked: boolean) => {
    if (!token) return
    if (!draftDay) {
      toast.error(t('settings.lock.error.noDate'), t('notice.error'))
      return
    }
    setSaving(true)
    try {
      await patchLockSettings(token, {
        lock_date: lockDayToIso(draftDay),
        is_locked: nextLocked,
      })
      await refresh()
      toast.success(t(nextLocked ? 'settings.lock.notice.locked' : 'settings.lock.notice.unlocked'), t('notice.success'))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {isLocked ? <Lock className="h-4 w-4 text-amber-600" /> : <LockOpen className="h-4 w-4" />}
          {t('settings.lock.title')}
        </CardTitle>
        <CardDescription>{t('settings.lock.desc')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <label className="text-sm font-medium">{t('settings.lock.date')}</label>
          <DatePicker
            value={draftDay}
            onChange={setDraftDay}
            label={t('settings.lock.date')}
          />
          <p className="text-xs text-muted-foreground">{t('settings.lock.hint')}</p>
        </div>

        {isLocked && lockDay ? (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{t('settings.lock.activeHint').replace('{date}', lockDay)}</span>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void save(true)} disabled={saving || isLocked}>
            <Lock className="mr-1 h-4 w-4" />{t('settings.lock.button.lock')}
          </Button>
          <Button variant="outline" onClick={() => void save(false)} disabled={saving || !isLocked}>
            <LockOpen className="mr-1 h-4 w-4" />{t('settings.lock.button.unlock')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
