import { FormEvent, useState } from 'react'

import { ApiError, login } from '@beecount/api-client'
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

type LoginPageProps = {
  onLoggedIn: (token: string) => void
}

export function LoginPage({ onLoggedIn }: LoginPageProps) {
  const t = useT()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<{ type: 'default' | 'destructive'; title: string; message: string } | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    try {
      const data = await login(email, password)
      onLoggedIn(data.access_token)
      setNotice(null)
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
              <img src="/branding/logo.svg" alt="蜜蜂记账" className="h-12 w-12" />
              <div>
                <div className="text-2xl font-bold">{t('app.brand')}</div>
                <div className="text-sm text-muted-foreground">{t('app.subtitle')}</div>
              </div>
            </div>
            <h1 className="text-4xl font-bold leading-tight tracking-tight">
              <span className="bg-gradient-to-br from-primary to-primary/60 bg-clip-text text-transparent">
                自部署
              </span>
              <span>的个人记账云</span>
            </h1>
            <p className="text-base text-muted-foreground">
              {t('login.subtitle')}
            </p>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">双向实时同步</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  手机 / 网页改动 2 秒内到达对端
                </div>
              </div>
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">完全自持数据</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  数据库 / 文件都在你自己的服务器
                </div>
              </div>
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">多端兼顾</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  iOS / Android / Web 同一套账本
                </div>
              </div>
              <div className="rounded-xl border border-border/50 bg-card/60 p-3 backdrop-blur">
                <div className="font-semibold">零订阅费</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  无广告、无内购、无外部依赖
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
                  <Label htmlFor="login-password">{t('login.password')}</Label>
                  <Input
                    id="login-password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
                <Button className="w-full" type="submit" disabled={loading}>
                  {loading ? '…' : t('login.submit')}
                </Button>
              </form>

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
