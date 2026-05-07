import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Camera,
  Check,
  ChevronDown,
  Loader2,
  Moon,
  Palette,
  Pencil,
  Sun,
  Sunrise,
  Sunset,
  X,
  type LucideIcon,
} from 'lucide-react'

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  PrimaryColorPicker,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast,
  usePrimaryColor,
} from '@beecount/ui'

import { patchProfileMe, uploadProfileAvatar } from '@beecount/api-client'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'
import { TwoFactorAuthInline } from './TwoFactorAuthSection'

const AVATAR_MAX_BYTES = 4 * 1024 * 1024 // 4 MB,跟 server 限制一致
const DISPLAY_NAME_MAX = 60

/** 按本地时段返回欢迎语 i18n key + 配图 —— 5-11 / 11-13 / 13-18 / 18-23 /
 *  23-5。icon 用 lucide-react,不同时段 vibe 不同。 */
function pickGreeting(): { key: string; icon: LucideIcon; tone: string } {
  const h = new Date().getHours()
  if (h >= 5 && h < 11)
    return { key: 'profile.greeting.morning', icon: Sunrise, tone: 'text-amber-500' }
  if (h >= 11 && h < 13)
    return { key: 'profile.greeting.noon', icon: Sun, tone: 'text-amber-500' }
  if (h >= 13 && h < 18)
    return { key: 'profile.greeting.afternoon', icon: Sun, tone: 'text-orange-500' }
  if (h >= 18 && h < 23)
    return { key: 'profile.greeting.evening', icon: Sunset, tone: 'text-rose-500' }
  return { key: 'profile.greeting.night', icon: Moon, tone: 'text-indigo-400' }
}

/**
 * 设置 - 账号 / 主题色 / 二次验证 / 同步偏好 section。
 *
 * 头像和收支配色现在 web 端也可写 —— 改完会推送 server,然后 server 广播
 * `profile_change` WS,mobile 端 sync_engine 自动 syncMyProfile() 拉新。
 * 实现见 .docs/web-tx-batch-actions.md(同期):API client 多了 patchProfileMe
 * 的 income_is_red / theme_primary_color / appearance 字段 + uploadProfileAvatar。
 */
export function SettingsProfileAppearanceSection() {
  const t = useT()
  const toast = useToast()
  const { token, profileMe, sessionUserId, refreshProfile } = useAuth()
  const { color: primaryColor } = usePrimaryColor()
  const [themeOpen, setThemeOpen] = useState(false)
  const [avatarUploading, setAvatarUploading] = useState(false)
  const [incomeColorSaving, setIncomeColorSaving] = useState(false)
  const [nameEditing, setNameEditing] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [nameSaving, setNameSaving] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  // 欢迎语随时段变化:每分钟刷一次 tick,刚好跨越 11/13/18/23 这些边界时
  // UI 自动更新。useState 持有 tick 数,变了就重新走 useMemo。
  const [greetingTick, setGreetingTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setGreetingTick((n) => n + 1), 60_000)
    return () => clearInterval(id)
  }, [])
  const greeting = useMemo(() => pickGreeting(), [greetingTick])
  // JSX 要求大写起始,把 icon 别名出来
  const GreetingIcon = greeting.icon

  const profileDisplayLabel = useMemo(
    () => profileMe?.display_name?.trim() || profileMe?.email || sessionUserId || '-',
    [profileMe, sessionUserId]
  )
  const profileInitial = useMemo(
    () => profileDisplayLabel.trim().charAt(0).toUpperCase() || '?',
    [profileDisplayLabel]
  )

  const handleAvatarPick = () => {
    fileInputRef.current?.click()
  }

  const handleAvatarSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    // 选完后清掉 input,允许同名文件再次触发 onChange
    event.target.value = ''
    if (!file) return
    if (!file.type.startsWith('image/')) {
      toast.error(t('profile.avatar.upload.invalidType'))
      return
    }
    if (file.size > AVATAR_MAX_BYTES) {
      toast.error(t('profile.avatar.upload.tooLarge'))
      return
    }
    setAvatarUploading(true)
    try {
      await uploadProfileAvatar(token, file)
      // server 已广播 profile_change,WS 监听会触发 refreshProfile;这里也立即拉一次
      // 兜底,避免 WS 偶尔丢包或本地连接刚断开。
      await refreshProfile()
      toast.success(t('profile.avatar.upload.success'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setAvatarUploading(false)
    }
  }

  const startNameEdit = () => {
    setNameDraft(profileMe?.display_name?.trim() || '')
    setNameEditing(true)
    // 聚焦 / 选中走 Input 的 onFocus(挂载后会自动 autoFocus)
  }
  const cancelNameEdit = () => {
    setNameEditing(false)
    setNameDraft('')
  }
  const submitNameEdit = async () => {
    if (nameSaving) return
    const next = nameDraft.trim().slice(0, DISPLAY_NAME_MAX)
    const current = profileMe?.display_name?.trim() || ''
    if (next === current) {
      cancelNameEdit()
      return
    }
    setNameSaving(true)
    try {
      // 空字符串 → 允许清空 display_name(回退到展示 email);server 接受空串
      await patchProfileMe(token, { display_name: next })
      await refreshProfile()
      setNameEditing(false)
      setNameDraft('')
      toast.success(t('profile.displayName.saved'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setNameSaving(false)
    }
  }

  const incomeIsRed = profileMe?.income_is_red ?? true

  const handleIncomeColorToggle = async () => {
    if (incomeColorSaving) return
    const next = !incomeIsRed
    setIncomeColorSaving(true)
    try {
      await patchProfileMe(token, { income_is_red: next })
      await refreshProfile()
      toast.success(t('profile.sync.incomeScheme.saved'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setIncomeColorSaving(false)
    }
  }

  // 三个外观偏好(月装饰 / 紧凑金额 / 显示交易时间)—— 现在 web 也可写。
  // 改任一项时,把整个 appearance dict 一起 PATCH(server 是整体替换语义,
  // 单字段传过去会丢掉其它字段)。 :这里需要先合并出"完整的下一个 appearance"
  // 再发 patch,而不是只发改动的那个 key。
  const appearance = profileMe?.appearance ?? {}
  const headerDecorationStyle = appearance.header_decoration_style ?? 'icons'
  const compactAmount = appearance.compact_amount ?? false
  const showTransactionTime = appearance.show_transaction_time ?? false
  const [appearanceSaving, setAppearanceSaving] = useState(false)

  const saveAppearance = async (
    patch: Partial<NonNullable<typeof profileMe>['appearance']>,
  ) => {
    if (appearanceSaving) return
    setAppearanceSaving(true)
    try {
      await patchProfileMe(token, {
        appearance: {
          header_decoration_style: headerDecorationStyle,
          compact_amount: compactAmount,
          show_transaction_time: showTransactionTime,
          ...patch,
        },
      })
      await refreshProfile()
      toast.success(t('profile.sync.appearanceSaved'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setAppearanceSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="bc-panel overflow-hidden">
        <div className="relative">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/20 via-primary/5 to-transparent" />
          <CardContent className="relative space-y-5 p-6">
            <div className="flex flex-wrap items-center gap-4">
              {/* 头像 — hover 出 Camera + 暗罩,点击触发文件选择器。常驻角标
                  视觉太重,改回 hover-reveal。 */}
              <button
                type="button"
                onClick={handleAvatarPick}
                disabled={avatarUploading}
                className="group relative h-16 w-16 shrink-0 overflow-hidden rounded-full border-2 border-primary/30 shadow-sm transition hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-60"
                aria-label={t('profile.avatar.upload.button') as string}
                title={t('profile.avatar.upload.button') as string}
              >
                {profileMe?.avatar_url ? (
                  <img
                    alt={profileDisplayLabel}
                    className="h-full w-full object-cover"
                    src={profileMe.avatar_url}
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-muted text-base font-semibold text-muted-foreground">
                    {profileInitial}
                  </div>
                )}
                <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition group-hover:opacity-100">
                  {avatarUploading ? (
                    <Loader2 className="h-4 w-4 animate-spin text-white" />
                  ) : (
                    <Camera className="h-4 w-4 text-white" />
                  )}
                </div>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleAvatarSelected}
              />
              <div className="min-w-0 flex-1">
                {/* 欢迎语图标 + 文案 + display name 同一行 —— icon 按时段切
                    (Sunrise / Sun / Sunset / Moon),配色 amber/orange/rose/indigo;
                    名字 hover 出 ✏️,点击进入 inline edit。 */}
                {nameEditing ? (
                  <div className="flex items-center gap-1.5">
                    <GreetingIcon
                      className={`h-4 w-4 shrink-0 ${greeting.tone}`}
                      aria-hidden
                    />
                    <span className="shrink-0 text-sm text-muted-foreground">
                      {t(greeting.key)},
                    </span>
                    <Input
                      autoFocus
                      onFocus={(e) => e.currentTarget.select()}
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          void submitNameEdit()
                        } else if (e.key === 'Escape') {
                          e.preventDefault()
                          cancelNameEdit()
                        }
                      }}
                      maxLength={DISPLAY_NAME_MAX}
                      placeholder={profileMe?.email || ''}
                      className="h-8 max-w-[240px] text-base font-semibold"
                      disabled={nameSaving}
                    />
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      onClick={() => void submitNameEdit()}
                      disabled={nameSaving}
                      aria-label={t('common.save') as string}
                    >
                      {nameSaving ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="h-3.5 w-3.5" />
                      )}
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      onClick={cancelNameEdit}
                      disabled={nameSaving}
                      aria-label={t('common.cancel') as string}
                    >
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={startNameEdit}
                    className="group/name -ml-1 flex max-w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left transition hover:bg-muted/40"
                    aria-label={t('profile.displayName.edit') as string}
                  >
                    <GreetingIcon
                      className={`h-4 w-4 shrink-0 ${greeting.tone}`}
                      aria-hidden
                    />
                    <span className="shrink-0 text-sm text-muted-foreground">
                      {t(greeting.key)},
                    </span>
                    <span className="truncate text-lg font-semibold">
                      {profileDisplayLabel}
                    </span>
                    <Pencil className="h-3 w-3 shrink-0 text-muted-foreground opacity-0 transition group-hover/name:opacity-100" />
                  </button>
                )}
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
          {/* 收支配色:可点 toggle —— web 改后 server 广播 profile_change,
              mobile 端 sync_engine 监听到自动拉新 */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
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
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={handleIncomeColorToggle}
              disabled={incomeColorSaving}
            >
              {incomeColorSaving ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : null}
              {incomeIsRed
                ? t('profile.sync.incomeScheme.red')
                : t('profile.sync.incomeScheme.green')}
            </Button>
          </div>

          <div className="grid gap-2 sm:grid-cols-3">
            {/* 月装饰风格 —— Select 4 选项,跟 mobile 端 headerDecorationStyle
                provider 注释里列出的可选值对齐(none / icons / particles /
                honeycomb)。改完整体 PATCH 整个 appearance dict。 */}
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.headerDecoration')}
              </p>
              <Select
                value={headerDecorationStyle}
                onValueChange={(value) =>
                  void saveAppearance({ header_decoration_style: value })
                }
                disabled={appearanceSaving}
              >
                <SelectTrigger className="mt-1 h-8 border-0 bg-transparent px-0 text-sm font-medium shadow-none focus:ring-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('profile.sync.headerDecoration.none')}</SelectItem>
                  <SelectItem value="icons">{t('profile.sync.headerDecoration.icons')}</SelectItem>
                  <SelectItem value="particles">{t('profile.sync.headerDecoration.particles')}</SelectItem>
                  <SelectItem value="honeycomb">{t('profile.sync.headerDecoration.honeycomb')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {/* 余额显示格式:跟 mobile 一样下拉选择 — full(完整金额) / compact(简洁,如 12.3万) */}
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.compactAmount')}
              </p>
              <Select
                value={compactAmount ? 'compact' : 'full'}
                onValueChange={(value) =>
                  void saveAppearance({ compact_amount: value === 'compact' })
                }
                disabled={appearanceSaving}
              >
                <SelectTrigger className="mt-1 h-8 border-0 bg-transparent px-0 text-sm font-medium shadow-none focus:ring-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="full">{t('profile.sync.compactAmount.full')}</SelectItem>
                  <SelectItem value="compact">{t('profile.sync.compactAmount.compact')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {/* 显示交易时间:Switch 风格(iOS pill) */}
            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.showTime')}
              </p>
              <button
                type="button"
                role="switch"
                aria-checked={showTransactionTime}
                aria-label={t('profile.sync.showTime') as string}
                disabled={appearanceSaving}
                onClick={() =>
                  void saveAppearance({ show_transaction_time: !showTransactionTime })
                }
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  showTransactionTime ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    showTransactionTime ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
