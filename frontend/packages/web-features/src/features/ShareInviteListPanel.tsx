import {
  Badge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'

import type { ShareInviteListItem } from '@beecount/api-client'

import { formatIsoDateTime } from '../format'
import { ListTableShell } from '../components/ListTableShell'

type ShareInviteListPanelProps = {
  rows: ShareInviteListItem[]
}

function statusVariant(status: ShareInviteListItem['status']): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'active') return 'default'
  if (status === 'revoked') return 'destructive'
  if (status === 'expired' || status === 'exhausted') return 'secondary'
  return 'outline'
}

export function ShareInviteListPanel({ rows }: ShareInviteListPanelProps) {
  const t = useT()

  return (
    <ListTableShell title={t('share.invite.list.title')}>
      <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">
                  {t('share.invite.list.inviteId')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.invite.list.role')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.invite.list.status')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.invite.list.usage')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.invite.list.expiresAt')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('share.invite.list.createdAt')}
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
                <TableRow key={row.invite_id} className="odd:bg-muted/20">
                  <TableCell className="max-w-[220px] truncate">{row.invite_id}</TableCell>
                  <TableCell>{t(`enum.role.${row.role}`)}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(row.status)}>{t(`enum.inviteStatus.${row.status}`)}</Badge>
                  </TableCell>
                  <TableCell>
                    {row.used_count} / {row.max_uses ?? t('share.invite.list.unlimited')}
                  </TableCell>
                  <TableCell>{formatIsoDateTime(row.expires_at)}</TableCell>
                  <TableCell>{formatIsoDateTime(row.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
      </div>
    </ListTableShell>
  )
}
