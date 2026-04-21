import type { UserAdmin } from '@beecount/api-client'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  useT,
} from '@beecount/ui'
import { AdminUsersPanel } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'

interface Props {
  adminUsers: UserAdmin[]
  listQuery: string
  onListQueryChange: (value: string) => void
  onRefresh: () => void
  onPatch: (
    userId: string,
    payload: { email?: string; is_enabled?: boolean }
  ) => Promise<boolean> | boolean
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
  onCreate: () => Promise<boolean>
}

/**
 * 管理员 - 用户管理 section —— 从 AppPage.tsx 抽出。
 * 非管理员走 permission-denied 兜底卡片。
 */
export function AdminUsersSection(props: Props) {
  const t = useT()
  const { isAdmin } = useAuth()

  if (!isAdmin) {
    return (
      <Card className="bc-panel">
        <CardHeader>
          <CardTitle>{t('admin.users.title')}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {t('admin.users.noPermission')}
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardContent className="pt-4">
          <Input
            className="h-9 w-[220px] bg-muted lg:w-[320px]"
            placeholder={t('shell.placeholder.keyword')}
            value={props.listQuery}
            onChange={(event) => props.onListQueryChange(event.target.value)}
          />
        </CardContent>
      </Card>
      <AdminUsersPanel
        rows={props.adminUsers}
        onReload={props.onRefresh}
        onPatch={props.onPatch}
        onChangePassword={props.onChangePassword}
        onDelete={props.onDelete}
        statusFilter={props.statusFilter}
        onStatusFilterChange={props.onStatusFilterChange}
        createEmail={props.createEmail}
        createPassword={props.createPassword}
        createIsAdmin={props.createIsAdmin}
        createIsEnabled={props.createIsEnabled}
        onCreateEmailChange={props.onCreateEmailChange}
        onCreatePasswordChange={props.onCreatePasswordChange}
        onCreateIsAdminChange={props.onCreateIsAdminChange}
        onCreateIsEnabledChange={props.onCreateIsEnabledChange}
        onCreate={props.onCreate}
      />
    </div>
  )
}
