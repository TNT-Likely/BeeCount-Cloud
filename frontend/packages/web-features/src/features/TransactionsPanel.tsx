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
  useT
} from '@beecount/ui'

import type { AttachmentRef, ReadAccount, ReadCategory, ReadTag, ReadTransaction } from '@beecount/api-client'

import { TransactionList } from '../components/TransactionList'
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
  onPreviewAttachment: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
  resolveAttachmentPreviewUrl: (ref: AttachmentRef) => Promise<string | null>
  onEdit: (row: ReadTransaction) => void
  onDelete: (row: ReadTransaction) => void
}

type AttachmentCarouselCellProps = {
  attachments: AttachmentRef[]
  onPreviewAttachment: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
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
              void onPreviewAttachment(readyAttachments, index)
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
      {/* 去掉 ListTableShell 的"交易管理" header，改用紧凑的 TransactionList
          —— 表格信息列太多，首页/交易页都不需要账本 + 创建人那两列。 */}
      <div className="rounded-xl border border-border/50 bg-card">
        <TransactionList
          items={rows}
          tags={tags}
          variant="default"
          canManage={canWrite}
          onEdit={(row) => {
            onEdit(row)
            setOpen(true)
          }}
          onDelete={onDelete}
          onPreviewAttachment={onPreviewAttachment}
          resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl}
        />
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
      </div>

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
                type="datetime-local"
                step={60}
                value={isoToDatetimeLocal(form.happened_at)}
                onChange={(e) =>
                  onFormChange({
                    ...form,
                    happened_at: datetimeLocalToIso(e.target.value, form.happened_at)
                  })
                }
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

/**
 * 把后端 ISO 时间（可能带 Z / 毫秒 / 时区 offset）转成 `<input type="datetime-local">`
 * 期望的 `YYYY-MM-DDTHH:mm` 字符串。用本地时区展示，避免用户看到的时间跟记录
 * 时间错位一个时区。
 */
function isoToDatetimeLocal(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * datetime-local 返回的本地时间字符串反序列化成后端想要的 ISO。保留原 value
 * 的秒与时区（避免用户只改了分钟却把秒抹 0 + 跨时区）。
 */
function datetimeLocalToIso(local: string, fallback: string): string {
  if (!local) return fallback
  // `new Date('2026-04-17T23:32')` 会按本地时区解析；toISOString() 再转 UTC。
  const d = new Date(local)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toISOString()
}
