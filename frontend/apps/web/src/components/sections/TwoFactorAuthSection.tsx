import { useEffect, useMemo, useState } from 'react'

import {
  ApiError,
  confirmTwoFA,
  disableTwoFA,
  fetchTwoFAStatus,
  regenerateRecoveryCodes,
  setupTwoFA
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT
} from '@beecount/ui'
import QRCode from 'qrcode'

import { useAuth } from '../../context/AuthContext'

type Status = { enabled: boolean; enabled_at: string | null } | null

/**
 * 个人资料页「二次验证」卡片。
 *
 * 状态:
 * - 未启用 → "为账号添加额外保护" + [启用] 按钮
 * - 已启用 → "已启用 ✓ · 启用于 YYYY-MM-DD" + [重新生成 recovery code] [禁用] 按钮
 *
 * 子 dialog:
 * - SetupDialog:三步向导(QR → 输码 confirm → 显示 10 个 recovery codes)
 * - DisableDialog:密码 + 6 位码双重确认
 * - RegenerateDialog:6 位码 → 显示新 10 个
 *
 * 设计文档:.docs/2fa-design.md(第 4.7 节)
 */
export function TwoFactorAuthSection() {
  const t = useT()
  const { token } = useAuth()
  const [status, setStatus] = useState<Status>(null)
  const [loading, setLoading] = useState(true)
  const [setupOpen, setSetupOpen] = useState(false)
  const [disableOpen, setDisableOpen] = useState(false)
  const [regenerateOpen, setRegenerateOpen] = useState(false)

  const reload = useMemo(
    () => async () => {
      setLoading(true)
      try {
        const s = await fetchTwoFAStatus(token)
        setStatus(s)
      } finally {
        setLoading(false)
      }
    },
    [token]
  )

  useEffect(() => {
    void reload()
  }, [reload])

  const enabledLabel = status?.enabled
    ? `${t('twofa.status.enabled')} ✓`
    : t('twofa.status.disabled')

  const enabledAtLabel = status?.enabled_at
    ? new Date(status.enabled_at).toLocaleDateString()
    : null

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('twofa.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">{t('twofa.desc')}</p>

        <div className="flex items-center gap-2 text-sm">
          <span
            className={
              status?.enabled
                ? 'rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-300'
                : 'rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground'
            }
          >
            {loading ? t('common.loading') : enabledLabel}
          </span>
          {enabledAtLabel && (
            <span className="text-xs text-muted-foreground">
              {t('twofa.enabledAt', { date: enabledAtLabel })}
            </span>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          {!status?.enabled && (
            <Button size="sm" onClick={() => setSetupOpen(true)} disabled={loading}>
              {t('twofa.action.enable')}
            </Button>
          )}
          {status?.enabled && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setRegenerateOpen(true)}
              >
                {t('twofa.action.regenerate')}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => setDisableOpen(true)}
              >
                {t('twofa.action.disable')}
              </Button>
            </>
          )}
        </div>
      </CardContent>

      <SetupDialog
        open={setupOpen}
        onOpenChange={(v) => {
          setSetupOpen(v)
          if (!v) void reload()
        }}
      />
      <DisableDialog
        open={disableOpen}
        onOpenChange={(v) => {
          setDisableOpen(v)
          if (!v) void reload()
        }}
      />
      <RegenerateDialog
        open={regenerateOpen}
        onOpenChange={(v) => {
          setRegenerateOpen(v)
        }}
      />
    </Card>
  )
}

// ---------------- Setup wizard ----------------

type SetupStep = 'qr' | 'confirm' | 'codes'

function SetupDialog({
  open,
  onOpenChange
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const t = useT()
  const { token } = useAuth()
  const [step, setStep] = useState<SetupStep>('qr')
  const [secret, setSecret] = useState('')
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([])
  const [savedAck, setSavedAck] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // 打开 dialog → 调 setup 拿 secret + qr
  useEffect(() => {
    if (!open) {
      // 关闭时重置
      setStep('qr')
      setSecret('')
      setQrDataUrl(null)
      setCode('')
      setRecoveryCodes([])
      setSavedAck(false)
      setErr(null)
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setErr(null)
    void (async () => {
      try {
        const data = await setupTwoFA(token)
        if (cancelled) return
        setSecret(data.secret)
        const dataUrl = await QRCode.toDataURL(data.qr_code_uri, { margin: 1, width: 220 })
        if (cancelled) return
        setQrDataUrl(dataUrl)
      } catch (e) {
        if (!cancelled) setErr(e instanceof ApiError ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, token])

  const onConfirm = async () => {
    setLoading(true)
    setErr(null)
    try {
      const result = await confirmTwoFA(token, code.replace(/\s+/g, ''))
      setRecoveryCodes(result.recovery_codes)
      setStep('codes')
      setCode('')
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('twofa.setup.title')}</DialogTitle>
          <DialogDescription>
            {step === 'qr' && t('twofa.setup.qrHint')}
            {step === 'confirm' && t('twofa.setup.confirmHint')}
            {step === 'codes' && t('twofa.setup.codesHint')}
          </DialogDescription>
        </DialogHeader>

        {step === 'qr' && (
          <div className="space-y-3">
            {loading && <p className="text-sm text-muted-foreground">{t('common.loading')}</p>}
            {qrDataUrl && (
              <div className="flex flex-col items-center gap-3">
                <img
                  alt="2FA QR Code"
                  src={qrDataUrl}
                  className="rounded-md border border-border bg-white p-2"
                />
                <div className="w-full space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t('twofa.setup.manualSecret')}
                  </Label>
                  <code className="block break-all rounded bg-muted px-2 py-1 text-xs">
                    {secret}
                  </code>
                </div>
              </div>
            )}
            {err && <p className="text-sm text-destructive">{err}</p>}
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              <Button onClick={() => setStep('confirm')} disabled={!qrDataUrl}>
                {t('common.next')}
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 'confirm' && (
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="totp-code">{t('twofa.setup.codeLabel')}</Label>
              <Input
                id="totp-code"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                autoFocus
              />
            </div>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <DialogFooter>
              <Button variant="outline" onClick={() => setStep('qr')}>
                {t('common.back')}
              </Button>
              <Button onClick={onConfirm} disabled={code.length !== 6 || loading}>
                {t('twofa.setup.verify')}
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 'codes' && (
          <div className="space-y-3">
            <div className="rounded-md border border-amber-500/40 bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">
              {t('twofa.setup.codesWarning')}
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-muted/40 p-3 font-mono text-sm">
              {recoveryCodes.map((c) => (
                <code key={c} className="rounded bg-background px-2 py-1">
                  {c}
                </code>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  void navigator.clipboard.writeText(recoveryCodes.join('\n'))
                }}
              >
                {t('twofa.setup.copyAll')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  const blob = new Blob(
                    [
                      `BeeCount Recovery Codes\nGenerated: ${new Date().toISOString()}\n\n${recoveryCodes.join(
                        '\n'
                      )}\n`
                    ],
                    { type: 'text/plain' }
                  )
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = 'beecount-recovery-codes.txt'
                  a.click()
                  URL.revokeObjectURL(url)
                }}
              >
                {t('twofa.setup.download')}
              </Button>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={savedAck}
                onChange={(e) => setSavedAck(e.target.checked)}
              />
              {t('twofa.setup.savedAck')}
            </label>
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)} disabled={!savedAck}>
                {t('common.done')}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ---------------- Disable ----------------

function DisableDialog({
  open,
  onOpenChange
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const t = useT()
  const { token } = useAuth()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setPassword('')
      setCode('')
      setErr(null)
      setLoading(false)
    }
  }, [open])

  const onSubmit = async () => {
    setLoading(true)
    setErr(null)
    try {
      await disableTwoFA(token, password, code.replace(/\s+/g, ''))
      onOpenChange(false)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('twofa.disable.title')}</DialogTitle>
          <DialogDescription>{t('twofa.disable.desc')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="disable-password">{t('twofa.disable.password')}</Label>
            <Input
              id="disable-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="disable-code">{t('twofa.disable.code')}</Label>
            <Input
              id="disable-code"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              placeholder="000000"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            />
          </div>
          {err && <p className="text-sm text-destructive">{err}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={onSubmit}
              disabled={!password || code.length !== 6 || loading}
            >
              {t('twofa.action.disable')}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------- Regenerate ----------------

function RegenerateDialog({
  open,
  onOpenChange
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const t = useT()
  const { token } = useAuth()
  const [code, setCode] = useState('')
  const [newCodes, setNewCodes] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setCode('')
      setNewCodes([])
      setErr(null)
      setLoading(false)
    }
  }, [open])

  const onSubmit = async () => {
    setLoading(true)
    setErr(null)
    try {
      const r = await regenerateRecoveryCodes(token, code.replace(/\s+/g, ''))
      setNewCodes(r.recovery_codes)
      setCode('')
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('twofa.regenerate.title')}</DialogTitle>
          <DialogDescription>{t('twofa.regenerate.desc')}</DialogDescription>
        </DialogHeader>
        {newCodes.length === 0 && (
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="regen-code">{t('twofa.disable.code')}</Label>
              <Input
                id="regen-code"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                autoFocus
              />
            </div>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              <Button onClick={onSubmit} disabled={code.length !== 6 || loading}>
                {t('twofa.regenerate.submit')}
              </Button>
            </DialogFooter>
          </div>
        )}

        {newCodes.length > 0 && (
          <div className="space-y-3">
            <div className="rounded-md border border-amber-500/40 bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">
              {t('twofa.setup.codesWarning')}
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-muted/40 p-3 font-mono text-sm">
              {newCodes.map((c) => (
                <code key={c} className="rounded bg-background px-2 py-1">
                  {c}
                </code>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  void navigator.clipboard.writeText(newCodes.join('\n'))
                }}
              >
                {t('twofa.setup.copyAll')}
              </Button>
            </div>
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>{t('common.done')}</Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
