import { FormEvent, useState } from 'react'

import { ApiError, login } from '@beecount/api-client'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
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
  const [notice, setNotice] = useState<{ type: 'default' | 'destructive'; title: string; message: string } | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
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
    }
  }

  return (
    <div className="min-h-screen bg-background px-4 pb-8 pt-10 text-foreground">
      <div className="grid w-full gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <Card className="border-border/70 bg-card/95 shadow-sm">
          <CardHeader className="space-y-3">
            <div className="inline-flex w-fit rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
              {t('app.brand')}
            </div>
            <CardTitle className="text-3xl font-bold">{t('login.title')}</CardTitle>
            <CardDescription className="text-base">{t('login.subtitle')}</CardDescription>
            <div className="flex flex-wrap items-center gap-2 pt-2">
              <LanguageToggle />
              <ThemeToggle />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <form className="grid gap-3" onSubmit={onSubmit}>
              <div className="space-y-2">
                <Label htmlFor="login-email">{t('login.email')}</Label>
                <Input
                  id="login-email"
                  autoComplete="email"
                  placeholder="owner@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="login-password">{t('login.password')}</Label>
                <Input
                  id="login-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div className="flex justify-end">
                <Button className="min-w-28" type="submit">
                  {t('login.submit')}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <Card className="border-border/70 bg-card/95 shadow-sm">
          <CardHeader>
            <CardTitle>{t('login.dev.title')}</CardTitle>
            <CardDescription>{t('app.subtitle')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>{t('login.dev.step1')}</p>
            <p>{t('login.dev.step2')}</p>
            <p>{t('login.dev.step3')}</p>
            <p>{t('login.dev.troubleshoot')}</p>
          </CardContent>
        </Card>

        {notice && (
          <Alert className="lg:col-span-2" variant={notice.type}>
            <AlertTitle>{notice.title}</AlertTitle>
            <AlertDescription>{notice.message}</AlertDescription>
          </Alert>
        )}
      </div>
    </div>
  )
}
