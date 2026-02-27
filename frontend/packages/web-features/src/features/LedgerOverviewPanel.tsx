import { useState } from 'react'

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT
} from '@beecount/ui'

import type { ReadLedger } from '@beecount/api-client'

type LedgerOverviewPanelProps = {
  ledgers: ReadLedger[]
  selectedLedger: ReadLedger | null
  canManageMeta: boolean
  createDialogOpen: boolean
  createLedgerName: string
  createCurrency: string
  editLedgerName: string
  editCurrency: string
  onCreateDialogOpenChange: (open: boolean) => void
  onCreateLedgerNameChange: (value: string) => void
  onCreateCurrencyChange: (value: string) => void
  onEditLedgerNameChange: (value: string) => void
  onEditCurrencyChange: (value: string) => void
  onCreateLedger: () => Promise<void> | void
  onUpdateLedgerMeta: () => Promise<void> | void
}

export function LedgerOverviewPanel({
  ledgers,
  selectedLedger,
  canManageMeta,
  createDialogOpen,
  createLedgerName,
  createCurrency,
  editLedgerName,
  editCurrency,
  onCreateDialogOpenChange,
  onCreateLedgerNameChange,
  onCreateCurrencyChange,
  onEditLedgerNameChange,
  onEditCurrencyChange,
  onCreateLedger,
  onUpdateLedgerMeta
}: LedgerOverviewPanelProps) {
  const t = useT()
  const [metaOpen, setMetaOpen] = useState(false)

  return (
    <Card className="bc-panel">
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1">
          <CardTitle>{t('nav.overview')}</CardTitle>
          <CardDescription>{t('overview.createLedger.hint', { count: ledgers.length })}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => onCreateDialogOpenChange(true)}>{t('overview.createLedger.button.create')}</Button>
          <Button
            variant="outline"
            disabled={!selectedLedger || !canManageMeta}
            onClick={() => setMetaOpen(true)}
          >
            {t('overview.ledgerMeta.button.save')}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {ledgers.length === 0 ? <p className="text-sm text-muted-foreground">{t('overview.empty')}</p> : null}
        {ledgers.map((ledger) => (
          <div
            key={ledger.ledger_id}
            className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2"
          >
            <div className="space-y-1">
              <p className="text-sm font-medium">{ledger.ledger_name}</p>
              <p className="text-xs text-muted-foreground">{ledger.ledger_id}</p>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <Badge variant="outline">{t(`enum.role.${ledger.role}`)}</Badge>
              <Badge variant="secondary">{ledger.currency}</Badge>
            </div>
          </div>
        ))}
      </CardContent>

      <Dialog open={createDialogOpen} onOpenChange={onCreateDialogOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('overview.createLedger.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('overview.createLedger.name')}</Label>
              <Input value={createLedgerName} onChange={(e) => onCreateLedgerNameChange(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>{t('overview.createLedger.currency')}</Label>
              <Input value={createCurrency} onChange={(e) => onCreateCurrencyChange(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => onCreateDialogOpenChange(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button onClick={() => void onCreateLedger()}>
              {t('overview.createLedger.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={metaOpen} onOpenChange={setMetaOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('overview.ledgerMeta.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('overview.ledgerMeta.name')}</Label>
              <Input
                value={editLedgerName}
                disabled={!selectedLedger || !canManageMeta}
                onChange={(e) => onEditLedgerNameChange(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('overview.ledgerMeta.currency')}</Label>
              <Input
                value={editCurrency}
                disabled={!selectedLedger || !canManageMeta}
                onChange={(e) => onEditCurrencyChange(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMetaOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!selectedLedger || !canManageMeta}
              onClick={async () => {
                await onUpdateLedgerMeta()
                setMetaOpen(false)
              }}
            >
              {t('overview.ledgerMeta.button.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
