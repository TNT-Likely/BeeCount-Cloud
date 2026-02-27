import { useState } from 'react'

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
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
  useT
} from '@beecount/ui'

type ShareInvitePanelProps = {
  role: 'editor' | 'viewer'
  maxUses: number
  inviteCode: string
  inviteId: string
  canManage: boolean
  onRoleChange: (value: 'editor' | 'viewer') => void
  onMaxUsesChange: (value: number) => void
  onCreateInvite: () => Promise<void> | void
  onRevokeInvite: () => Promise<void> | void
}

export function ShareInvitePanel({
  role,
  maxUses,
  inviteCode,
  inviteId,
  canManage,
  onRoleChange,
  onMaxUsesChange,
  onCreateInvite,
  onRevokeInvite
}: ShareInvitePanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)

  return (
    <Card className="bc-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle>{t('share.invite.title')}</CardTitle>
        <Button disabled={!canManage} onClick={() => setOpen(true)}>
          {t('share.invite.button.create')}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {inviteCode ? (
          <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
            <Input readOnly placeholder={t('share.invite.placeholder.inviteCode')} value={inviteCode} />
            <Input readOnly placeholder={t('share.invite.placeholder.inviteId')} value={inviteId} />
            <Button variant="destructive" disabled={!canManage} onClick={onRevokeInvite}>
              {t('share.invite.button.revoke')}
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t('share.invite.list.title')}</p>
        )}
      </CardContent>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('share.invite.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('share.members.table.role')}</Label>
              <Select value={role} onValueChange={(value) => onRoleChange(value as 'editor' | 'viewer')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="editor">{t('enum.role.editor')}</SelectItem>
                  <SelectItem value="viewer">{t('enum.role.viewer')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('share.invite.placeholder.maxUses')}</Label>
              <Input
                type="number"
                min={1}
                value={maxUses}
                onChange={(e) => onMaxUsesChange(Number(e.target.value || 1))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canManage}
              onClick={async () => {
                await onCreateInvite()
                setOpen(false)
              }}
            >
              {t('share.invite.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
