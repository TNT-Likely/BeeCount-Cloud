import { useMemo, useState } from 'react'
import { ChevronDown, Palette } from 'lucide-react'

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  PrimaryColorPicker,
  useT,
  usePrimaryColor,
} from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { TwoFactorAuthInline } from './TwoFactorAuthSection'

/**
 * 设置 - 账号 / 主题色 / 二次验证 / 同步偏好(只读) section。
 *
 * 顶部一张 hero 卡:头像 + email + 两个 inline pill(主题色 / 二次验证),
 * 各自打开 popup。第二张卡保留 sync 偏好(只读,概念跟账号偏好不同)。
 *
 * 头像 + display_name 只读 — 统一在 mobile "我的" 里修改,避免两端都能改
 * 导致 LWW 抖动。
 */
export function SettingsProfileAppearanceSection() {
  const t = useT()
  const { profileMe, sessionUserId } = useAuth()
  const { color: primaryColor } = usePrimaryColor()
  const [themeOpen, setThemeOpen] = useState(false)

  const profileDisplayLabel = useMemo(
    () => profileMe?.display_name?.trim() || profileMe?.email || sessionUserId || '-',
    [profileMe, sessionUserId]
  )
  const profileInitial = useMemo(
    () => profileDisplayLabel.trim().charAt(0).toUpperCase() || '?',
    [profileDisplayLabel]
  )

  return (
    <div className="space-y-4">
      <Card className="bc-panel overflow-hidden">
        <div className="relative">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/20 via-primary/5 to-transparent" />
          <CardContent className="relative space-y-5 p-6">
            <div className="flex flex-wrap items-center gap-4">
              {profileMe?.avatar_url ? (
                <img
                  alt={profileDisplayLabel}
                  className="h-16 w-16 rounded-full border-2 border-primary/30 object-cover shadow-sm"
                  src={profileMe.avatar_url}
                />
              ) : (
                <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-primary/30 bg-muted text-base font-semibold text-muted-foreground">
                  {profileInitial}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate text-lg font-semibold">{profileDisplayLabel}</p>
                <p className="truncate text-xs text-muted-foreground">{profileMe?.email || '-'}</p>
              </div>
            </div>

            {/* Inline pills:主题色 + 二次验证 各自打开 popup */}
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setThemeOpen(true)}
                className="group inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/40 px-3 py-1.5 text-xs font-medium transition hover:bg-muted"
                aria-label={t('profile.theme.title')}
              >
                <Palette className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{t('profile.theme.title')}</span>
                <span
                  className="inline-block h-3.5 w-3.5 rounded-full border border-border/60 shadow-sm"
                  style={{ background: primaryColor }}
                  aria-hidden
                />
                <ChevronDown className="h-3 w-3 text-muted-foreground transition group-hover:translate-y-0.5" />
              </button>
              <TwoFactorAuthInline />
            </div>

            <p className="text-xs text-muted-foreground">{t('profile.avatarManagedByApp')}</p>
          </CardContent>
        </div>
      </Card>

      <Dialog open={themeOpen} onOpenChange={setThemeOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('profile.theme.title')}</DialogTitle>
            <DialogDescription>{t('profile.theme.desc')}</DialogDescription>
          </DialogHeader>
          <PrimaryColorPicker />
        </DialogContent>
      </Dialog>

      <Card className="bc-panel">
        <CardHeader>
          <CardTitle>{t('profile.sync.title')}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-muted-foreground">{t('profile.sync.desc')}</p>

          <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <span
                  className="inline-block h-4 w-4 rounded-full ring-2 ring-background"
                  style={{ background: 'rgb(var(--income-rgb))' }}
                  aria-label={t('enum.txType.income')}
                />
                <span className="text-sm">{t('enum.txType.income')}</span>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className="inline-block h-4 w-4 rounded-full ring-2 ring-background"
                  style={{ background: 'rgb(var(--expense-rgb))' }}
                  aria-label={t('enum.txType.expense')}
                />
                <span className="text-sm">{t('enum.txType.expense')}</span>
              </div>
            </div>
            <span className="rounded-full border border-border/60 bg-card px-3 py-1 text-xs font-medium">
              {(profileMe?.income_is_red ?? true)
                ? t('profile.sync.incomeScheme.red')
                : t('profile.sync.incomeScheme.green')}
            </span>
          </div>

          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.headerDecoration')}
              </p>
              <p className="mt-1 text-sm font-medium">
                {profileMe?.appearance?.header_decoration_style || t('common.dash')}
              </p>
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.compactAmount')}
              </p>
              <p className="mt-1 text-sm font-medium">
                {profileMe?.appearance?.compact_amount === true
                  ? t('common.on')
                  : profileMe?.appearance?.compact_amount === false
                    ? t('common.off')
                    : t('common.dash')}
              </p>
            </div>
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.showTime')}
              </p>
              <p className="mt-1 text-sm font-medium">
                {profileMe?.appearance?.show_transaction_time === true
                  ? t('common.on')
                  : profileMe?.appearance?.show_transaction_time === false
                    ? t('common.off')
                    : t('common.dash')}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
