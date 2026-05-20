import { useCallback, useState } from 'react'
import { Loader2 } from 'lucide-react'

import {
  acceptLedgerInvite,
  previewLedgerInvite,
  type LedgerInvitePreview,
} from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT,
  useToast,
} from '@beecount/ui'
import { localizeError } from '../i18n/errors'

import { useAuth } from '../context/AuthContext'
import { useLedgers } from '../context/LedgersContext'

interface Props {
  open: boolean
  onOpenChange: (next: boolean) => void
}

/**
 * 加入共享账本对话框 — 输 6 位邀请码 → preview 显示账本信息 → accept。
 *
 * 对齐 mobile lib/pages/cloud/join_shared_ledger_page.dart 流程。
 * accept 成功后调 refreshLedgers 拉新账本到 sidebar。
 */
export function JoinSharedLedgerDialog({ open, onOpenChange }: Props) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { refreshLedgers, setActiveLedgerId } = useLedgers()
  const [code, setCode] = useState('')
  const [preview, setPreview] = useState<LedgerInvitePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [accepting, setAccepting] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  const reset = useCallback(() => {
    setCode('')
    setPreview(null)
    setLoading(false)
    setAccepting(false)
  }, [])

  const onPreview = useCallback(async () => {
    const normalized = code.trim().toUpperCase().replace(/[\s-]/g, '')
    if (!normalized) {
      notifyError(new Error(t('sharedLedger.codeRequired')))
      return
    }
    setLoading(true)
    try {
      const p = await previewLedgerInvite(token, normalized)
      setPreview(p)
    } catch (err) {
      notifyError(err)
      setPreview(null)
    } finally {
      setLoading(false)
    }
  }, [code, token, t, notifyError])

  const onAccept = useCallback(async () => {
    if (!preview) return
    setAccepting(true)
    try {
      const result = await acceptLedgerInvite(token, preview.code)
      notifySuccess(
        t('sharedLedger.joinedSuccess', { ledger: result.ledger_name || '' }),
      )
      await refreshLedgers()
      // 切到刚加入的账本,UI 立即看到新数据
      setActiveLedgerId(result.ledger_external_id)
      reset()
      onOpenChange(false)
    } catch (err) {
      notifyError(err)
    } finally {
      setAccepting(false)
    }
  }, [
    preview,
    token,
    t,
    notifyError,
    notifySuccess,
    refreshLedgers,
    setActiveLedgerId,
    reset,
    onOpenChange,
  ])

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>🤝 {t('sharedLedger.joinTitle')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="invite-code" className="mb-1 text-xs">
              {t('sharedLedger.inviteCodeLabel')}
            </Label>
            <Input
              id="invite-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="ABC 123"
              className="font-mono text-lg uppercase tracking-wider"
              autoFocus
              disabled={!!preview || accepting}
            />
            <p className="mt-1 text-xs text-muted-foreground">
              {t('sharedLedger.inviteCodeHint')}
            </p>
          </div>

          {preview ? (
            <div className="rounded border border-primary/30 bg-primary/5 p-3 text-sm">
              <div className="font-semibold">
                {preview.ledger_name || t('sharedLedger.untitled')}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {t('sharedLedger.inviteFrom')}: {preview.invited_by_display}
              </div>
              <div className="text-xs text-muted-foreground">
                {t('sharedLedger.targetRole')}:{' '}
                {preview.target_role === 'owner'
                  ? t('sharedLedger.roleOwner')
                  : t('sharedLedger.roleEditor')}
              </div>
              <div className="text-xs text-muted-foreground">
                {t('sharedLedger.expiresAt')}:{' '}
                {new Date(preview.expires_at).toLocaleString()}
              </div>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          {preview ? (
            <Button onClick={() => void onAccept()} disabled={accepting}>
              {accepting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                t('sharedLedger.acceptInvite')
              )}
            </Button>
          ) : (
            <Button onClick={() => void onPreview()} disabled={loading || !code.trim()}>
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                t('sharedLedger.preview')
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
