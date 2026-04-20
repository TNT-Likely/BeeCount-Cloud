import { useCallback, useEffect, useState } from 'react'

import {
  changeAdminUserPassword,
  createAdminUser,
  deleteAdminUser,
  fetchAdminUsers,
  patchAdminUser,
  type UserAdmin,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import { AdminUsersSection } from '../../components/sections/AdminUsersSection'
import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

type StatusFilter = 'enabled' | 'disabled' | 'all'

/**
 * 管理员 · 用户管理页 —— 自持 list + create form + 所有 CRUD handler。
 *
 * 只有 AppShell 的 isAdmin 已解析为 true 才进入;否则 AppShell 已在更外层把
 * /app/admin/* 重定向到 overview。但 AdminUsersSection 内部仍保留
 * "非 admin → noPermission" 的兜底卡片,处理 admin 被降权的场景。
 */
export function AdminUsersPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [rows, setRows] = useState<UserAdmin[]>([])
  const [listQuery, setListQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('enabled')
  const [createEmail, setCreateEmail] = useState('')
  const [createPassword, setCreatePassword] = useState('')
  const [createIsAdmin, setCreateIsAdmin] = useState(false)
  const [createIsEnabled, setCreateIsEnabled] = useState(true)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    if (!isAdmin) {
      setRows([])
      return
    }
    try {
      const list = await fetchAdminUsers(token, {
        q: listQuery || undefined,
        status: statusFilter,
        limit: 500,
      })
      setRows(list.items)
    } catch (err) {
      notifyError(err)
    }
  }, [token, isAdmin, listQuery, statusFilter, notifyError])

  useEffect(() => {
    if (!isAdminResolved) return
    void refresh()
  }, [isAdminResolved, refresh])

  const onPatch = async (
    userId: string,
    payload: { email?: string; is_enabled?: boolean }
  ) => {
    try {
      await patchAdminUser(token, userId, payload)
      await refresh()
      notifySuccess(t('notice.userUpdated'))
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onChangePassword = async (
    userId: string,
    adminPassword: string,
    newPassword: string
  ) => {
    try {
      await changeAdminUserPassword(token, userId, {
        admin_password: adminPassword,
        new_password: newPassword,
      })
      notifySuccess(t('notice.userPasswordUpdated'))
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onDelete = async (userId: string) => {
    try {
      await deleteAdminUser(token, userId)
      await refresh()
      notifySuccess(t('notice.userDeleted'))
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onCreate = async () => {
    if (!createEmail.trim() || !createPassword.trim()) {
      toast.error(t('admin.users.error.createRequired'), t('notice.error'))
      return false
    }
    try {
      await createAdminUser(token, {
        email: createEmail.trim(),
        password: createPassword,
        is_admin: createIsAdmin,
        is_enabled: createIsEnabled,
      })
      await refresh()
      setCreateEmail('')
      setCreatePassword('')
      setCreateIsAdmin(false)
      setCreateIsEnabled(true)
      notifySuccess(t('notice.userCreated'))
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  return (
    <AdminUsersSection
      adminUsers={rows}
      listQuery={listQuery}
      onListQueryChange={setListQuery}
      onRefresh={() => void refresh()}
      onPatch={onPatch}
      onChangePassword={onChangePassword}
      onDelete={onDelete}
      statusFilter={statusFilter}
      onStatusFilterChange={setStatusFilter}
      createEmail={createEmail}
      createPassword={createPassword}
      createIsAdmin={createIsAdmin}
      createIsEnabled={createIsEnabled}
      onCreateEmailChange={setCreateEmail}
      onCreatePasswordChange={setCreatePassword}
      onCreateIsAdminChange={setCreateIsAdmin}
      onCreateIsEnabledChange={setCreateIsEnabled}
      onCreate={onCreate}
    />
  )
}
