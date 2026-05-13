import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createPat,
  listPats,
  revokePat,
  updatePat,
  type PatCreateResponse,
  type PatListItem,
  type PatScope,
} from '@beecount/api-client'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
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
import { Copy, KeyRound, Pencil, Plus, Trash2 } from 'lucide-react'
// Copy 仅创建弹窗里复制完整 token 用;行内不放"复制 prefix"按钮 —— 完整
// token 服务端只存 sha256 不可恢复(同 GitHub PAT 设计),只能复制 prefix
// 反而误导用户以为能拿来配置客户端。需要完整 token 的话撤销重建。

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

type ExpirationOption = '30' | '90' | '180' | '365' | 'never'

const EXPIRATION_OPTIONS: ExpirationOption[] = ['30', '90', '180', '365', 'never']

const SCOPE_OPTIONS: Array<{ value: 'read' | 'write'; scopes: PatScope[] }> = [
  { value: 'read', scopes: ['mcp:read'] },
  { value: 'write', scopes: ['mcp:read', 'mcp:write'] },
]

/**
 * Settings → 开发者 → PAT 管理。
 *
 * 列出 / 创建 / 编辑 / 撤销 / 删除。创建后明文 token 弹一次让用户复制,关闭
 * 弹窗后**再也拿不到** —— 跟 GitHub PAT 同体验。撤销是软删除(revoked_at),
 * 已撤销 token 上再点删除会物理移除行。编辑只改 name / scopes,不允许延长
 * 有效期(防止泄露后偷续期)。
 */
export function SettingsPatsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()

  const [rows, setRows] = useState<PatListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newScope, setNewScope] = useState<'read' | 'write'>('read')
  const [newExpiration, setNewExpiration] = useState<ExpirationOption>('90')
  const [createdToken, setCreatedToken] = useState<PatCreateResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState<PatListItem | null>(null)
  const [editName, setEditName] = useState('')
  const [editScope, setEditScope] = useState<'read' | 'write'>('read')
  const [savingEdit, setSavingEdit] = useState(false)
  const [pendingRevoke, setPendingRevoke] = useState<PatListItem | null>(null)
  const [revoking, setRevoking] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const items = await listPats(token)
      setRows(items)
    } catch (err) {
      notifyError(err)
    } finally {
      setLoading(false)
    }
  }, [token, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleCreate = useCallback(async () => {
    const trimmed = newName.trim()
    if (!trimmed) {
      toast.error(t('settings.pats.create.errors.nameRequired'))
      return
    }
    setCreating(true)
    try {
      const scopes = SCOPE_OPTIONS.find((opt) => opt.value === newScope)?.scopes ?? ['mcp:read']
      const expires_in_days = newExpiration === 'never' ? null : Number(newExpiration)
      const result = await createPat(token, { name: trimmed, scopes, expires_in_days })
      setCreatedToken(result)
      setCreateOpen(false)
      setNewName('')
      setNewScope('read')
      setNewExpiration('90')
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setCreating(false)
    }
  }, [token, newName, newScope, newExpiration, refresh, notifyError, toast, t])

  const handleRevokeConfirmed = useCallback(async () => {
    if (!pendingRevoke) return
    setRevoking(true)
    try {
      await revokePat(token, pendingRevoke.id)
      toast.success(t('settings.pats.delete.success'))
      setPendingRevoke(null)
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setRevoking(false)
    }
  }, [pendingRevoke, token, refresh, notifyError, toast, t])

  const openEdit = useCallback((row: PatListItem) => {
    setEditing(row)
    setEditName(row.name)
    setEditScope(row.scopes.includes('mcp:write') ? 'write' : 'read')
  }, [])

  const handleSaveEdit = useCallback(async () => {
    if (!editing) return
    const trimmed = editName.trim()
    if (!trimmed) {
      toast.error(t('settings.pats.create.errors.nameRequired'))
      return
    }
    const scopes = SCOPE_OPTIONS.find((opt) => opt.value === editScope)?.scopes ?? ['mcp:read']
    setSavingEdit(true)
    try {
      await updatePat(token, editing.id, { name: trimmed, scopes })
      toast.success(t('settings.pats.edit.success'))
      setEditing(null)
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setSavingEdit(false)
    }
  }, [editing, editName, editScope, token, refresh, notifyError, toast, t])

  const handleCopy = useCallback(async () => {
    if (!createdToken) return
    try {
      await navigator.clipboard.writeText(createdToken.token)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error(t('settings.pats.created.copyFailed'))
    }
  }, [createdToken, toast, t])

  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
      const aActive = a.revoked_at ? 1 : 0
      const bActive = b.revoked_at ? 1 : 0
      if (aActive !== bActive) return aActive - bActive
      return b.created_at.localeCompare(a.created_at)
    })
  }, [rows])


  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <KeyRound className="h-5 w-5 text-muted-foreground" />
              <h2 className="text-base font-semibold">{t('settings.pats.title')}</h2>
              <span className="hidden text-xs text-muted-foreground sm:inline">
                {t('settings.pats.subtitleShort')}
              </span>
            </div>
            <Button onClick={() => setCreateOpen(true)} size="sm">
              <Plus className="mr-1 h-4 w-4" />
              {t('settings.pats.actions.create')}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {loading ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              {t('common.loading')}
            </div>
          ) : sortedRows.length === 0 ? (
            <div className="rounded-md border border-dashed py-10 text-center text-sm text-muted-foreground">
              {t('settings.pats.empty')}
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {sortedRows.map((row) => (
                <PatRow
                  key={row.id}
                  row={row}
                  onRevoke={() => setPendingRevoke(row)}
                  onEdit={() => openEdit(row)}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.pats.create.title')}</DialogTitle>
            <DialogDescription>{t('settings.pats.create.description')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="pat-name">{t('settings.pats.create.fields.name')}</Label>
              <Input
                id="pat-name"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="Claude Desktop"
                maxLength={128}
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                {t('settings.pats.create.fields.nameHint')}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>{t('settings.pats.create.fields.scope')}</Label>
              <Select value={newScope} onValueChange={(v) => setNewScope(v as 'read' | 'write')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="read">{t('settings.pats.scope.readOnly')}</SelectItem>
                  <SelectItem value="write">{t('settings.pats.scope.readWrite')}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {newScope === 'read'
                  ? t('settings.pats.create.fields.scopeHintRead')
                  : t('settings.pats.create.fields.scopeHintWrite')}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>{t('settings.pats.create.fields.expiration')}</Label>
              <Select
                value={newExpiration}
                onValueChange={(v) => setNewExpiration(v as ExpirationOption)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRATION_OPTIONS.map((opt) => (
                    <SelectItem key={opt} value={opt}>
                      {opt === 'never'
                        ? t('settings.pats.create.expiration.never')
                        : t('settings.pats.create.expiration.days', { days: Number(opt) })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void handleCreate()} disabled={creating || !newName.trim()}>
              {creating ? t('common.loading') : t('settings.pats.actions.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!createdToken} onOpenChange={(open) => !open && setCreatedToken(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.pats.created.title')}</DialogTitle>
            <DialogDescription>{t('settings.pats.created.description')}</DialogDescription>
          </DialogHeader>
          {createdToken ? (
            <div className="space-y-3">
              <div className="rounded-md border bg-muted/50 p-3 font-mono text-sm break-all">
                {createdToken.token}
              </div>
              <Button onClick={() => void handleCopy()} variant="secondary" className="w-full">
                <Copy className="mr-2 h-4 w-4" />
                {copied ? t('settings.pats.created.copied') : t('settings.pats.created.copy')}
              </Button>
              <p className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-xs text-amber-900 dark:text-amber-200">
                {t('settings.pats.created.warning')}
              </p>
            </div>
          ) : null}
          <DialogFooter>
            <Button onClick={() => setCreatedToken(null)}>{t('common.done')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.pats.edit.title')}</DialogTitle>
            <DialogDescription>{t('settings.pats.edit.description')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="pat-edit-name">{t('settings.pats.create.fields.name')}</Label>
              <Input
                id="pat-edit-name"
                value={editName}
                onChange={(event) => setEditName(event.target.value)}
                maxLength={128}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t('settings.pats.create.fields.scope')}</Label>
              <Select value={editScope} onValueChange={(v) => setEditScope(v as 'read' | 'write')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="read">{t('settings.pats.scope.readOnly')}</SelectItem>
                  <SelectItem value="write">{t('settings.pats.scope.readWrite')}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {editScope === 'read'
                  ? t('settings.pats.create.fields.scopeHintRead')
                  : t('settings.pats.create.fields.scopeHintWrite')}
              </p>
            </div>
            <p className="rounded-md border border-muted bg-muted/30 p-2 text-xs text-muted-foreground">
              {t('settings.pats.edit.expirationLocked')}
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)} disabled={savingEdit}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void handleSaveEdit()} disabled={savingEdit || !editName.trim()}>
              {savingEdit ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!pendingRevoke}
        title={t('settings.pats.delete.title')}
        description={
          pendingRevoke ? t('settings.pats.delete.confirm', { name: pendingRevoke.name }) : ''
        }
        confirmText={t('settings.pats.actions.delete')}
        cancelText={t('common.cancel')}
        loading={revoking}
        onCancel={() => setPendingRevoke(null)}
        onConfirm={() => void handleRevokeConfirmed()}
      />
    </div>
  )
}

interface PatRowProps {
  row: PatListItem
  onRevoke: () => void
  onEdit: () => void
}

function PatRow({ row, onRevoke, onEdit }: PatRowProps) {
  const t = useT()
  const revoked = !!row.revoked_at
  const expired = row.expires_at ? new Date(row.expires_at).getTime() < Date.now() : false
  const status: 'active' | 'revoked' | 'expired' = revoked ? 'revoked' : expired ? 'expired' : 'active'

  const statusVariant =
    status === 'active'
      ? 'success'
      : status === 'expired'
        ? 'warning'
        : 'muted'
  const statusLabel = t(`settings.pats.status.${status}`)
  const scopeLabel = row.scopes.includes('mcp:write')
    ? t('settings.pats.scope.readWriteShort')
    : t('settings.pats.scope.readOnlyShort')

  return (
    <li className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{row.name}</span>
          <StatusBadge variant={statusVariant}>{statusLabel}</StatusBadge>
          <Badge variant="outline" className="font-normal">
            {scopeLabel}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
            {row.prefix}…
          </code>
          {row.expires_at ? (
            <span>
              {t('settings.pats.fields.expiresAt')} {new Date(row.expires_at).toLocaleDateString()}
            </span>
          ) : (
            <span>{t('settings.pats.fields.noExpiry')}</span>
          )}
          {row.last_used_at ? (
            <span>
              {t('settings.pats.fields.lastUsed')} {new Date(row.last_used_at).toLocaleString()}
              {row.last_used_ip ? ` · ${row.last_used_ip}` : ''}
            </span>
          ) : (
            <span>{t('settings.pats.fields.neverUsed')}</span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-0.5 self-end sm:self-center">
        {!revoked ? (
          <IconButton onClick={onEdit} label={t('settings.pats.actions.edit')}>
            <Pencil className="h-4 w-4" />
          </IconButton>
        ) : null}
        <IconButton
          onClick={onRevoke}
          label={t('settings.pats.actions.delete')}
          variant="destructive"
        >
          <Trash2 className="h-4 w-4" />
        </IconButton>
      </div>
    </li>
  )
}

function IconButton({
  children,
  onClick,
  label,
  variant = 'default',
}: {
  children: React.ReactNode
  onClick: () => void
  label: string
  variant?: 'default' | 'destructive'
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={
        variant === 'destructive'
          ? 'h-8 w-8 text-destructive hover:text-destructive'
          : 'h-8 w-8'
      }
    >
      {children}
    </Button>
  )
}

function StatusBadge({
  children,
  variant,
}: {
  children: React.ReactNode
  variant: 'success' | 'warning' | 'muted'
}) {
  const cls =
    variant === 'success'
      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : variant === 'warning'
        ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'border-border bg-muted text-muted-foreground'
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] ${cls}`}>
      {children}
    </span>
  )
}
