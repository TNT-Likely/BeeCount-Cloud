import { useCallback, useEffect, useState } from 'react'
import { Copy, Loader2, Share2, Trash2 } from 'lucide-react'

import {
  createLedgerInvite,
  fetchLedgerInvites,
  fetchLedgerMembers,
  removeLedgerMember,
  revokeLedgerInvite,
  type LedgerInvite,
  type LedgerMember,
} from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast,
} from '@beecount/ui'
import { ConfirmDialog } from '@beecount/web-features'
import { localizeError } from '../i18n/errors'

import { useAuth } from '../context/AuthContext'

interface Props {
  open: boolean
  onOpenChange: (next: boolean) => void
  ledgerId: string
  ledgerName: string
  isOwner: boolean
}

/**
 * 共享账本管理对话框 — 成员列表 + 邀请管理(创建/列出/撤销/复制)。
 *
 * Owner: 全部可操作。Editor: 只能看自己,只能"退出"。
 * 设计上跟 mobile member_list_page.dart + invite_page.dart 等价,缩到一个
 * dialog 里方便 web 触发(从 LedgersSection / cmdk / AppHeader 进入)。
 */
export function SharedLedgerManageDialog({
  open,
  onOpenChange,
  ledgerId,
  ledgerName,
  isOwner,
}: Props) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const [members, setMembers] = useState<LedgerMember[]>([])
  const [invites, setInvites] = useState<LedgerInvite[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [expiresHours, setExpiresHours] = useState('24')
  // §7 UI:用 ConfirmDialog 替代 window.confirm。pendingRevoke/Remove/Leave 三
  // 个 state 标识当前要确认的目标;同时只会有一个非空。
  const [pendingRevokeCode, setPendingRevokeCode] = useState<string | null>(null)
  const [pendingRemove, setPendingRemove] = useState<LedgerMember | null>(null)
  const [pendingLeave, setPendingLeave] = useState<LedgerMember | null>(null)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  const load = useCallback(async () => {
    if (!token || !ledgerId) return
    setLoading(true)
    try {
      const [mems, invs] = await Promise.all([
        fetchLedgerMembers(token, ledgerId),
        isOwner ? fetchLedgerInvites(token, ledgerId) : Promise.resolve([] as LedgerInvite[]),
      ])
      setMembers(mems)
      setInvites(invs)
    } catch (err) {
      notifyError(err)
    } finally {
      setLoading(false)
    }
  }, [token, ledgerId, isOwner, notifyError])

  // §7 闪烁修复:**只**依赖原始值(open / token / ledgerId / isOwner)而非
  // load 回调 — 回调对 toast / t 不稳定的引用敏感,父组件每次 re-render 会让
  // 回调改 reference → useEffect 抖动 → setLoading(true) → 整块 dialog body
  // 被 spinner 替代 → 视觉闪烁。
  // 成员收支统计独立到 SharedLedgerStatsDialog 触发,不在本对话框 fetch。
  useEffect(() => {
    if (open) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, token, ledgerId, isOwner])

  const onClickRemove = useCallback((member: LedgerMember) => {
    if (member.is_self) setPendingLeave(member)
    else setPendingRemove(member)
  }, [])

  const doRemove = useCallback(
    async (member: LedgerMember) => {
      const isSelfLeave = member.is_self
      try {
        await removeLedgerMember(token, ledgerId, member.user_id)
        notifySuccess(
          isSelfLeave ? t('sharedLedger.left') : t('sharedLedger.removed'),
        )
        if (isSelfLeave) {
          // 退出后关闭对话框 — LedgersContext 会通过 WS member_change.removed
          // 自动刷新列表,本账本会从 sidebar 消失。
          onOpenChange(false)
        } else {
          await load()
        }
      } catch (err) {
        notifyError(err)
      }
    },
    [token, ledgerId, t, notifyError, notifySuccess, onOpenChange, load],
  )

  const onCreate = useCallback(async () => {
    setCreating(true)
    try {
      const hours = Math.max(1, Math.min(168, parseInt(expiresHours, 10) || 24))
      await createLedgerInvite(token, ledgerId, { expires_in_hours: hours })
      notifySuccess(t('sharedLedger.inviteCreated'))
      await load()
    } catch (err) {
      notifyError(err)
    } finally {
      setCreating(false)
    }
  }, [token, ledgerId, expiresHours, t, notifyError, notifySuccess, load])

  const doRevoke = useCallback(
    async (code: string) => {
      try {
        await revokeLedgerInvite(token, ledgerId, code)
        notifySuccess(t('sharedLedger.revoked'))
        await load()
      } catch (err) {
        notifyError(err)
      }
    },
    [token, ledgerId, t, notifyError, notifySuccess, load],
  )

  // 复制按钮:**只**复制邀请码本身(用户口头报 / 复制到 IM 私聊场景常用),
  // 完整链接 + 文案走旁边的 share 按钮。
  const copyInvite = useCallback(
    async (invite: LedgerInvite) => {
      try {
        await navigator.clipboard.writeText(invite.formatted_code)
        notifySuccess(t('sharedLedger.copied'))
      } catch (err) {
        notifyError(err)
      }
    },
    [t, notifyError, notifySuccess],
  )

  // 分享按钮:走完整文案(账本名 + 邀请码 + share_url),OS share sheet
  // 或 fallback 复制到剪贴板。
  const sysShare = useCallback(
    async (invite: LedgerInvite) => {
      const text = t('sharedLedger.inviteShareTemplate', {
        ledger: ledgerName,
        code: invite.formatted_code,
        url: invite.share_url,
      })
      if (navigator.share) {
        try {
          await navigator.share({ title: t('sharedLedger.shareTitle'), text })
        } catch {
          // 用户取消 share 不算 error
        }
      } else {
        try {
          await navigator.clipboard.writeText(text)
          notifySuccess(t('sharedLedger.copied'))
        } catch (err) {
          notifyError(err)
        }
      }
    },
    [t, ledgerName, notifyError, notifySuccess],
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            🤝 {ledgerName} · {t('sharedLedger.manageTitle')}
          </DialogTitle>
        </DialogHeader>

        {/* §7 闪烁修复:首次加载时占位,后续刷新保留旧数据 — 复制 / 切换
            周期时不再整块替换为 spinner。 */}
        {loading && members.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : (
          <div className="space-y-6">
            {/* 成员列表 */}
            <section>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                <span>{t('sharedLedger.members')} ({members.length})</span>
                {loading ? (
                  <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                ) : null}
              </h3>
              <div className="space-y-1">
                {members.map((m) => (
                  <div
                    key={m.user_id}
                    className="flex items-center justify-between rounded border border-border/40 bg-background/40 px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {m.display_name || m.email.split('@')[0]}
                        {m.is_self ? (
                          <span className="ml-1 text-xs text-muted-foreground">
                            ({t('sharedLedger.you')})
                          </span>
                        ) : null}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {m.email}
                      </div>
                    </div>
                    <span
                      className={`mr-2 rounded px-2 py-0.5 text-xs ${
                        m.role === 'owner'
                          ? 'bg-primary/15 text-primary'
                          : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {m.role === 'owner'
                        ? t('sharedLedger.roleOwner')
                        : t('sharedLedger.roleEditor')}
                    </span>
                    {/* Owner 可踢非 owner;任意成员可删自己(Owner 自己也可,server
                        会拦 — Owner 退出前必须 transfer,目前 Phase 3 才支持) */}
                    {(isOwner && !m.is_self && m.role !== 'owner') ||
                    (m.is_self && m.role !== 'owner') ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onClickRemove(m)}
                        title={
                          m.is_self
                            ? t('sharedLedger.leave')
                            : t('sharedLedger.remove')
                        }
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    ) : null}
                  </div>
                ))}
              </div>
            </section>

            {/* 邀请管理 — 仅 Owner */}
            {isOwner ? (
              <section>
                <h3 className="mb-2 text-sm font-semibold">
                  {t('sharedLedger.invites')} ({invites.length})
                </h3>
                <div className="mb-3 flex items-end gap-2">
                  <div className="flex-1">
                    <Label className="mb-1 text-xs">
                      {t('sharedLedger.inviteExpires')}
                    </Label>
                    <Select
                      value={expiresHours}
                      onValueChange={setExpiresHours}
                    >
                      <SelectTrigger className="h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="1">1 {t('sharedLedger.hour')}</SelectItem>
                        <SelectItem value="24">24 {t('sharedLedger.hour')}</SelectItem>
                        <SelectItem value="72">3 {t('sharedLedger.day')}</SelectItem>
                        <SelectItem value="168">7 {t('sharedLedger.day')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    onClick={() => void onCreate()}
                    disabled={creating}
                    size="sm"
                  >
                    {creating ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      t('sharedLedger.createInvite')
                    )}
                  </Button>
                </div>
                <div className="space-y-1">
                  {invites.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      {t('sharedLedger.noInvites')}
                    </p>
                  ) : null}
                  {invites.map((inv) => (
                    <div
                      key={inv.code}
                      className="flex items-center justify-between rounded border border-border/40 bg-background/40 px-3 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-lg font-semibold tracking-wider">
                          {inv.formatted_code}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {t('sharedLedger.expiresAt')}:{' '}
                          {new Date(inv.expires_at).toLocaleString()}
                        </div>
                      </div>
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void copyInvite(inv)}
                          title={t('sharedLedger.copyCode') as string}
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void sysShare(inv)}
                          title={t('sharedLedger.share')}
                        >
                          <Share2 className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setPendingRevokeCode(inv.code)}
                          title={t('sharedLedger.revoke')}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <ConfirmDialog
        open={pendingRevokeCode != null}
        title={t('sharedLedger.revoke')}
        description={t('sharedLedger.revokeConfirm')}
        confirmText={t('sharedLedger.revoke')}
        cancelText={t('common.cancel')}
        onCancel={() => setPendingRevokeCode(null)}
        onConfirm={() => {
          if (pendingRevokeCode) void doRevoke(pendingRevokeCode)
          setPendingRevokeCode(null)
        }}
      />
      <ConfirmDialog
        open={pendingRemove != null}
        title={t('sharedLedger.remove')}
        description={
          pendingRemove
            ? t('sharedLedger.removeConfirm', {
                name: pendingRemove.display_name || pendingRemove.email,
              })
            : ''
        }
        confirmText={t('sharedLedger.remove')}
        cancelText={t('common.cancel')}
        onCancel={() => setPendingRemove(null)}
        onConfirm={() => {
          if (pendingRemove) void doRemove(pendingRemove)
          setPendingRemove(null)
        }}
      />
      <ConfirmDialog
        open={pendingLeave != null}
        title={t('sharedLedger.leave')}
        description={t('sharedLedger.leaveConfirm')}
        confirmText={t('sharedLedger.leave')}
        cancelText={t('common.cancel')}
        onCancel={() => setPendingLeave(null)}
        onConfirm={() => {
          if (pendingLeave) void doRemove(pendingLeave)
          setPendingLeave(null)
        }}
      />
    </Dialog>
  )
}
