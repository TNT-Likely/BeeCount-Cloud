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
  onUploadAttachments: (files: File[]) => Promise<AttachmentRef[]>
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
  onUploadAttachments,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  onEdit,
  onDelete
}: TransactionsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [uploadingAttachments, setUploadingAttachments] = useState(false)
  const [dialogAttachmentIndex, setDialogAttachmentIndex] = useState(0)
  const [dialogAttachmentPreviewUrl, setDialogAttachmentPreviewUrl] = useState<string | null>(null)
  const [dialogAttachmentPreviewLoading, setDialogAttachmentPreviewLoading] = useState(false)
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

  const isTransfer = form.tx_type === 'transfer'
  const canSubmit = Boolean(writeLedgerId.trim()) && (isTransfer
    ? Boolean(form.from_account_name.trim()) && Boolean(form.to_account_name.trim())
    : Boolean(form.account_name.trim()))
  const selectedTags = form.tags
  const categoryValue = form.category_name.trim()
  const tagsSummary = selectedTags.length === 0 ? t('common.none') : selectedTags.join(', ')
  const attachmentRows = [...form.attachments].sort(
    (a, b) => (a.sortOrder ?? Number.MAX_SAFE_INTEGER) - (b.sortOrder ?? Number.MAX_SAFE_INTEGER)
  )
  const currentDialogAttachment = attachmentRows[dialogAttachmentIndex]
  const currentDialogAttachmentKey = currentDialogAttachment
    ? `${currentDialogAttachment.cloudFileId ?? ''}:${currentDialogAttachment.cloudSha256 ?? ''}:${currentDialogAttachment.fileName ?? ''}:${currentDialogAttachment.originalName ?? ''}`
    : ''
  const currentDialogAttachmentReady =
    typeof currentDialogAttachment?.cloudFileId === 'string' &&
    currentDialogAttachment.cloudFileId.trim().length > 0

  const setAttachments = (next: AttachmentRef[]) => {
    onFormChange({
      ...form,
      attachments: next.map((item, idx) => ({
        ...item,
        sortOrder: idx
      }))
    })
  }

  const moveAttachment = (index: number, offset: -1 | 1) => {
    const target = index + offset
    if (target < 0 || target >= attachmentRows.length) return
    const next = [...attachmentRows]
    const [picked] = next.splice(index, 1)
    next.splice(target, 0, picked)
    setAttachments(next)
  }

  const removeAttachment = (index: number) => {
    const next = [...attachmentRows]
    next.splice(index, 1)
    setAttachments(next)
  }

  const addAttachments = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploadingAttachments(true)
    try {
      const incoming = await onUploadAttachments(Array.from(files))
      if (incoming.length === 0) return
      setAttachments([...attachmentRows, ...incoming])
    } finally {
      setUploadingAttachments(false)
    }
  }

  useEffect(() => {
    if (attachmentRows.length === 0) {
      setDialogAttachmentIndex(0)
      return
    }
    if (dialogAttachmentIndex >= attachmentRows.length) {
      setDialogAttachmentIndex(attachmentRows.length - 1)
    }
  }, [attachmentRows.length, dialogAttachmentIndex])

  useEffect(() => {
    let cancelled = false
    if (!open || !currentDialogAttachment || !currentDialogAttachmentReady) {
      setDialogAttachmentPreviewUrl(null)
      setDialogAttachmentPreviewLoading(false)
      return () => {
        cancelled = true
      }
    }
    setDialogAttachmentPreviewLoading(true)
    void resolveAttachmentPreviewUrl(currentDialogAttachment)
      .then((url) => {
        if (cancelled) return
        setDialogAttachmentPreviewUrl(url)
      })
      .finally(() => {
        if (cancelled) return
        setDialogAttachmentPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, currentDialogAttachmentKey, currentDialogAttachmentReady, resolveAttachmentPreviewUrl])

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

  const colCount = 7 + (showCreatorColumn ? 1 : 0) + (showLedgerColumn ? 1 : 0)
  const totalPages = Math.max(1, Math.ceil(total / Math.max(pageSize, 1)))
  const safePage = Math.min(Math.max(page, 1), totalPages)
  const rangeStart = total === 0 ? 0 : (safePage - 1) * pageSize + 1
  const rangeEnd = total === 0 ? 0 : Math.min(total, safePage * pageSize)

  return (
    <>
      <ListTableShell
        title={t('transactions.title')}
        actions={
          <>
            <Button variant="outline" onClick={onReload}>
              {t('transactions.button.reload')}
            </Button>
            <Button
              disabled={!canWrite}
              onClick={() => {
                onReset()
                setDialogAttachmentIndex(0)
                setOpen(true)
              }}
            >
              {t('transactions.button.create')}
            </Button>
          </>
        }
      >
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
                            setDialogAttachmentIndex(0)
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
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-hidden p-0">
          <DialogHeader className="px-6 pt-6">
            <DialogTitle>{form.editingId ? t('transactions.button.update') : t('transactions.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="overflow-y-auto px-6 pb-4">
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
                <DropdownMenuContent align="start" className="max-h-64 w-[320px] overflow-y-auto">
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
            <div className="space-y-2 md:col-span-2">
              <div className="flex items-center justify-between gap-2">
                <Label>{t('transactions.table.attachments')}</Label>
                <label className="inline-flex cursor-pointer">
                  <input
                    className="hidden"
                    type="file"
                    multiple
                    onChange={async (event) => {
                      await addAttachments(event.target.files)
                      event.currentTarget.value = ''
                    }}
                  />
                  <Button disabled={!canWrite || !writeLedgerId || uploadingAttachments} size="sm" variant="outline">
                    {uploadingAttachments
                      ? t('transactions.attachment.uploading')
                      : t('transactions.attachment.upload')}
                  </Button>
                </label>
              </div>
              {dictionariesLoading ? (
                <p className="text-xs text-muted-foreground">{t('transactions.dictionary.loading')}</p>
              ) : null}
              <div className="space-y-2 rounded-md border border-border/70 bg-muted/30 p-3">
                {attachmentRows.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{t('transactions.attachment.empty')}</p>
                ) : (
                  <div className="space-y-3">
                    <div className="relative overflow-hidden rounded-md border border-border/60 bg-background">
                      <div className="flex h-56 items-center justify-center bg-muted/30">
                        {dialogAttachmentPreviewUrl ? (
                          <img
                            alt={currentDialogAttachment?.originalName || currentDialogAttachment?.fileName || 'attachment-preview'}
                            className="h-full w-full cursor-zoom-in object-contain"
                            src={dialogAttachmentPreviewUrl}
                            onClick={() => {
                              if (!currentDialogAttachment || !currentDialogAttachmentReady) return
                              void onPreviewAttachment(currentDialogAttachment)
                            }}
                          />
                        ) : (
                          <p className="px-3 text-center text-xs text-muted-foreground">
                            {dialogAttachmentPreviewLoading
                              ? '...'
                              : currentDialogAttachmentReady
                                ? t('transactions.attachment.notPreviewable')
                                : t('transactions.attachment.metadataOnly')}
                          </p>
                        )}
                      </div>
                      {attachmentRows.length > 1 ? (
                        <>
                          <button
                            aria-label={t('transactions.attachment.prev')}
                            className="absolute left-2 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full border border-border/70 bg-background/90 text-sm"
                            type="button"
                            onClick={() =>
                              setDialogAttachmentIndex((prev) => (prev - 1 + attachmentRows.length) % attachmentRows.length)
                            }
                          >
                            ‹
                          </button>
                          <button
                            aria-label={t('transactions.attachment.next')}
                            className="absolute right-2 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full border border-border/70 bg-background/90 text-sm"
                            type="button"
                            onClick={() => setDialogAttachmentIndex((prev) => (prev + 1) % attachmentRows.length)}
                          >
                            ›
                          </button>
                        </>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {currentDialogAttachment?.originalName || currentDialogAttachment?.fileName || '-'}
                        </p>
                        <p className="text-muted-foreground">
                          {typeof currentDialogAttachment?.fileSize === 'number'
                            ? `${Math.round(currentDialogAttachment.fileSize / 1024)} KB`
                            : '-'}
                          {currentDialogAttachment?.width && currentDialogAttachment?.height
                            ? ` · ${currentDialogAttachment.width}x${currentDialogAttachment.height}`
                            : ''}
                          {` · ${Math.min(dialogAttachmentIndex + 1, attachmentRows.length)}/${attachmentRows.length}`}
                        </p>
                      </div>
                      <Badge variant={currentDialogAttachmentReady ? 'default' : 'secondary'}>
                        {currentDialogAttachmentReady
                          ? t('transactions.attachment.ready')
                          : t('transactions.attachment.metadataOnly')}
                      </Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                      <button
                        className={textActionClass}
                        disabled={attachmentRows.length <= 1}
                        type="button"
                        onClick={() =>
                          setDialogAttachmentIndex((prev) => (prev - 1 + attachmentRows.length) % attachmentRows.length)
                        }
                      >
                        {t('transactions.attachment.prev')}
                      </button>
                      <button
                        className={textActionClass}
                        disabled={attachmentRows.length <= 1}
                        type="button"
                        onClick={() => setDialogAttachmentIndex((prev) => (prev + 1) % attachmentRows.length)}
                      >
                        {t('transactions.attachment.next')}
                      </button>
                      <button
                        className={textActionClass}
                        disabled={!currentDialogAttachmentReady || !currentDialogAttachment}
                        type="button"
                        onClick={() => {
                          if (!currentDialogAttachment) return
                          void onPreviewAttachment(currentDialogAttachment)
                        }}
                      >
                        {t('transactions.attachment.preview')}
                      </button>
                      <button
                        className={textActionClass}
                        disabled={dialogAttachmentIndex === 0}
                        type="button"
                        onClick={() => moveAttachment(dialogAttachmentIndex, -1)}
                      >
                        {t('transactions.attachment.movePrev')}
                      </button>
                      <button
                        className={textActionClass}
                        disabled={dialogAttachmentIndex >= attachmentRows.length - 1}
                        type="button"
                        onClick={() => moveAttachment(dialogAttachmentIndex, 1)}
                      >
                        {t('transactions.attachment.moveNext')}
                      </button>
                      <button
                        className={textDangerActionClass}
                        type="button"
                        onClick={() => removeAttachment(dialogAttachmentIndex)}
                      >
                        {t('transactions.attachment.remove')}
                      </button>
                    </div>
                    {attachmentRows.some(
                      (item) =>
                        typeof item.cloudFileId !== 'string' || item.cloudFileId.trim().length === 0
                    ) ? (
                      <p className="text-xs text-muted-foreground">{t('transactions.attachment.partial')}</p>
                    ) : null}
                  </div>
                )}
              </div>
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
          <DialogFooter className="border-t border-border/60 px-6 py-4">
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
