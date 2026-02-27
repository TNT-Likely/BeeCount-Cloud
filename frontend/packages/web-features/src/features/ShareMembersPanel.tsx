import { useState } from 'react'

import {
  Button,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'

import type { ShareMember } from '@beecount/api-client'

import { formatIsoDateTime } from '../format'
import { ListTableShell } from '../components/ListTableShell'
import { StatusBadge } from '../components/StatusBadge'

type ShareMembersPanelProps = {
  members: ShareMember[]
  canManage: boolean
  memberEmail: string
  onMemberEmailChange: (value: string) => void
  onUpsertMember: () => Promise<boolean> | boolean
  onRemoveMember: () => Promise<boolean> | boolean
}

export function ShareMembersPanel({
  members,
  canManage,
  memberEmail,
  onMemberEmailChange,
  onUpsertMember,
  onRemoveMember
}: ShareMembersPanelProps) {
  const t = useT()
  const [brokenAvatarUserIds, setBrokenAvatarUserIds] = useState<Set<string>>(() => new Set())
  const canOperate = canManage && memberEmail.trim().length > 0
  const memberDisplayName = (member: ShareMember): string =>
    member.user_display_name?.trim() ||
    member.user_email?.trim() ||
    member.user_id
  const avatarInitial = (member: ShareMember): string =>
    memberDisplayName(member).trim().charAt(0).toUpperCase() || '?'

  return (
    <>
      <ListTableShell
        title={t('share.members.title')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="h-9 w-[220px] bg-muted"
              placeholder={t('share.members.placeholder.email')}
              value={memberEmail}
              onChange={(e) => onMemberEmailChange(e.target.value)}
            />
            <Button disabled={!canOperate} onClick={onUpsertMember}>
              {t('share.members.button.addOrUpdate')}
            </Button>
            <Button variant="destructive" disabled={!canOperate} onClick={onRemoveMember}>
              {t('share.members.button.remove')}
            </Button>
          </div>
        }
      >
        {!canManage ? (
          <p className="mb-3 text-sm text-muted-foreground">{t('share.members.readOnlyHint')}</p>
        ) : (
          <div className="mb-3 space-y-1 text-sm text-muted-foreground">
            <p>{t('share.members.manageHint')}</p>
            <p>{t('share.members.registeredOnlyHint')}</p>
          </div>
        )}
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">
                  {t('share.members.table.user')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.members.table.role')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.members.table.status')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.members.table.joined')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.members.table.email')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-12 text-center text-sm text-muted-foreground">
                    {t('table.empty')}
                  </TableCell>
                </TableRow>
              ) : null}
              {members.map((member) => (
                <TableRow key={member.user_id} className="odd:bg-muted/20">
                  <TableCell>
                    <div className="flex min-w-[220px] items-center gap-2">
                      {member.user_avatar_url && !brokenAvatarUserIds.has(member.user_id) ? (
                        <img
                          alt={memberDisplayName(member)}
                          className="h-7 w-7 rounded-full border border-border/60 object-cover"
                          src={member.user_avatar_url}
                          onError={() =>
                            setBrokenAvatarUserIds((prev) => {
                              if (prev.has(member.user_id)) return prev
                              const next = new Set(prev)
                              next.add(member.user_id)
                              return next
                            })
                          }
                        />
                      ) : (
                        <div className="flex h-7 w-7 items-center justify-center rounded-full border border-border/60 bg-muted text-xs font-medium text-muted-foreground">
                          {avatarInitial(member)}
                        </div>
                      )}
                      <div className="min-w-0">
                        <p className="truncate text-sm">{memberDisplayName(member)}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {member.user_email || member.user_id}
                        </p>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <StatusBadge value={member.role} />
                  </TableCell>
                  <TableCell>
                    <StatusBadge value={member.status} />
                  </TableCell>
                  <TableCell>{formatIsoDateTime(member.joined_at)}</TableCell>
                  <TableCell>{member.user_email || '-'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </ListTableShell>
    </>
  )
}
