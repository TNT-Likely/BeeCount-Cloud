import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Camera,
  Check,
  ChevronDown,
  Loader2,
  Moon,
  MoonStar,
  Palette,
  Pencil,
  RotateCcw,
  Sun,
  Sunrise,
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
  useT,
  useToast,
  usePrimaryColor,
} from '@beecount/ui'

import { patchProfileMe, uploadProfileAvatar } from '@beecount/api-client'

import { CategoryIcon } from '@beecount/web-features'
import {
  DEFAULT_FLOW_ICONS,
  FLOW_ICON_KEYS,
  INCOME_ICON_GROUPS,
  resolveFlowIcon,
  resolveFlowIconColor,
  type FlowIconKey,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import {
  HEADER_SKINS,
  HEADER_SKIN_GROUP_ORDER,
  boundPrimaryOf,
  headerSkinGroupLabelKey,
  headerSkinLabelKey,
} from '../../lib/header-skins'
import { localizeError } from '../../i18n/errors'
import { TwoFactorAuthInline } from './TwoFactorAuthSection'
import { SettingsExchangeRatesSection } from './SettingsExchangeRatesSection'

const AVATAR_MAX_BYTES = 4 * 1024 * 1024 // 4 MB,跟 server 限制一致
const DISPLAY_NAME_MAX = 60

/** 特殊流水图标可选用的图标集合 —— 复用收入分组的 stored 值(Material Symbols),
 *  覆盖金融/流向语义(savings / swap_horiz / trending_up 等),足够表达七类流水。 */
const FLOW_ICON_CHOICES: { key: string; label: string }[] = INCOME_ICON_GROUPS.flatMap(
  (g) => g.icons,
)

/** 特殊流水图标可选用的底色(十六进制)。首项跟随各自默认色,其余为常用区分色。 */
const FLOW_ICON_COLOR_CHOICES: readonly string[] = [
  '#10b981', '#0ea5e9', '#14b8a6', '#f97316', '#f59e0b', '#a855f7',
  '#ef4444', '#ec4899', '#8b5cf6', '#6366f1', '#06b6d4', '#84cc16',
  '#64748b', '#d97706', '#059669', '#dc2626',
]

/** 七类特殊流水的展示名 i18n key(与 flowIcons.ts 的 FlowIconKey 一一对齐)。 */
const FLOW_ICON_LABEL_KEYS: Record<FlowIconKey, string> = {
  deposit_interest: 'profile.sync.flowIcon.type.depositInterest',
  transfer: 'profile.sync.flowIcon.type.transfer',
  loan_lend: 'profile.sync.flowIcon.type.loanLend',
  loan_borrow: 'profile.sync.flowIcon.type.loanBorrow',
  loan_collect: 'profile.sync.flowIcon.type.loanCollect',
  loan_repay: 'profile.sync.flowIcon.type.loanRepay',
  refund: 'profile.sync.flowIcon.type.refund',
}

// 主币种候选列表 —— 跟 mobile prefs `baseCurrency` 常见取值对齐。当前值不在列表时
// 会在下方动态补进去,保证旧值也能正常展示 / 选中。
const PRIMARY_CURRENCY_OPTIONS = [
  'CNY', 'USD', 'EUR', 'JPY', 'HKD', 'GBP', 'KRW', 'SGD',
  'AUD', 'CAD', 'TWD', 'THB', 'MYR', 'RUB', 'INR', 'CHF',
]

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
    return { key: 'profile.greeting.evening', icon: MoonStar, tone: 'text-violet-500' }
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
  const headerSkin = appearance.header_skin ?? 'none'
  const compactAmount = appearance.compact_amount ?? false
  const showTransactionTime = appearance.show_transaction_time ?? false
  const calendarLunarEnabled = appearance.calendar_lunar_enabled ?? true
  const calendarHolidayEnabled = appearance.calendar_holiday_enabled ?? true
  // 动态皮肤默认播放动效;关掉后 mobile 那边皮肤停在静态帧(省电)
  const skinAnimation = appearance.skin_animation ?? true
  const noteDisplayMode = appearance.note_display_mode ?? 'category'
  const [appearanceSaving, setAppearanceSaving] = useState(false)

  const saveAppearance = async (
    patch: Partial<NonNullable<typeof profileMe>['appearance']>,
  ) => {
    if (appearanceSaving) return
    setAppearanceSaving(true)
    try {
      await patchProfileMe(token, {
        // 整体替换语义:把 server 现有 appearance 全量带上再 patch,否则会清掉
        // mobile 设的 header_skin 等本页未直接管理的字段。
        appearance: { ...appearance, ...patch },
      })
      await refreshProfile()
      toast.success(t('profile.sync.appearanceSaved'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setAppearanceSaving(false)
    }
  }

  // ── 特殊流水图标 / 颜色自定义 ──────────────────────────────────────────
  // 转账 / 借款四态 / 退款 / 存款利息这些流水没有可编辑分类,图标此前硬编码,
  // 现允许用户在 appearance.flow_icons / appearance.flow_icon_colors 里逐项自定义。
  // 改任一项都走 saveAppearance 整体替换语义,写回 server 后 FlowIconsBridge 广播到全部渲染点。
  const flowIcons = appearance.flow_icons ?? {}
  const flowIconColors = appearance.flow_icon_colors ?? {}
  // 当前正在编辑图标 / 颜色的 flow key(null = 未打开选择器)
  const [flowIconPicker, setFlowIconPicker] = useState<FlowIconKey | null>(null)
  const [flowColorPicker, setFlowColorPicker] = useState<FlowIconKey | null>(null)

  const saveFlowIcon = (key: FlowIconKey, iconName: string) => {
    void saveAppearance({ flow_icons: { ...flowIcons, [key]: iconName } })
    setFlowIconPicker(null)
  }
  const resetFlowIcon = (key: FlowIconKey) => {
    const next = { ...flowIcons }
    delete next[key]
    void saveAppearance({ flow_icons: next })
  }
  const saveFlowColor = (key: FlowIconKey, color: string) => {
    void saveAppearance({ flow_icon_colors: { ...flowIconColors, [key]: color } })
    setFlowColorPicker(null)
  }
  const resetFlowColor = (key: FlowIconKey) => {
    const next = { ...flowIconColors }
    delete next[key]
    void saveAppearance({ flow_icon_colors: next })
  }

  // 切换皮肤。自带配色的皮肤(如周年蛋糕)在 mobile 上会把主题色
  // 锁成皮肤色,web 这边必须把同一个颜色也写回 server —— 否则 server 的
  // theme_primary_color 停在旧值,app 收到 appearance 广播时会先应用旧色再被本地
  // 绑定逻辑改回皮肤色,主题色在两个颜色之间闪。
  //
  // 顺序固定为「先颜色、后 appearance」:两者是 server 上两个字段、两次广播,
  // 先把颜色落地,对端收到 header_skin 变更时读到的颜色才是对的。
  const handleHeaderSkinChange = async (skinId: string) => {
    if (appearanceSaving || skinId === headerSkin) return
    const bound = boundPrimaryOf(skinId)
    setAppearanceSaving(true)
    try {
      if (bound) {
        await patchProfileMe(token, { theme_primary_color: bound })
      }
      await patchProfileMe(token, {
        appearance: { ...appearance, header_skin: skinId },
      })
      await refreshProfile()
      toast.success(t('profile.sync.appearanceSaved'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setAppearanceSaving(false)
    }
  }

  // 主币种(本位币)—— 资产折算目标,跟 mobile prefs `baseCurrency` 同步。
  const primaryCurrency = profileMe?.primary_currency || ''
  const [primaryCurrencySaving, setPrimaryCurrencySaving] = useState(false)
  // 候选列表 ∪ 当前值(旧值可能不在内置列表,补进去保证可选中)
  const currencyOptions = useMemo(() => {
    const set = [...PRIMARY_CURRENCY_OPTIONS]
    if (primaryCurrency && !set.includes(primaryCurrency)) set.unshift(primaryCurrency)
    return set
  }, [primaryCurrency])

  const handlePrimaryCurrencyChange = async (code: string) => {
    if (primaryCurrencySaving || code === primaryCurrency) return
    setPrimaryCurrencySaving(true)
    try {
      await patchProfileMe(token, { primary_currency: code })
      await refreshProfile()
      toast.success(t('notice.profileUpdated'))
    } catch (err) {
      toast.error(localizeError(err, t))
    } finally {
      setPrimaryCurrencySaving(false)
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
                    (Sunrise / Sun / MoonStar / Moon),配色 amber/orange/violet/indigo;
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
            {/* 皮肤 —— 选项来自 lib/header-skins.ts，与 mobile 的 kHeaderSkins
                一一对齐（周年 / 秋日 / 经典三组）。自带配色的皮肤会连同主题色
                一起写回 server，见 handleHeaderSkinChange。 */}
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.headerSkin')}
              </p>
              <Select
                value={headerSkin}
                onValueChange={(value) => void handleHeaderSkinChange(value)}
                disabled={appearanceSaving}
              >
                <SelectTrigger className="mt-1 h-8 border-0 bg-transparent px-0 text-sm font-medium shadow-none focus:ring-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('profile.sync.headerSkin.none')}</SelectItem>
                  {HEADER_SKIN_GROUP_ORDER.map((group) => (
                    <SelectGroup key={group}>
                      <SelectLabel>{t(headerSkinGroupLabelKey(group))}</SelectLabel>
                      {HEADER_SKINS.filter((s) => s.group === group).map((skin) => (
                        <SelectItem key={skin.id} value={skin.id}>
                          <span className="flex items-center gap-1.5">
                            {t(headerSkinLabelKey(skin.id))}
                            {skin.animated ? (
                              <span className="rounded-sm bg-primary/12 px-1 text-[9px] font-medium leading-4 text-primary">
                                {t('profile.sync.headerSkin.animated')}
                              </span>
                            ) : null}
                            {skin.boundPrimary ? (
                              <span
                                aria-label={t('profile.sync.headerSkin.boundPalette')}
                                title={t('profile.sync.headerSkin.boundPalette')}
                                className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-border"
                                style={{ backgroundColor: skin.boundPrimary }}
                              />
                            ) : null}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
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
            {/* 备注显示方式:下拉 — category(分类优先) / note(备注优先) */}
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.noteDisplay')}
              </p>
              <Select
                value={noteDisplayMode}
                onValueChange={(value) =>
                  void saveAppearance({ note_display_mode: value as 'category' | 'note' })
                }
                disabled={appearanceSaving}
              >
                <SelectTrigger className="mt-1 h-8 border-0 bg-transparent px-0 text-sm font-medium shadow-none focus:ring-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="category">{t('profile.sync.noteDisplay.category')}</SelectItem>
                  <SelectItem value="note">{t('profile.sync.noteDisplay.note')}</SelectItem>
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
            {/* 日历农历显示 */}
            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.calendarLunar')}
              </p>
              <button
                type="button"
                role="switch"
                aria-checked={calendarLunarEnabled}
                aria-label={t('profile.sync.calendarLunar') as string}
                disabled={appearanceSaving}
                onClick={() =>
                  void saveAppearance({ calendar_lunar_enabled: !calendarLunarEnabled })
                }
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  calendarLunarEnabled ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    calendarLunarEnabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
            {/* 日历节假日显示 */}
            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.calendarHoliday')}
              </p>
              <button
                type="button"
                role="switch"
                aria-checked={calendarHolidayEnabled}
                aria-label={t('profile.sync.calendarHoliday') as string}
                disabled={appearanceSaving}
                onClick={() =>
                  void saveAppearance({ calendar_holiday_enabled: !calendarHolidayEnabled })
                }
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  calendarHolidayEnabled ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    calendarHolidayEnabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
            {/* 皮肤动效:关掉后 mobile 的动态皮肤停在静态帧 —— 那些皮肤是持续
                重绘的,长时间用会发烫,这是给用户的省电出口。web 不渲染皮肤,
                这里只负责把开关写回 server。 */}
            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('profile.sync.skinAnimation')}
              </p>
              <button
                type="button"
                role="switch"
                aria-checked={skinAnimation}
                aria-label={t('profile.sync.skinAnimation') as string}
                disabled={appearanceSaving}
                onClick={() => void saveAppearance({ skin_animation: !skinAnimation })}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  skinAnimation ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    skinAnimation ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>

          {/* 主币种(本位币)—— 资产折算目标,与 mobile prefs baseCurrency 同步。
              空值时占位显示「未设置」;旧值不在内置列表会被 currencyOptions 补上。 */}
          <div className="rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">{t('settings.primaryCurrency')}</p>
                <p className="text-xs text-muted-foreground">
                  {t('settings.primaryCurrency.hint')}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {primaryCurrencySaving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                ) : null}
                <Select
                  value={primaryCurrency}
                  onValueChange={(value) => void handlePrimaryCurrencyChange(value)}
                  disabled={primaryCurrencySaving}
                >
                  <SelectTrigger className="h-8 w-[140px] text-sm">
                    <SelectValue placeholder={t('settings.primaryCurrency.unset')} />
                  </SelectTrigger>
                  <SelectContent>
                    {currencyOptions.map((code) => (
                      <SelectItem key={code} value={code}>
                        {code} · {t(`currency.${code}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          {/* 特殊流水图标 —— 转账/借款四态/退款/存款利息可逐项自定义图标与底色。
              写回 appearance.flow_icons / flow_icon_colors,FlowIconsBridge 广播到所有渲染点。 */}
          <div className="rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
            <div className="mb-3">
              <p className="text-sm font-medium">{t('profile.sync.flowIcon.title')}</p>
              <p className="text-xs text-muted-foreground">
                {t('profile.sync.flowIcon.hint')}
              </p>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
              {FLOW_ICON_KEYS.map((key) => {
                const iconName = resolveFlowIcon(key, flowIcons)
                const color = resolveFlowIconColor(key, flowIconColors)
                const isDefault =
                  iconName === DEFAULT_FLOW_ICONS[key] &&
                  color === resolveFlowIconColor(key, {})
                return (
                  <div
                    key={key}
                    className="flex items-center gap-2 rounded-md border border-border/40 bg-background/40 px-2.5 py-2"
                  >
                    <span
                      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
                      style={{ backgroundColor: `${color}26`, color }}
                    >
                      <CategoryIcon icon={iconName} iconType="material" size={18} color={color} />
                    </span>
                    <span
                      className="min-w-0 flex-1 truncate text-xs font-medium"
                      title={t(FLOW_ICON_LABEL_KEYS[key]) as string}
                    >
                      {t(FLOW_ICON_LABEL_KEYS[key])}
                    </span>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => setFlowIconPicker(key)}
                        disabled={appearanceSaving}
                      >
                        {t('profile.sync.flowIcon.change')}
                      </Button>
                      <button
                        type="button"
                        onClick={() => setFlowColorPicker(key)}
                        disabled={appearanceSaving}
                        className="h-7 w-7 shrink-0 rounded-full border border-border/60 shadow-sm transition hover:scale-105 disabled:cursor-not-allowed disabled:opacity-60"
                        style={{ backgroundColor: color }}
                        title={t('profile.sync.flowIcon.color') as string}
                        aria-label={t('profile.sync.flowIcon.color') as string}
                      />
                      {!isDefault ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 w-7 px-0 text-xs text-muted-foreground"
                          onClick={() => {
                            resetFlowIcon(key)
                            resetFlowColor(key)
                          }}
                          disabled={appearanceSaving}
                          title={t('profile.sync.flowIcon.reset') as string}
                          aria-label={t('profile.sync.flowIcon.reset') as string}
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                        </Button>
                      ) : null}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 图标选择器 —— 复用收入分组图标网格(Material Symbols)。 */}
      <Dialog
        open={flowIconPicker !== null}
        onOpenChange={(open) => {
          if (!open) setFlowIconPicker(null)
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {flowIconPicker ? t(FLOW_ICON_LABEL_KEYS[flowIconPicker]) : ''} ·{' '}
              {t('profile.sync.flowIcon.change')}
            </DialogTitle>
            <DialogDescription>{t('profile.sync.flowIcon.changeHint')}</DialogDescription>
          </DialogHeader>
          <div className="grid max-h-[50vh] grid-cols-6 gap-2 overflow-y-auto sm:grid-cols-8">
            {FLOW_ICON_CHOICES.map((item) => {
              const active = flowIconPicker
                ? resolveFlowIcon(flowIconPicker, flowIcons) === item.key
                : false
              const color = flowIconPicker
                ? resolveFlowIconColor(flowIconPicker, flowIconColors)
                : undefined
              return (
                <button
                  key={item.key}
                  type="button"
                  title={item.label}
                  onClick={() => {
                    if (flowIconPicker) saveFlowIcon(flowIconPicker, item.key)
                  }}
                  className={`flex aspect-square items-center justify-center rounded-md border transition ${
                    active
                      ? 'border-primary bg-primary/10'
                      : 'border-border/60 hover:bg-muted'
                  }`}
                  style={active && color ? { color } : undefined}
                >
                  <CategoryIcon
                    icon={item.key}
                    iconType="material"
                    size={20}
                    color={active && color ? color : undefined}
                  />
                </button>
              )
            })}
          </div>
        </DialogContent>
      </Dialog>

      {/* 颜色选择器 —— 常用区分色网格。 */}
      <Dialog
        open={flowColorPicker !== null}
        onOpenChange={(open) => {
          if (!open) setFlowColorPicker(null)
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {flowColorPicker ? t(FLOW_ICON_LABEL_KEYS[flowColorPicker]) : ''} ·{' '}
              {t('profile.sync.flowIcon.color')}
            </DialogTitle>
            <DialogDescription>{t('profile.sync.flowIcon.colorHint')}</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-8 gap-2">
            {FLOW_ICON_COLOR_CHOICES.map((c) => {
              const active = flowColorPicker
                ? resolveFlowIconColor(flowColorPicker, flowIconColors) === c
                : false
              return (
                <button
                  key={c}
                  type="button"
                  onClick={() => {
                    if (flowColorPicker) saveFlowColor(flowColorPicker, c)
                  }}
                  className={`h-8 w-8 rounded-full border-2 transition ${
                    active ? 'scale-110 border-foreground' : 'border-border/60 hover:scale-105'
                  }`}
                  style={{ backgroundColor: c }}
                  title={c}
                  aria-label={c}
                />
              )
            })}
          </div>
        </DialogContent>
      </Dialog>

      {/* 汇率管理小节 —— 主币种未设置时内部渲染空态、不发请求 */}
      <SettingsExchangeRatesSection />
    </div>
  )
}
