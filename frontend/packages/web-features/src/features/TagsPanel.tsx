import { useState, type ChangeEvent } from 'react'

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

import type { ReadTag } from '@beecount/api-client'

import type { TagForm } from '../forms'
import { ListTableShell } from '../components/ListTableShell'

type TagsPanelProps = {
  form: TagForm
  rows: ReadTag[]
  canManage: boolean
  showCreatorColumn?: boolean
  onFormChange: (next: TagForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadTag) => void
  onDelete: (row: ReadTag) => void
}

export function TagsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete
}: TagsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const colCount = 2 + (showCreatorColumn ? 1 : 0)
  const colors = ['#F59E0B', '#EF4444', '#10B981', '#3B82F6', '#8B5CF6', '#EC4899', '#6B7280']
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  return (
    <>
      <ListTableShell
        title={t('tags.title')}
        actions={
          <Button
            disabled={!canManage}
            onClick={() => {
              onReset()
              setOpen(true)
            }}
          >
            {t('tags.button.create')}
          </Button>
        }
      >
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">{t('tags.table.name')}</TableHead>
                <TableHead className="bc-table-head">{t('tags.table.color')}</TableHead>
                {showCreatorColumn ? (
                  <TableHead className="bc-table-head">
                    {t('transactions.table.user')}
                  </TableHead>
                ) : null}
                <TableHead className="bc-table-head sticky right-0 z-20 min-w-[132px] bg-card">
                  {t('tags.table.ops')}
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
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span
                        className="h-4 w-4 rounded-full border border-border"
                        style={{ background: row.color || '#F59E0B' }}
                      />
                      {row.color || '-'}
                    </div>
                  </TableCell>
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
            <DialogTitle>{form.editingId ? t('tags.button.update') : t('tags.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('tags.table.name')}</Label>
              <Input
                placeholder={t('tags.placeholder.name')}
                value={form.name}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  onFormChange({ ...form, name: event.target.value })
                }
              />
            </div>
            <div className="space-y-1">
              <Label>{t('tags.table.color')}</Label>
              <Select value={form.color || '#F59E0B'} onValueChange={(value) => onFormChange({ ...form, color: value })}>
                <SelectTrigger>
                  <SelectValue placeholder={t('tags.placeholder.color')} />
                </SelectTrigger>
                <SelectContent>
                  {colors.map((color) => (
                    <SelectItem key={color} value={color}>
                      {color}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                <span className="h-4 w-4 rounded-full border border-border" style={{ background: form.color || '#F59E0B' }} />
                {t('tags.preview')}
              </div>
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
              {form.editingId ? t('tags.button.update') : t('tags.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
