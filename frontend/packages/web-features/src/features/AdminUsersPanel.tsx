import { useMemo, useState } from 'react'

import {
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
  onPatch: (userId: string, payload: { is_admin?: boolean; is_enabled?: boolean }) => Promise<boolean> | boolean
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

type UserDraft = {
  is_admin: boolean
  is_enabled: boolean
}

export function AdminUsersPanel({
  rows,
  onReload,
  onPatch,
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
  const [draftById, setDraftById] = useState<Record<string, UserDraft>>({})
  const [open, setOpen] = useState(false)
  const [brokenAvatarUserIds, setBrokenAvatarUserIds] = useState<Set<string>>(new Set())
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  const rowById = useMemo(() => {
    const map = new Map<string, UserAdmin>()
    for (const row of rows) {
      map.set(row.id, row)
    }
    return map
  }, [rows])
  const userDisplayName = (row: UserAdmin): string =>
    row.display_name?.trim() || row.email?.trim() || row.id
  const userAvatarInitial = (row: UserAdmin): string => {
    const source = userDisplayName(row).trim()
    return source.charAt(0).toUpperCase() || '?'
  }

  return (
    <ListTableShell
      title={t('admin.users.title')}
      actions={
        <div className="flex items-center gap-2">
          <Select value={statusFilter} onValueChange={(value) => onStatusFilterChange(value as 'enabled' | 'disabled' | 'all')}>
            <SelectTrigger className="h-9 w-[180px] bg-muted">
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
          <Button onClick={() => setOpen(true)}>{t('admin.users.button.create')}</Button>
        </div>
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
              <TableHead className="bc-table-head sticky right-0 z-20 min-w-[132px] bg-card">
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
            {rows.map((row) => {
              const draft = draftById[row.id] || { is_admin: row.is_admin, is_enabled: row.is_enabled }
              const dirty = draft.is_admin !== row.is_admin || draft.is_enabled !== row.is_enabled
              return (
                <TableRow
                  key={row.id}
                  className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[132px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
                >
                  <TableCell>
                    <div className="flex min-w-[220px] items-center gap-2">
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
                  <TableCell className="min-w-[180px]">
                    <Select
                      value={draft.is_admin ? 'admin' : 'user'}
                      onValueChange={(value) =>
                        setDraftById((prev) => ({
                          ...prev,
                          [row.id]: {
                            is_admin: value === 'admin',
                            is_enabled: draft.is_enabled
                          }
                        }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="admin">{t('enum.platformRole.admin')}</SelectItem>
                        <SelectItem value="user">{t('enum.platformRole.user')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell className="min-w-[180px]">
                    <Select
                      value={draft.is_enabled ? 'enabled' : 'disabled'}
                      onValueChange={(value) =>
                        setDraftById((prev) => ({
                          ...prev,
                          [row.id]: {
                            is_admin: draft.is_admin,
                            is_enabled: value === 'enabled'
                          }
                        }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="enabled">{t('enum.userStatus.enabled')}</SelectItem>
                        <SelectItem value="disabled">{t('enum.userStatus.disabled')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>{formatIsoDateTime(row.created_at)}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-3 whitespace-nowrap">
                      <button
                        className={textActionClass}
                        disabled={!dirty}
                        type="button"
                        onClick={async () => {
                          const current = rowById.get(row.id)
                          if (!current) return
                          const success = await onPatch(row.id, {
                            is_admin: draft.is_admin,
                            is_enabled: draft.is_enabled
                          })
                          if (!success) return
                          setDraftById((prev) => {
                            const next = { ...prev }
                            delete next[row.id]
                            return next
                          })
                        }}
                      >
                        {t('admin.users.button.save')}
                      </button>
                      <button
                        className={textDangerActionClass}
                        type="button"
                        onClick={async () => {
                          if (!window.confirm(t('admin.users.confirm.delete'))) return
                          await onDelete(row.id)
                        }}
                      >
                        {t('common.delete')}
                      </button>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('admin.users.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('admin.users.table.email')}</Label>
              <Input value={createEmail} onChange={(event) => onCreateEmailChange(event.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>{t('login.password')}</Label>
              <Input
                type="password"
                value={createPassword}
                onChange={(event) => onCreatePasswordChange(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('admin.users.table.role')}</Label>
              <Select
                value={createIsAdmin ? 'admin' : 'user'}
                onValueChange={(value) => onCreateIsAdminChange(value === 'admin')}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">{t('enum.platformRole.admin')}</SelectItem>
                  <SelectItem value="user">{t('enum.platformRole.user')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
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
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              onClick={async () => {
                const ok = await onCreate()
                if (ok) setOpen(false)
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
