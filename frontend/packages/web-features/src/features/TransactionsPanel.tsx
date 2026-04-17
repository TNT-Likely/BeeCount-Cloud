import { useEffect, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
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

import type { AttachmentRef, ReadAccount, ReadCategory, ReadTag, ReadTransaction } from '@beecount/api-client'

import { ListTableShell } from '../components/ListTableShell'
import { formatAmountCny, formatIsoDateTime } from '../format'
import type { TxForm } from '../forms'

type TransactionsPanelProps = {
  form: TxForm
  rows: ReadTransaction[]
  total: number
  page: number
  pageSize: number
  accounts: ReadAccount[]
  categories: ReadCategory[]
  tags: ReadTag[]
  ledgerOptions: Array<{ ledger_id: string; ledger_name: string }>
  writeLedgerId: string
  onWriteLedgerIdChange: (ledgerId: string) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  canWrite: boolean
  dictionariesLoading?: boolean
  showCreatorColumn?: boolean
  showLedgerColumn?: boolean
  onFormChange: (next: TxForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onReload: () => void
  onPreviewAttachment: (ref: AttachmentRef) => Promise<void>
  resolveAttachmentPreviewUrl: (ref: AttachmentRef) => Promise<string | null>
  onEdit: (row: ReadTransaction) => void
  onDelete: (row: ReadTransaction) => void
}

type AttachmentCarouselCellProps = {
  attachments: AttachmentRef[]
  onPreviewAttachment: (ref: AttachmentRef) => Promise<void>
  resolveAttachmentPreviewUrl: (ref: AttachmentRef) => Promise<string | null>
  partialLabel: string
  metadataOnlyLabel: string
  notPreviewableLabel: string
  prevLabel: string
  nextLabel: string
}

function AttachmentCarouselCell({
  attachments,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  partialLabel,
  metadataOnlyLabel,
  notPreviewableLabel,
  prevLabel,
  nextLabel
}: AttachmentCarouselCellProps) {
  const [index, setIndex] = useState(0)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const readyAttachments = attachments.filter(
    (attachment) => typeof attachment.cloudFileId === 'string' && attachment.cloudFileId.trim().length > 0
  )
  const current = readyAttachments[index]

  useEffect(() => {
    if (readyAttachments.length === 0) {
      setIndex(0)
      return
    }
    if (index >= readyAttachments.length) {
      setIndex(0)
    }
  }, [index, readyAttachments.length])

  useEffect(() => {
    let cancelled = false
    if (!current) {
      setPreviewUrl(null)
      setLoading(false)
      return () => {
        cancelled = true
      }
    }
    setLoading(true)
    void resolveAttachmentPreviewUrl(current)
      .then((url) => {
        if (cancelled) return
        setPreviewUrl(url)
      })
      .finally(() => {
        if (cancelled) return
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [current, resolveAttachmentPreviewUrl])

  if (attachments.length === 0) return <>-</>

  if (readyAttachments.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="secondary">{attachments.length}</Badge>
        <span className="text-xs text-muted-foreground">{metadataOnlyLabel}</span>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="relative h-24 w-40 overflow-hidden rounded-md border border-border/70 bg-muted/30">
        {previewUrl ? (
          <img
            alt={current?.originalName || current?.fileName || 'attachment-preview'}
            className="h-full w-full cursor-zoom-in object-cover"
            src={previewUrl}
            onClick={() => {
              if (!current) return
              void onPreviewAttachment(current)
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-2 text-center text-[11px] text-muted-foreground">
            {loading ? '...' : notPreviewableLabel}
          </div>
        )}
        {readyAttachments.length > 1 ? (
          <>
            <Button
              aria-label={prevLabel}
              className="absolute left-1 top-1/2 h-6 w-6 -translate-y-1/2 bg-background/90 p-0"
              size="icon"
              type="button"
              variant="outline"
              onClick={() =>
                setIndex((prev) => (prev - 1 + readyAttachments.length) % readyAttachments.length)
              }
            >
              ‹
            </Button>
            <Button
              aria-label={nextLabel}
              className="absolute right-1 top-1/2 h-6 w-6 -translate-y-1/2 bg-background/90 p-0"
              size="icon"
              type="button"
              variant="outline"
              onClick={() => setIndex((prev) => (prev + 1) % readyAttachments.length)}
            >
              ›
            </Button>
          </>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <Badge variant="default">{attachments.length}</Badge>
        {readyAttachments.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {Math.min(index + 1, readyAttachments.length)}/{readyAttachments.length}
          </span>
        ) : null}
        {readyAttachments.length < attachments.length ? (
          <span className="text-xs text-muted-foreground">{partialLabel}</span>
        ) : null}
      </div>
    </div>
  )
}

export function TransactionsPanel({
  form,
  rows,
  total,
  page,
  pageSize,
  accounts,
  categories,
  tags,
  ledgerOptions,
  writeLedgerId,
  onWriteLedgerIdChange,
  onPageChange,
  onPageSizeChange,
  canWrite,
  dictionariesLoading = false,
  showCreatorColumn = false,
  showLedgerColumn = false,
  onFormChange,
  onSave,
  onReset,
  onReload,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  onEdit,
  onDelete
}: TransactionsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  const accountOptions = accounts
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0)
    .filter((name, index, self) => self.indexOf(name) === index)
    .sort((a, b) => a.localeCompare(b))
  const categoryOptions = categories
    .filter((row) => row.kind === form.tx_type)
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0)
    .filter((name, index, self) => self.indexOf(name) === index)
    .sort((a, b) => a.localeCompare(b))
  const tagOptions = tags
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0)
    .filter((name, index, self) => self.indexOf(name) === index)
    .sort((a, b) => a.localeCompare(b))
  // 按 name 反查 tag 颜色，tx 列表行里给每个标签 badge 上色。大小写不敏感。
  const tagColorByName = new Map<string, string>()
  for (const row of tags) {
    const key = (row.name || '').trim().toLowerCase()
    if (!key) continue
    if (row.color && !tagColorByName.has(key)) tagColorByName.set(key, row.color)
  }

  const isTransfer = form.tx_type === 'transfer'
  // 非转账允许不选账户（与 mobile 保持一致，tx.accountId 本来就是 nullable）；
  // 转账必须两端都选（否则无法表达方向）。
  const canSubmit = Boolean(writeLedgerId.trim()) && (isTransfer
    ? Boolean(form.from_account_name.trim()) && Boolean(form.to_account_name.trim())
    : true)
  const selectedTags = form.tags
  const categoryValue = form.category_name.trim()
  const tagsSummary = selectedTags.length === 0 ? t('common.none') : selectedTags.join(', ')

  const applyTxType = (nextType: TxForm['tx_type']) => {
    if (nextType === 'transfer') {
      onFormChange({
        ...form,
        tx_type: nextType,
        account_name: '',
        category_name: '',
        category_kind: 'transfer'
      })
      return
    }
    const keepCategory = form.category_kind === nextType ? form.category_name : ''
    onFormChange({
      ...form,
      tx_type: nextType,
      category_kind: nextType,
      category_name: keepCategory,
      from_account_name: '',
      to_account_name: ''
    })
  }

  const colCount = 8 + (showCreatorColumn ? 1 : 0) + (showLedgerColumn ? 1 : 0)
  const totalPages = Math.max(1, Math.ceil(total / Math.max(pageSize, 1)))
  const safePage = Math.min(Math.max(page, 1), totalPages)
  const rangeStart = total === 0 ? 0 : (safePage - 1) * pageSize + 1
  const rangeEnd = total === 0 ? 0 : Math.min(total, safePage * pageSize)

  return (
    <>
      {/* 同账户/分类/标签：两端模型未对齐前，web 端不提供新建交易。
          Reload 按钮也撤掉 —— WS + polling 已经覆盖刷新，手动 reload 是多余出口。 */}
      <ListTableShell title={t('transactions.title')}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">
                  {t('transactions.table.time')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('transactions.table.type')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('transactions.table.amount')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('transactions.table.category')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('transactions.table.account')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('transactions.table.note')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('tags.title')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('transactions.table.attachments')}
                </TableHead>
                {showLedgerColumn ? (
                  <TableHead className="bc-table-head">
                    {t('transactions.table.ledger')}
                  </TableHead>
                ) : null}
                {showCreatorColumn ? (
                  <TableHead className="bc-table-head">
                    {t('transactions.table.user')}
                  </TableHead>
                ) : null}
                <TableHead className="bc-table-head sticky right-0 z-20 min-w-[132px] bg-card">
                  {t('transactions.table.ops')}
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
              {rows.map((row) => {
                const txAttachments = Array.isArray(row.attachments) ? row.attachments : []
                return (
                  <TableRow
                    key={row.id}
                    className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[132px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
                  >
                    <TableCell>{formatIsoDateTime(row.happened_at)}</TableCell>
                    <TableCell>{t(`enum.txType.${row.tx_type}`)}</TableCell>
                    <TableCell>{formatAmountCny(row.amount)}</TableCell>
                    <TableCell>{row.category_name || '-'}</TableCell>
                    <TableCell>{row.account_name || row.from_account_name || '-'}</TableCell>
                    <TableCell className="max-w-[300px] truncate">{row.note || '-'}</TableCell>
                    <TableCell>
                      {row.tags_list && row.tags_list.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {row.tags_list.map((tagName) => {
                            const color = tagColorByName.get(tagName.trim().toLowerCase())
                            return (
                              <span
                                key={tagName}
                                className="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium"
                                style={
                                  color
                                    ? {
                                        color,
                                        borderColor: `${color}66`,
                                        background: `${color}1a`
                                      }
                                    : undefined
                                }
                              >
                                {tagName}
                              </span>
                            )
                          })}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <AttachmentCarouselCell
                        attachments={txAttachments}
                        metadataOnlyLabel={t('transactions.attachment.metadataOnly')}
                        nextLabel={t('transactions.attachment.next')}
                        notPreviewableLabel={t('transactions.attachment.notPreviewable')}
                        onPreviewAttachment={onPreviewAttachment}
                        partialLabel={t('transactions.attachment.partial')}
                        prevLabel={t('transactions.attachment.prev')}
                        resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl}
                      />
                    </TableCell>
                    {showLedgerColumn ? <TableCell>{row.ledger_name || '-'}</TableCell> : null}
                    {showCreatorColumn ? (
                      <TableCell>{row.created_by_email || row.created_by_user_id || '-'}</TableCell>
                    ) : null}
                    <TableCell>
                      <div className="flex items-center gap-3 whitespace-nowrap">
                        <button
                          className={textActionClass}
                          disabled={!canWrite}
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
                          disabled={!canWrite}
                          type="button"
                          onClick={() => onDelete(row)}
                        >
                          {t('common.delete')}
                        </button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 px-3 py-3">
          <p className="text-xs text-muted-foreground">
            {t('pagination.summary', { start: rangeStart, end: rangeEnd, total })}
          </p>
          <div className="flex items-center gap-2">
            <Select
              value={`${pageSize}`}
              onValueChange={(value) => onPageSizeChange(Number(value))}
            >
              <SelectTrigger className="h-8 w-[110px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="20">{t('pagination.perPage', { size: 20 })}</SelectItem>
                <SelectItem value="50">{t('pagination.perPage', { size: 50 })}</SelectItem>
                <SelectItem value="100">{t('pagination.perPage', { size: 100 })}</SelectItem>
              </SelectContent>
            </Select>
            <Button
              className="h-8 px-3"
              disabled={safePage <= 1}
              size="sm"
              variant="outline"
              onClick={() => onPageChange(safePage - 1)}
            >
              {t('pagination.prev')}
            </Button>
            <span className="min-w-[72px] text-center text-xs text-muted-foreground">
              {safePage}/{totalPages}
            </span>
            <Button
              className="h-8 px-3"
              disabled={safePage >= totalPages}
              size="sm"
              variant="outline"
              onClick={() => onPageChange(safePage + 1)}
            >
              {t('pagination.next')}
            </Button>
          </div>
        </div>
      </ListTableShell>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle>{form.editingId ? t('transactions.button.update') : t('transactions.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
              <Label>{t('shell.ledger')}</Label>
              <Select value={writeLedgerId || undefined} onValueChange={onWriteLedgerIdChange} disabled={Boolean(form.editingId)}>
                <SelectTrigger>
                  <SelectValue placeholder={t('shell.ledger')} />
                </SelectTrigger>
                <SelectContent>
                  {ledgerOptions.map((ledger) => (
                    <SelectItem key={ledger.ledger_id} value={ledger.ledger_id}>
                      {ledger.ledger_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.type')}</Label>
              <Select value={form.tx_type} onValueChange={(value) => applyTxType(value as TxForm['tx_type'])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="expense">{t('enum.txType.expense')}</SelectItem>
                  <SelectItem value="income">{t('enum.txType.income')}</SelectItem>
                  <SelectItem value="transfer">{t('enum.txType.transfer')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.amount')}</Label>
              <Input
                placeholder={t('transactions.placeholder.amount')}
                value={form.amount}
                onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.time')}</Label>
              <Input
                placeholder={t('transactions.placeholder.happenedAt')}
                value={form.happened_at}
                onChange={(e) => onFormChange({ ...form, happened_at: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.category')}</Label>
              {isTransfer ? (
                <Input disabled value={t('common.none')} />
              ) : (
                <Select
                  value={categoryValue || '__none__'}
                  disabled={dictionariesLoading}
                  onValueChange={(value) =>
                    onFormChange({
                      ...form,
                      category_name: value === '__none__' ? '' : value,
                      category_kind: form.tx_type
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('transactions.placeholder.categoryName')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('common.none')}</SelectItem>
                    {categoryOptions.map((name) => (
                      <SelectItem key={name} value={name}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            {isTransfer ? (
              <>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.fromAccountName')}</Label>
                  <Select
                    value={form.from_account_name || undefined}
                    disabled={dictionariesLoading}
                    onValueChange={(value) => onFormChange({ ...form, from_account_name: value })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('transactions.placeholder.fromAccountName')} />
                    </SelectTrigger>
                    <SelectContent>
                      {accountOptions.map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.toAccountName')}</Label>
                  <Select
                    value={form.to_account_name || undefined}
                    disabled={dictionariesLoading}
                    onValueChange={(value) => onFormChange({ ...form, to_account_name: value })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('transactions.placeholder.toAccountName')} />
                    </SelectTrigger>
                    <SelectContent>
                      {accountOptions.map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            ) : (
              <div className="space-y-1">
                <Label>{t('accounts.title')}</Label>
                <Select
                  value={form.account_name || undefined}
                  disabled={dictionariesLoading}
                  onValueChange={(value) => onFormChange({ ...form, account_name: value })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('transactions.placeholder.accountName')} />
                  </SelectTrigger>
                  <SelectContent>
                    {accountOptions.map((name) => (
                      <SelectItem key={name} value={name}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="space-y-1">
              <Label>{t('tags.title')}</Label>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button className="w-full justify-between" disabled={dictionariesLoading} variant="outline">
                    <span className="truncate text-left">{tagsSummary}</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="max-h-64 w-[320px] overflow-y-auto border-border/60 bg-popover text-popover-foreground shadow-lg"
                >
                  <DropdownMenuLabel>{t('tags.title')}</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {tagOptions.map((name) => (
                    <DropdownMenuCheckboxItem
                      key={name}
                      checked={selectedTags.includes(name)}
                      onCheckedChange={(checked) => {
                        if (checked) {
                          if (selectedTags.includes(name)) return
                          onFormChange({ ...form, tags: [...selectedTags, name] })
                          return
                        }
                        onFormChange({ ...form, tags: selectedTags.filter((value) => value !== name) })
                      }}
                      onSelect={(event) => event.preventDefault()}
                    >
                      {name}
                    </DropdownMenuCheckboxItem>
                  ))}
                  <DropdownMenuSeparator />
                  <Button
                    className="mx-1 h-8 w-[calc(100%-0.5rem)]"
                    size="sm"
                    variant="ghost"
                    onClick={() => onFormChange({ ...form, tags: [] })}
                  >
                    {t('tags.button.reset')}
                  </Button>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <div className="space-y-1 md:col-span-2">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                placeholder={t('transactions.placeholder.note')}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>
          </div>
          </div>
          <DialogFooter className="shrink-0 border-t border-border/60 bg-card px-6 py-4">
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
              disabled={!canWrite || !canSubmit}
              onClick={async () => {
                const success = await onSave()
                if (success) {
                  setOpen(false)
                }
              }}
            >
              {form.editingId ? t('transactions.button.update') : t('transactions.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
