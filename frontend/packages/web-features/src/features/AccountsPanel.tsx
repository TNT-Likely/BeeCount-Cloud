import { useState } from 'react'

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

import type { ReadAccount } from '@beecount/api-client'

import { formatAmountCny } from '../format'
import type { AccountForm } from '../forms'
import { ListTableShell } from '../components/ListTableShell'

type AccountsPanelProps = {
  form: AccountForm
  rows: ReadAccount[]
  canManage: boolean
  showCreatorColumn?: boolean
  onFormChange: (next: AccountForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadAccount) => void
  onDelete: (row: ReadAccount) => void
}

export function AccountsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete
}: AccountsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const colCount = 4 + (showCreatorColumn ? 1 : 0)
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  return (
    <>
      <ListTableShell
        title={t('accounts.title')}
        actions={
          <Button
            disabled={!canManage}
            onClick={() => {
              onReset()
              setOpen(true)
            }}
          >
            {t('accounts.button.create')}
          </Button>
        }
      >
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">{t('accounts.table.name')}</TableHead>
                <TableHead className="bc-table-head">{t('accounts.table.type')}</TableHead>
                <TableHead className="bc-table-head">
                  {t('accounts.table.currency')}
                </TableHead>
                <TableHead className="bc-table-head">{t('accounts.table.init')}</TableHead>
                {showCreatorColumn ? (
                  <TableHead className="bc-table-head">
                    {t('transactions.table.user')}
                  </TableHead>
                ) : null}
                <TableHead className="bc-table-head sticky right-0 z-20 min-w-[132px] bg-card">
                  {t('accounts.table.ops')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={colCount} className="py-12 text-center text-sm text-muted-foreground">
                    {t('table.empty')}
                  </TableCell>
                </TableRow>
              ) : null}
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[132px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
                >
                  <TableCell>{row.name}</TableCell>
                  <TableCell>{row.account_type || '-'}</TableCell>
                  <TableCell>{row.currency || '-'}</TableCell>
                  <TableCell>{row.initial_balance === null ? '-' : formatAmountCny(row.initial_balance)}</TableCell>
                  {showCreatorColumn ? <TableCell>{row.created_by_email || row.created_by_user_id || '-'}</TableCell> : null}
                  <TableCell>
                    <div className="flex items-center gap-3 whitespace-nowrap">
                      <button
                        className={textActionClass}
                        disabled={!canManage}
                        type="button"
                        onClick={() => {
                          onEdit(row)
                          setOpen(true)
                        }}
                      >
                        {t('common.edit')}
                      </button>
                      <button
                        className={textDangerActionClass}
                        disabled={!canManage}
                        type="button"
                        onClick={() => onDelete(row)}
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
      </ListTableShell>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form.editingId ? t('accounts.button.update') : t('accounts.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('accounts.table.name')}</Label>
              <Input
                placeholder={t('accounts.placeholder.name')}
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('accounts.table.type')}</Label>
                <Select value={form.account_type || 'cash'} onValueChange={(value) => onFormChange({ ...form, account_type: value })}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.placeholder.type')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cash">cash</SelectItem>
                    <SelectItem value="bank">bank</SelectItem>
                    <SelectItem value="credit">credit</SelectItem>
                    <SelectItem value="investment">investment</SelectItem>
                    <SelectItem value="other">other</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('accounts.table.currency')}</Label>
                <Select value={form.currency || 'CNY'} onValueChange={(value) => onFormChange({ ...form, currency: value })}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.placeholder.currency')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CNY">CNY</SelectItem>
                    <SelectItem value="USD">USD</SelectItem>
                    <SelectItem value="HKD">HKD</SelectItem>
                    <SelectItem value="EUR">EUR</SelectItem>
                    <SelectItem value="JPY">JPY</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('accounts.table.init')}</Label>
              <Input
                placeholder={t('accounts.placeholder.initialBalance')}
                value={form.initial_balance}
                onChange={(e) => onFormChange({ ...form, initial_balance: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                onReset()
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canManage}
              onClick={async () => {
                const success = await onSave()
                if (success) {
                  setOpen(false)
                }
              }}
            >
              {form.editingId ? t('accounts.button.update') : t('accounts.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
