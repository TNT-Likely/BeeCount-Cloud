import { FormEvent, useEffect, useState } from 'react'
import { HelpCircle, X } from 'lucide-react'

import {
  ApiError,
  detectWebClientInfo,
  getStoredDeviceId,
  login,
  verifyTwoFA
} from '@beecount/api-client'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Input,
  Label,
  LanguageToggle,
  ThemeToggle,
  useT
} from '@beecount/ui'
import { localizeError } from '../i18n/errors'

const HINT_DISMISSED_KEY = 'login_initial_password_hint_dismissed'

type LoginPageProps = {
  onLoggedIn: (token: string) => void
}

type ChallengeState = {
  challenge_token: string
  available_methods: Array<'totp' | 'recovery_code'>
}

export function LoginPage({ onLoggedIn }: LoginPageProps) {
  const t = useT()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<{ type: 'default' | 'destructive'; title: string; message: string } | null>(null)
  const [challenge, setChallenge] = useState<ChallengeState | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    try {
      const data = await login(email, password)
      if (data.requires_2fa && data.challenge_token) {
        // 切到 2FA 输码视图,留住表单内容(用户失败重试时不必重输邮密)
        setChallenge({
          challenge_token: data.challenge_token,
          available_methods: data.available_methods || ['totp', 'recovery_code']
        })
        setNotice(null)
        return
      }
      if (data.access_token) {
        onLoggedIn(data.access_token)
        setNotice(null)
      }
    } catch (err) {
      const message = localizeError(err, t)
      if (err instanceof ApiError && err.code === 'AUTH_INVALID_CREDENTIALS') {
        setNotice({
          type: 'destructive',
          title: t('notice.failed'),
          message: t('login.error.invalid')
        })
        return
      }
      setNotice({
        type: 'destructive',
        title: t('notice.failed'),
        message
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      {/* 背景：品牌色渐变 + 两坨模糊光斑，给登录页一个有"场"的底色，不再是
          纯平背景。dark mode 下依然好看因为用的是 hsl CSS 变量。 */}
      <div
        className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/15 via-primary/5 to-transparent"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -left-32 top-1/4 h-96 w-96 rounded-full bg-primary/20 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -right-24 bottom-1/4 h-80 w-80 rounded-full bg-secondary/20 blur-3xl"
        aria-hidden
      />

      {/* 右上角工具栏 */}
      <div className="absolute right-4 top-4 flex items-center gap-2">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-6xl items-center justify-center px-4 py-10">
        <div className="grid w-full gap-8 lg:grid-cols-[1fr_minmax(0,420px)] lg:items-center">
          {/* 左：品牌叙事 */}
          <div className="hidden space-y-6 lg:block">
            <div className="flex items-center gap-3">
              <img src="/branding/logo.svg" alt={t('shell.appName')} className="h-12 w-12" />
              <div>
                <div className="text-2xl font-bold">{t('app.brand')}</div>
                <div className="text-sm text-muted-foreground">{t('app.subtitle')}</div>
              </div>
            </div>
            <h1 className="text-4xl font-bold leading-tight tracking-tight">
              <span className="bg-gradient-to-br from-primary to-primary/60 bg-clip-text text-transparent">
                {t('login.heroHighlight')}
              </span>
              <span>{t('login.heroTail')}</span>
            </h1>
            <p className="text-base text-muted-foreground">
              {t('login.subtitle')}
            </p>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">{t('login.feature.syncTitle')}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {t('login.feature.syncDesc')}
                </div>
              </div>
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">{t('login.feature.selfHostTitle')}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {t('login.feature.selfHostDesc')}
                </div>
              </div>
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">{t('login.feature.multiTitle')}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {t('login.feature.multiDesc')}
                </div>
              </div>
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">{t('login.feature.freeTitle')}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {t('login.feature.freeDesc')}
                </div>
              </div>
            </div>
          </div>

          {/* 右：登录表单卡 */}
          <div className="w-full">
            <div className="rounded-2xl border border-border/60 bg-card/90 p-8 shadow-xl backdrop-blur-md">
              <div className="mb-6 flex items-center gap-3 lg:hidden">
                <img src="/branding/logo.svg" alt="" className="h-10 w-10" />
                <div>
                  <div className="text-lg font-bold">{t('app.brand')}</div>
                  <div className="text-xs text-muted-foreground">{t('app.subtitle')}</div>
                </div>
              </div>
              <div className="mb-6 space-y-1">
                <div className="inline-flex w-fit rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                  {t('app.brand')}
                </div>
                <h2 className="mt-2 text-2xl font-bold">{t('login.title')}</h2>
              </div>

              {challenge ? (
                <TwoFactorChallengeView
                  challenge={challenge}
                  onCancel={() => {
                    setChallenge(null)
                    setNotice(null)
                  }}
                  onVerified={(accessToken) => {
                    onLoggedIn(accessToken)
                  }}
                />
              ) : (
                <form className="space-y-4" onSubmit={onSubmit}>
                  <div className="space-y-1.5">
                    <Label htmlFor="login-email">{t('login.email')}</Label>
                    <Input
                      id="login-email"
                      autoComplete="email"
                      placeholder="owner@example.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <Label htmlFor="login-password">{t('login.password')}</Label>
                      {/* (?) 图标 hover 出 hint —— 即便用户关掉了下方提示卡,仍
                          能从这里查到初始密码出处。Tooltip 仅是 native title,
                          需要包裹 button(span 上的 title 在某些浏览器里 SVG
                          区域不触发 hover);同时点击 toggle 出可见浮窗作为
                          兜底,移动端 / 触屏没有 hover 时也能查看。 */}
                      <PasswordHintTrigger />
                    </div>
                    <Input
                      id="login-password"
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </div>
                  {/* 初次部署的用户经常不知道初始密码哪里来 — 自部署 server 在
                      首次启动时会把 admin 账号 + 密码打到 docker log,同时
                      也写入容器内 `var/initial_admin_password` 文件。提示卡
                      显示在密码框下方,带 × 关闭按钮,关闭后写 localStorage,
                      下次不再显示。 */}
                  <InitialPasswordHint />
                  <Button className="w-full" type="submit" disabled={loading}>
                    {loading ? '…' : t('login.submit')}
                  </Button>
                </form>
              )}

              {notice && (
                <Alert className="mt-4" variant={notice.type}>
                  <AlertTitle>{notice.title}</AlertTitle>
                  <AlertDescription>{notice.message}</AlertDescription>
                </Alert>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * (?) 图标 — hover 显示 native title;点击 toggle 一个临时浮窗,移动端没
 * hover 也能看;再点别处 / 自身关闭。比 Tooltip 组件可靠,因为后者只是 span
 * 的 title 在 SVG hit area 上某些浏览器不一定触发。
 */
function PasswordHintTrigger() {
  const t = useT()
  const [open, setOpen] = useState(false)
  const hint = t('login.initialPasswordHint') as string

  // 点弹窗外部 / Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onDoc = () => setOpen(false)
    window.addEventListener('keydown', onKey)
    // 用 setTimeout + capture=false,避免本次点击直接被 onDoc 捕获关掉
    const tid = window.setTimeout(() => document.addEventListener('click', onDoc), 0)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('click', onDoc)
      window.clearTimeout(tid)
    }
  }, [open])

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        title={hint}
        aria-label={hint}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        className="rounded-full p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
      >
        <HelpCircle className="h-3.5 w-3.5" />
      </button>
      {open ? (
        <span
          role="tooltip"
          onClick={(e) => e.stopPropagation()}
          className="absolute left-1/2 top-full z-10 mt-1.5 w-[280px] -translate-x-1/2 rounded-md border border-border/60 bg-popover px-3 py-2 text-[11px] leading-relaxed text-foreground shadow-lg"
        >
          {hint}
        </span>
      ) : null}
    </span>
  )
}

/**
 * 初始密码引导 —— 自部署 server 首次启动时,admin 账号 + 密码会:
 *  1) 打到 docker-compose / docker logs 输出
 *  2) 写到容器 `var/initial_admin_password` 文件
 *
 * 很多用户初次访问 web 不知道这件事,反复来问"哪里注册"。这条提示只显示
 * 在登录态(2FA challenge 切走后不显示),× 关闭后写 localStorage,下次
 * 访问不再展示。即便关掉,密码 label 旁的 (?) 图标 hover 仍能看见同款
 * 文案,信息不会彻底丢失。
 */
function InitialPasswordHint() {
  const t = useT()
  const [dismissed, setDismissed] = useState(true)

  useEffect(() => {
    if (typeof window === 'undefined') return
    setDismissed(window.localStorage.getItem(HINT_DISMISSED_KEY) === '1')
  }, [])

  if (dismissed) return null

  const close = () => {
    setDismissed(true)
    try {
      window.localStorage.setItem(HINT_DISMISSED_KEY, '1')
    } catch {
      // 用户禁了 localStorage —— 本次会话内消失也算
    }
  }

  return (
    <div className="relative rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 pr-8 text-[11px] leading-relaxed text-foreground">
      <button
        type="button"
        onClick={close}
        className="absolute right-1.5 top-1.5 rounded p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        aria-label={t('common.close') as string}
      >
        <X className="h-3 w-3" />
      </button>
      {t('login.initialPasswordHint')}
    </div>
  )
}

/**
 * 2FA challenge view - login 拿到 requires_2fa=true 后切到这个表单。
 *
 * 倒计时:5 分钟从 challenge_token 签发起算;到点提示用户重新登录(后端的
 * challenge_token 实际 exp 也是 5 分钟,这里只是 UI 提示,过期后 verify 会
 * 抛 401,我们 fallback 让用户重 login)。
 */
function TwoFactorChallengeView({
  challenge,
  onCancel,
  onVerified
}: {
  challenge: ChallengeState
  onCancel: () => void
  onVerified: (accessToken: string) => void
}) {
  const t = useT()
  const [method, setMethod] = useState<'totp' | 'recovery_code'>('totp')
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setErr(null)
    try {
      const data = await verifyTwoFA({
        challenge_token: challenge.challenge_token,
        method,
        code: code.trim().replace(/\s+/g, ''),
        device_id: getStoredDeviceId(),
        client_info: detectWebClientInfo()
      })
      if (data.access_token) {
        onVerified(data.access_token)
      }
    } catch (e) {
      const message = e instanceof ApiError ? e.message : String(e)
      setErr(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      <div className="space-y-1.5">
        <Label>{t('login.twofa.method')}</Label>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant={method === 'totp' ? 'default' : 'outline'}
            onClick={() => {
              setMethod('totp')
              setCode('')
            }}
          >
            {t('login.twofa.methodTotp')}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={method === 'recovery_code' ? 'default' : 'outline'}
            onClick={() => {
              setMethod('recovery_code')
              setCode('')
            }}
          >
            {t('login.twofa.methodRecovery')}
          </Button>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="login-2fa-code">
          {method === 'totp' ? t('login.twofa.totpLabel') : t('login.twofa.recoveryLabel')}
        </Label>
        <Input
          id="login-2fa-code"
          inputMode={method === 'totp' ? 'numeric' : 'text'}
          autoComplete="one-time-code"
          maxLength={method === 'totp' ? 6 : 16}
          placeholder={method === 'totp' ? '000000' : 'xxxx-xxxx'}
          value={code}
          onChange={(e) =>
            setCode(method === 'totp' ? e.target.value.replace(/\D/g, '') : e.target.value)
          }
          autoFocus
        />
      </div>
      {err && <p className="text-sm text-destructive">{err}</p>}
      <div className="flex gap-2">
        <Button type="button" variant="outline" onClick={onCancel} className="flex-1">
          {t('common.cancel')}
        </Button>
        <Button
          type="submit"
          disabled={loading || (method === 'totp' ? code.length !== 6 : code.length < 6)}
          className="flex-1"
        >
          {loading ? '…' : t('login.twofa.submit')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t('login.twofa.hint')}</p>
    </form>
  )
}
