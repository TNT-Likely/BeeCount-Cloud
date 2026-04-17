import { useState, type ChangeEvent } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
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
  /** 按 tag.id 查询的统计（交易数/支出/收入），未传则不展开详情。 */
  statsById?: Record<string, { count: number; expense: number; income: number }>
  onFormChange: (next: TagForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadTag) => void
  onDelete?: (row: ReadTag) => void
}

export function TagsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  statsById,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete
}: TagsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const hasStats = Boolean(statsById)
  // 无色列：name + ops(+stats +creator)
  const colCount = (hasStats ? 4 : 1) + (showCreatorColumn ? 1 : 0)
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  return (
    <>
      {/* 同账户/分类：两端模型未对齐前屏蔽 web 新建标签。保留编辑入口。 */}
      <ListTableShell title={t('tags.title')}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">{t('tags.table.name')}</TableHead>
                {hasStats ? (
                  <>
                    <TableHead className="bc-table-head text-right">笔数</TableHead>
                    <TableHead className="bc-table-head text-right">支出</TableHead>
                    <TableHead className="bc-table-head text-right">收入</TableHead>
                  </>
                ) : null}
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
                  <TableCell colSpan={colCount} className="p-0">
                    <EmptyState
                      icon={
                        <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                             stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                             strokeLinejoin="round">
                          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                          <circle cx="7" cy="7" r="1.5" />
                        </svg>
                      }
                      title="还没有标签"
                      description={'标签可以给交易打备注。点击"新建标签"开始。'}
                    />
                  </TableCell>
                </TableRow>
              ) : null}
              {rows.map((row) => {
                const stats = statsById?.[row.id]
                return (
                <TableRow
                  key={row.id}
                  className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[132px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
                >
                  <TableCell>
                    <span className="inline-flex items-center gap-2">
                      {/* 颜色只读展示，不在 web 端编辑。 */}
                      <span
                        className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border border-border/50"
                        style={{ background: row.color || '#94a3b8' }}
                        aria-hidden
                      />
                      <span>{row.name}</span>
                    </span>
                  </TableCell>
                  {hasStats ? (
                    <>
                      <TableCell className="text-right">{stats?.count ?? 0}</TableCell>
                      <TableCell className="text-right text-destructive">
                        {stats ? stats.expense.toFixed(2) : '0.00'}
                      </TableCell>
                      <TableCell className="text-right text-emerald-600">
                        {stats ? stats.income.toFixed(2) : '0.00'}
                      </TableCell>
                    </>
                  ) : null}
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
                      {onDelete ? (
                        <button
                          className={textDangerActionClass}
                          disabled={!canManage}
                          type="button"
                          onClick={() => onDelete(row)}
                        >
                          {t('common.delete')}
                        </button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
                )
              })}
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
