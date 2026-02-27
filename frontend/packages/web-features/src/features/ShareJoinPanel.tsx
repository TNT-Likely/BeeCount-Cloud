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
  useT
} from '@beecount/ui'

type ShareJoinPanelProps = {
  inviteCode: string
  onInviteCodeChange: (value: string) => void
  onJoin: () => Promise<void> | void
  onLeave: () => Promise<void> | void
}

export function ShareJoinPanel({ inviteCode, onInviteCodeChange, onJoin, onLeave }: ShareJoinPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)

  return (
    <Card className="bc-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle>{t('share.join.title')}</CardTitle>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={onLeave}>
            {t('share.join.button.leave')}
          </Button>
          <Button onClick={() => setOpen(true)}>{t('share.join.button.join')}</Button>
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{t('share.join.placeholder.inviteCode')}</p>
      </CardContent>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('share.join.button.join')}</DialogTitle>
          </DialogHeader>
          <Input
            placeholder={t('share.join.placeholder.inviteCode')}
            value={inviteCode}
            onChange={(e) => onInviteCodeChange(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              onClick={async () => {
                await onJoin()
                setOpen(false)
              }}
            >
              {t('share.join.button.join')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
