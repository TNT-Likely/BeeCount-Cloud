import { useEffect, useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'

import type { UserAdmin } from '@beecount/api-client'

import { ListTableShell } from '../components/ListTableShell'
import { formatIsoDateTime } from '../format'

type AdminUsersPanelProps = {
  rows: UserAdmin[]
  onReload: () => void
  onPatch: (
    userId: string,
    payload: { email?: string; is_enabled?: boolean }
  ) => Promise<boolean> | boolean
  /**
   * 修改密码。adminPassword 是调用者自己当前的密码(二次验证),newPassword 是目标密码。
   * 成功后 server 会 revoke 目标用户的所有 refresh token。
   */
  onChangePassword: (
    userId: string,
    adminPassword: string,
    newPassword: string
  ) => Promise<boolean> | boolean
  onDelete: (userId: string) => Promise<boolean> | boolean
  statusFilter: 'enabled' | 'disabled' | 'all'
  onStatusFilterChange: (value: 'enabled' | 'disabled' | 'all') => void
  createEmail: string
  createPassword: string
  createIsAdmin: boolean
  createIsEnabled: boolean
  onCreateEmailChange: (value: string) => void
  onCreatePasswordChange: (value: string) => void
  onCreateIsAdminChange: (value: boolean) => void
  onCreateIsEnabledChange: (value: boolean) => void
  onCreate: () => Promise<boolean> | boolean
}

type EditState = {
  userId: string
  email: string
  is_enabled: boolean
  is_admin: boolean
}

type PasswordDialogState = {
  userId: string
  userLabel: string
  adminPassword: string
  newPassword: string
  confirmPassword: string
  error: string | null
  submitting: boolean
}

export function AdminUsersPanel({
  rows,
  onReload,
  onPatch,
  onChangePassword,
  onDelete,
  statusFilter,
  onStatusFilterChange,
  createEmail,
  createPassword,
  createIsAdmin,
  createIsEnabled,
  onCreateEmailChange,
  onCreatePasswordChange,
  onCreateIsAdminChange,
  onCreateIsEnabledChange,
  onCreate
}: AdminUsersPanelProps) {
  const t = useT()
  const [createOpen, setCreateOpen] = useState(false)
  const [edit, setEdit] = useState<EditState | null>(null)
  const [passwordDialog, setPasswordDialog] = useState<PasswordDialogState | null>(null)
  const [brokenAvatarUserIds, setBrokenAvatarUserIds] = useState<Set<string>>(new Set())
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  const rowById = useMemo(() => {
    const map = new Map<string, UserAdmin>()
    for (const row of rows) map.set(row.id, row)
    return map
  }, [rows])
  const userDisplayName = (row: UserAdmin): string =>
    row.display_name?.trim() || row.email?.trim() || row.id
  const userAvatarInitial = (row: UserAdmin): string =>
    userDisplayName(row).trim().charAt(0).toUpperCase() || '?'

  const openEditDialog = (row: UserAdmin) => {
    setEdit({
      userId: row.id,
      email: row.email || '',
      is_enabled: row.is_enabled,
      is_admin: row.is_admin
    })
  }

  const openPasswordDialog = (row: UserAdmin) => {
    setPasswordDialog({
      userId: row.id,
      userLabel: userDisplayName(row),
      adminPassword: '',
      newPassword: '',
      confirmPassword: '',
      error: null,
      submitting: false
    })
  }

  // edit 弹窗期间若外层刷新了 rows(onPatch 成功会触发),把最新的 is_enabled /
  // is_admin 同步回来,避免乐观 UI 跟远端脱节。
  useEffect(() => {
    if (!edit) return
    const fresh = rowById.get(edit.userId)
    if (!fresh) return
    setEdit((prev) => {
      if (!prev) return prev
      if (prev.is_enabled === fresh.is_enabled && prev.is_admin === fresh.is_admin) {
        return prev
      }
      return { ...prev, is_enabled: fresh.is_enabled, is_admin: fresh.is_admin }
    })
  }, [rowById, edit])

  const doSaveEdit = async () => {
    if (!edit) return
    const original = rowById.get(edit.userId)
    if (!original) return
    const payload: { email?: string; is_enabled?: boolean } = {}
    const trimmedEmail = edit.email.trim()
    if (trimmedEmail && trimmedEmail !== original.email) {
      payload.email = trimmedEmail
    }
    if (edit.is_enabled !== original.is_enabled) {
      payload.is_enabled = edit.is_enabled
    }
    if (Object.keys(payload).length === 0) {
      setEdit(null)
      return
    }
    const ok = await onPatch(edit.userId, payload)
    if (ok) setEdit(null)
  }

  const doSubmitPassword = async () => {
    if (!passwordDialog) return
    const { adminPassword, newPassword, confirmPassword } = passwordDialog
    if (!adminPassword.trim()) {
      setPasswordDialog((prev) =>
        prev ? { ...prev, error: t('admin.users.password.error.adminRequired') } : prev
      )
      return
    }
    if (newPassword.length < 6) {
      setPasswordDialog((prev) =>
        prev ? { ...prev, error: t('admin.users.password.error.tooShort') } : prev
      )
      return
    }
    if (newPassword !== confirmPassword) {
      setPasswordDialog((prev) =>
        prev ? { ...prev, error: t('admin.users.password.error.mismatch') } : prev
      )
      return
    }
    setPasswordDialog((prev) => (prev ? { ...prev, submitting: true, error: null } : prev))
    const ok = await onChangePassword(passwordDialog.userId, adminPassword, newPassword)
    if (ok) {
      setPasswordDialog(null)
    } else {
      // 服务器侧的错误信息(401 admin password mismatch 等)已经由 AppPage
      // 的 setErrorNotice 处理过了,这里只解除 submitting 让用户可以改字段重试。
      setPasswordDialog((prev) => (prev ? { ...prev, submitting: false } : prev))
    }
  }

  return (
    <ListTableShell
      title={t('admin.users.title')}
      actions={
        <>
          <Select
            value={statusFilter}
            onValueChange={(value) =>
              onStatusFilterChange(value as 'enabled' | 'disabled' | 'all')
            }
          >
            <SelectTrigger className="h-9 w-[140px] bg-muted sm:w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="enabled">{t('admin.users.filter.enabled')}</SelectItem>
              <SelectItem value="disabled">{t('admin.users.filter.disabled')}</SelectItem>
              <SelectItem value="all">{t('admin.users.filter.all')}</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={onReload}>
            {t('shell.refresh')}
          </Button>
          <Button onClick={() => setCreateOpen(true)}>{t('admin.users.button.create')}</Button>
        </>
      }
    >
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="bc-table-head">{t('admin.users.table.email')}</TableHead>
              <TableHead className="bc-table-head">{t('admin.users.table.id')}</TableHead>
              <TableHead className="bc-table-head">{t('admin.users.table.role')}</TableHead>
              <TableHead className="bc-table-head">{t('admin.users.table.status')}</TableHead>
              <TableHead className="bc-table-head">{t('admin.users.table.createdAt')}</TableHead>
              <TableHead className="bc-table-head sticky right-0 z-20 min-w-[180px] bg-card">
                {t('admin.users.table.ops')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-12 text-center text-sm text-muted-foreground">
                  {t('table.empty')}
                </TableCell>
              </TableRow>
            ) : null}
            {rows.map((row) => (
              <TableRow
                key={row.id}
                className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[180px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
              >
                <TableCell>
                  {/* mobile 上 min-w 设 160 够放头像 + 名字 + 邮箱,sm+ 恢复 220。
                      过宽会顶走 Ops sticky 列的可用空间。 */}
                  <div className="flex min-w-[160px] items-center gap-2 sm:min-w-[220px]">
                    {row.avatar_url && !brokenAvatarUserIds.has(row.id) ? (
                      <img
                        alt={userDisplayName(row)}
                        className="h-7 w-7 rounded-full border border-border/60 object-cover"
                        src={row.avatar_url}
                        onError={() =>
                          setBrokenAvatarUserIds((prev) => {
                            if (prev.has(row.id)) return prev
                            const next = new Set(prev)
                            next.add(row.id)
                            return next
                          })
                        }
                      />
                    ) : (
                      <div className="flex h-7 w-7 items-center justify-center rounded-full border border-border/60 bg-muted text-xs font-medium text-muted-foreground">
                        {userAvatarInitial(row)}
                      </div>
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-sm">{userDisplayName(row)}</p>
                      <p className="truncate text-xs text-muted-foreground">{row.email}</p>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">{row.id}</TableCell>
                <TableCell>
                  <Badge variant={row.is_admin ? 'default' : 'secondary'}>
                    {row.is_admin
                      ? t('enum.platformRole.admin')
                      : t('enum.platformRole.user')}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={row.is_enabled ? 'outline' : 'destructive'}>
                    {row.is_enabled
                      ? t('enum.userStatus.enabled')
                      : t('enum.userStatus.disabled')}
                  </Badge>
                </TableCell>
                <TableCell>{formatIsoDateTime(row.created_at)}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-3 whitespace-nowrap">
                    <button
                      className={textActionClass}
                      type="button"
                      onClick={() => openEditDialog(row)}
                    >
                      {t('admin.users.button.edit')}
                    </button>
                    <button
                      className={textActionClass}
                      type="button"
                      onClick={() => openPasswordDialog(row)}
                    >
                      {t('admin.users.button.changePassword')}
                    </button>
                    <button
                      className={textDangerActionClass}
                      type="button"
                      disabled={row.is_admin}
                      title={
                        row.is_admin ? t('admin.users.hint.adminProtected') : undefined
                      }
                      onClick={async () => {
                        if (row.is_admin) return
                        if (!window.confirm(t('admin.users.confirm.delete'))) return
                        await onDelete(row.id)
                      }}
                    >
                      {t('common.delete')}
                    </button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* 编辑弹窗:邮箱 / 启用状态。角色只展示不可改,密码改走独立弹窗。 */}
      <Dialog open={edit !== null} onOpenChange={(open) => (open ? null : setEdit(null))}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.users.edit.title')}</DialogTitle>
          </DialogHeader>
          {edit ? (
            <div className="grid gap-3">
              <div className="space-y-1">
                <Label>{t('admin.users.table.email')}</Label>
                <Input
                  value={edit.email}
                  onChange={(event) =>
                    setEdit((prev) => (prev ? { ...prev, email: event.target.value } : prev))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>{t('admin.users.table.role')}</Label>
                <div className="flex items-center gap-2">
                  <Badge variant={edit.is_admin ? 'default' : 'secondary'}>
                    {edit.is_admin
                      ? t('enum.platformRole.admin')
                      : t('enum.platformRole.user')}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {t('admin.users.edit.roleReadonly')}
                  </span>
                </div>
              </div>
              <div className="space-y-1">
                <Label>{t('admin.users.table.status')}</Label>
                <Select
                  value={edit.is_enabled ? 'enabled' : 'disabled'}
                  onValueChange={(value) =>
                    setEdit((prev) =>
                      prev ? { ...prev, is_enabled: value === 'enabled' } : prev
                    )
                  }
                  disabled={edit.is_admin}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="enabled">{t('enum.userStatus.enabled')}</SelectItem>
                    <SelectItem value="disabled">{t('enum.userStatus.disabled')}</SelectItem>
                  </SelectContent>
                </Select>
                {edit.is_admin ? (
                  <p className="text-[11px] text-muted-foreground">
                    {t('admin.users.hint.adminProtected')}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEdit(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button onClick={doSaveEdit}>{t('admin.users.button.save')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 修改密码独立弹窗:admin 当前密码 + 新密码 + 确认新密码。 */}
      <Dialog
        open={passwordDialog !== null}
        onOpenChange={(open) => {
          if (!open) setPasswordDialog(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.users.password.title')}</DialogTitle>
          </DialogHeader>
          {passwordDialog ? (
            <div className="grid gap-3">
              <p className="text-xs text-muted-foreground">
                {t('admin.users.password.subtitle').replace(
                  '{user}',
                  passwordDialog.userLabel
                )}
              </p>
              <div className="space-y-1">
                <Label>{t('admin.users.password.adminPassword')}</Label>
                <Input
                  type="password"
                  autoComplete="current-password"
                  value={passwordDialog.adminPassword}
                  onChange={(event) =>
                    setPasswordDialog((prev) =>
                      prev ? { ...prev, adminPassword: event.target.value } : prev
                    )
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>{t('admin.users.password.newPassword')}</Label>
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={passwordDialog.newPassword}
                  onChange={(event) =>
                    setPasswordDialog((prev) =>
                      prev ? { ...prev, newPassword: event.target.value } : prev
                    )
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>{t('admin.users.password.confirmPassword')}</Label>
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={passwordDialog.confirmPassword}
                  onChange={(event) =>
                    setPasswordDialog((prev) =>
                      prev ? { ...prev, confirmPassword: event.target.value } : prev
                    )
                  }
                />
              </div>
              {passwordDialog.error ? (
                <p className="text-xs text-destructive">{passwordDialog.error}</p>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPasswordDialog(null)}
              disabled={passwordDialog?.submitting}
            >
              {t('dialog.cancel')}
            </Button>
            <Button onClick={doSubmitPassword} disabled={passwordDialog?.submitting}>
              {t('admin.users.password.submit')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.users.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('admin.users.table.email')}</Label>
              <Input
                value={createEmail}
                onChange={(event) => onCreateEmailChange(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('login.password')}</Label>
              <Input
                type="password"
                value={createPassword}
                onChange={(event) => onCreatePasswordChange(event.target.value)}
              />
            </div>
            {/* 角色选择已移除 —— self-host 定位单管理员(创始人账号),
                新建用户一律是普通用户。想升/降管理员只能走 DB 手工操作 +
                审计,见 src/routers/admin.py create_user 里的硬锁注释。 */}
            <div className="space-y-1">
              <Label>{t('admin.users.table.status')}</Label>
              <Select
                value={createIsEnabled ? 'enabled' : 'disabled'}
                onValueChange={(value) => onCreateIsEnabledChange(value === 'enabled')}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="enabled">{t('enum.userStatus.enabled')}</SelectItem>
                  <SelectItem value="disabled">{t('enum.userStatus.disabled')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              onClick={async () => {
                const ok = await onCreate()
                if (ok) setCreateOpen(false)
              }}
            >
              {t('admin.users.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ListTableShell>
  )
}
