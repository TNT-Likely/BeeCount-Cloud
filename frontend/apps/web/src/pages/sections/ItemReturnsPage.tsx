import { useCallback, useEffect, useMemo, useState } from 'react'

import { Pencil, Plus, Trash2 } from 'lucide-react'

import {
  createItem,
  deleteItem,
  fetchReadItemTransactions,
  fetchReadItems,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  updateItem,
  type ItemUpdatePayload,
  type ReadItem,
  type ReadTransaction,
  type WorkspaceAccount,
  type WorkspaceCategory,
} from '@beecount/api-client'
import {
  Badge,
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
  Textarea,
  useT,
  useToast,
} from '@beecount/ui'
import { groupItemsByStatus, itemCostParts, warrantyStatusOf } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 归物页 · 物品持有管理(0029)。
 *
 * 一件物品 = 购买日期 + 金额 + 保修,按存活时间摊出日均成本;保修到期只是
 * 「过保」(物品仍存活、继续摊),只有二手卖出或报废才停止记录(天数定格)。
 * 交易可以在记账时挂到物品上,金额计入累计成本,这里能列出全部关联交易。
 */

type ItemForm = {
  editingId: string | null
  name: string
  purchase_date: string
  purchase_amount: string
  warranty_until: string
  status: 'in_use' | 'disposed'
  disposal_type: 'sold' | 'scrapped'
  disposal_date: string
  disposal_amount: string
  category_id: string
  category_name: string
  account_id: string
  account_name: string
  note: string
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function emptyForm(): ItemForm {
  return {
    editingId: null,
    name: '',
    purchase_date: todayISO(),
    purchase_amount: '',
    warranty_until: '',
    status: 'in_use',
    disposal_type: 'sold',
    disposal_date: todayISO(),
    disposal_amount: '',
    category_id: '',
    category_name: '',
    account_id: '',
    account_name: '',
    note: '',
  }
}

function dateOnly(value: string | null | undefined): string {
  return value ? String(value).slice(0, 10) : ''
}

function itemToForm(item: ReadItem): ItemForm {
  return {
    editingId: item.id,
    name: item.name || '',
    purchase_date: dateOnly(item.purchase_date),
    purchase_amount: String(item.purchase_amount ?? ''),
    warranty_until: dateOnly(item.warranty_until),
    status: item.status === 'disposed' ? 'disposed' : 'in_use',
    disposal_type: item.disposal_type === 'scrapped' ? 'scrapped' : 'sold',
    disposal_date: dateOnly(item.disposal_date) || todayISO(),
    disposal_amount:
      item.disposal_amount === null || item.disposal_amount === undefined
        ? ''
        : String(item.disposal_amount),
    category_id: item.category_id || '',
    category_name: item.category_name || '',
    account_id: item.account_id || '',
    account_name: item.account_name || '',
    note: item.note || '',
  }
}

function money(value: number, currency: string): string {
  return `${value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`
}

export function ItemReturnsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [items, setItems] = usePageCache<ReadItem[]>(`items:${bucket}:rows`, [])
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('items:accounts', [])
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>('items:categories', [])

  const [form, setForm] = useState<ItemForm>(emptyForm())
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  /** 详情弹窗:物品 + 它的关联交易。 */
  const [detail, setDetail] = useState<{
    item: ReadItem
    txs: ReadTransaction[]
  } | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast],
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setItems([])
      return
    }
    try {
      const [list, accts, cats] = await Promise.all([
        fetchReadItems(token, activeLedgerId),
        fetchWorkspaceAccounts(token, {}),
        fetchWorkspaceCategories(token, {}),
      ])
      setItems(list)
      setAccounts(accts)
      setCategories(cats)
    } catch (err) {
      notifyError(err)
    }
    // setItems / setAccounts / setCategories 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  const accountOptions = useMemo(
    () =>
      accounts
        .filter((a) => (a.name || '').trim())
        .map((a) => ({ id: a.id, name: (a.name || '').trim() })),
    [accounts],
  )

  const groups = useMemo(() => groupItemsByStatus(items), [items])

  const openCreate = useCallback(() => {
    setForm(emptyForm())
    setDialogOpen(true)
  }, [])

  const openEdit = useCallback((item: ReadItem) => {
    setForm(itemToForm(item))
    setDialogOpen(true)
  }, [])

  const openDetail = useCallback(
    async (item: ReadItem) => {
      setDetail({ item, txs: [] })
      setLoadingDetail(true)
      try {
        const txs = await fetchReadItemTransactions(token, item.ledger_id || activeLedgerId || '', item.id)
        setDetail({ item, txs })
      } catch (err) {
        notifyError(err)
      } finally {
        setLoadingDetail(false)
      }
    },
    [token, activeLedgerId, notifyError],
  )

  const buildPayload = useCallback((f: ItemForm) => {
    const amount = f.purchase_amount.trim()
    const isDisposed = f.status === 'disposed'
    const income = f.disposal_amount.trim()
    return {
      name: f.name.trim(),
      purchase_date: f.purchase_date,
      purchase_amount: amount === '' ? 0 : Number(amount),
      warranty_until: f.warranty_until.trim() || null,
      status: f.status,
      disposal_type: isDisposed ? f.disposal_type : null,
      disposal_date: isDisposed ? f.disposal_date || null : null,
      disposal_amount:
        isDisposed && f.disposal_type === 'sold' && income !== '' ? Number(income) : null,
      category_id: f.category_id || null,
      category_name: f.category_name || null,
      account_id: f.account_id || null,
      account_name: f.account_name || null,
      note: f.note.trim() || null,
    }
  }, [])

  const onSubmit = useCallback(async () => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    if (!form.name.trim()) {
      toast.error(t('items.error.nameRequired'), t('notice.error'))
      return
    }
    if (!form.purchase_date.trim()) {
      toast.error(t('items.error.purchaseDateRequired'), t('notice.error'))
      return
    }
    const amount = Number(form.purchase_amount || 0)
    if (!Number.isFinite(amount) || amount < 0) {
      toast.error(t('items.error.amountInvalid'), t('notice.error'))
      return
    }
    setSubmitting(true)
    try {
      const payload = buildPayload(form)
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateItem(token, activeLedgerId, form.editingId!, base, payload as ItemUpdatePayload),
        )
        notifySuccess(t('items.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createItem(token, activeLedgerId, base, payload),
        )
        notifySuccess(t('items.notice.created'))
      }
      setDialogOpen(false)
      setForm(emptyForm())
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    } finally {
      setSubmitting(false)
    }
  }, [
    activeLedgerId,
    form,
    buildPayload,
    retryOnConflict,
    token,
    notifySuccess,
    notifyError,
    refresh,
    isWriteConflict,
    t,
    toast,
  ])

  const onDelete = useCallback(async () => {
    if (!activeLedgerId || !pendingDelete) return
    setDeleting(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteItem(token, activeLedgerId, pendingDelete.id, base),
      )
      notifySuccess(t('items.notice.deleted'))
      setPendingDelete(null)
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    } finally {
      setDeleting(false)
    }
  }, [
    activeLedgerId,
    pendingDelete,
    retryOnConflict,
    token,
    notifySuccess,
    notifyError,
    refresh,
    isWriteConflict,
    t,
  ])

  const renderRow = (item: ReadItem) => {
    const cost = itemCostParts(item)
    const warranty = warrantyStatusOf(item)
    return (
      <div
        key={item.id}
        className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border/60 px-3 py-2 text-sm"
      >
        <button
          type="button"
          onClick={() => void openDetail(item)}
          className="min-w-0 flex-1 truncate text-left font-medium hover:underline"
        >
          {item.name}
        </button>
        <span className="text-xs text-muted-foreground">
          {t('items.field.purchaseDate')} {dateOnly(item.purchase_date)}
        </span>
        <span className="text-xs text-muted-foreground">
          {t('items.heldDays').replace('{days}', String(cost.heldDays))}
        </span>
        <span className="text-xs font-medium text-primary">
          {t('items.dailyCost')} {money(cost.daily, currency)}
        </span>
        {warranty === 'expired' ? (
          <Badge variant="secondary">{t('items.warranty.expired')}</Badge>
        ) : warranty === 'active' ? (
          <Badge variant="outline">{t('items.warranty.active')}</Badge>
        ) : null}
        {item.is_disposed ? (
          <Badge variant="outline">
            {item.disposal_type === 'sold'
              ? t('items.disposal.sold')
              : t('items.disposal.scrapped')}
          </Badge>
        ) : null}
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => openEdit(item)}
            aria-label={t('items.button.update')}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => setPendingDelete(item)}
            aria-label={t('items.button.delete')}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">{t('nav.itemReturns')}</h2>
          <p className="text-xs text-muted-foreground">{t('items.desc')}</p>
        </div>
        <Button size="sm" onClick={openCreate} disabled={!activeLedgerId}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          {t('items.button.create')}
        </Button>
      </div>

      {!activeLedgerId ? (
        <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
          {t('items.empty')}
        </p>
      ) : (
        <>
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">
                {t('items.group.inUse')} ({groups.inUse.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {groups.inUse.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t('items.group.inUseEmpty')}</p>
              ) : (
                groups.inUse.map(renderRow)
              )}
            </CardContent>
          </Card>

          {groups.disposed.length > 0 ? (
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">
                  {t('items.group.disposed')} ({groups.disposed.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">{groups.disposed.map(renderRow)}</CardContent>
            </Card>
          ) : null}
        </>
      )}

      {/* 新建 / 编辑 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('items.button.update') : t('items.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="item-name">{t('items.field.name')}</Label>
              <Input
                id="item-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('items.field.namePlaceholder')}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="item-purchase-date">{t('items.field.purchaseDate')}</Label>
                <Input
                  id="item-purchase-date"
                  type="date"
                  value={form.purchase_date}
                  onChange={(e) => setForm({ ...form, purchase_date: e.target.value })}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="item-purchase-amount">{t('items.field.purchaseAmount')}</Label>
                <Input
                  id="item-purchase-amount"
                  type="number"
                  step="0.01"
                  value={form.purchase_amount}
                  onChange={(e) => setForm({ ...form, purchase_amount: e.target.value })}
                />
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="item-warranty">{t('items.field.warrantyUntil')}</Label>
              <Input
                id="item-warranty"
                type="date"
                value={form.warranty_until}
                onChange={(e) => setForm({ ...form, warranty_until: e.target.value })}
              />
              <p className="text-[11px] text-muted-foreground">{t('items.field.warrantyHint')}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label>{t('items.field.category')}</Label>
                <Select
                  value={form.category_id || '__none__'}
                  onValueChange={(v) => {
                    if (v === '__none__') {
                      setForm({ ...form, category_id: '', category_name: '' })
                      return
                    }
                    const cat = categories.find((c) => c.id === v)
                    setForm({
                      ...form,
                      category_id: v,
                      category_name: cat?.name || '',
                    })
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('items.field.categoryPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('items.field.none')}</SelectItem>
                    {categories.map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label>{t('items.field.account')}</Label>
                <Select
                  value={form.account_id || '__none__'}
                  onValueChange={(v) => {
                    if (v === '__none__') {
                      setForm({ ...form, account_id: '', account_name: '' })
                      return
                    }
                    const acc = accountOptions.find((a) => a.id === v)
                    setForm({ ...form, account_id: v, account_name: acc?.name || '' })
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('items.field.accountPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('items.field.none')}</SelectItem>
                    {accountOptions.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-1.5 rounded-md border border-border/60 p-3">
              <Label>{t('items.field.status')}</Label>
              <Select
                value={form.status}
                onValueChange={(v) =>
                  setForm({ ...form, status: v === 'disposed' ? 'disposed' : 'in_use' })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="in_use">{t('items.status.inUse')}</SelectItem>
                  <SelectItem value="disposed">{t('items.status.disposed')}</SelectItem>
                </SelectContent>
              </Select>
              {form.status === 'disposed' ? (
                <div className="mt-2 grid gap-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="grid gap-1.5">
                      <Label>{t('items.field.disposalType')}</Label>
                      <Select
                        value={form.disposal_type}
                        onValueChange={(v) =>
                          setForm({
                            ...form,
                            disposal_type: v === 'scrapped' ? 'scrapped' : 'sold',
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="sold">{t('items.disposal.sold')}</SelectItem>
                          <SelectItem value="scrapped">{t('items.disposal.scrapped')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-1.5">
                      <Label>{t('items.field.disposalDate')}</Label>
                      <Input
                        type="date"
                        value={form.disposal_date}
                        onChange={(e) => setForm({ ...form, disposal_date: e.target.value })}
                      />
                    </div>
                  </div>
                  {form.disposal_type === 'sold' ? (
                    <div className="grid gap-1.5">
                      <Label>{t('items.field.disposalAmount')}</Label>
                      <Input
                        type="number"
                        step="0.01"
                        value={form.disposal_amount}
                        onChange={(e) => setForm({ ...form, disposal_amount: e.target.value })}
                        placeholder="0.00"
                      />
                      <p className="text-[11px] text-muted-foreground">
                        {t('items.field.disposalAmountHint')}
                      </p>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="item-note">{t('items.field.note')}</Label>
              <Textarea
                id="item-note"
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                rows={2}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t('items.button.cancel')}
            </Button>
            <Button onClick={() => void onSubmit()} disabled={submitting}>
              {submitting ? t('items.button.saving') : t('items.button.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 详情:成本拆分 + 关联交易 */}
      <Dialog open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{detail?.item.name}</DialogTitle>
          </DialogHeader>
          {detail ? (
            <div className="space-y-3 text-sm">
              {(() => {
                const cost = itemCostParts(detail.item)
                const rows: Array<[string, string]> = [
                  [t('items.detail.purchase'), money(cost.purchase, currency)],
                  [t('items.detail.purchaseDate'), dateOnly(detail.item.purchase_date)],
                  [t('items.detail.heldDays'), `${cost.heldDays} ${t('items.detail.days')}`],
                ]
                if (cost.linked > 0) {
                  rows.push([t('items.detail.linked'), money(cost.linked, currency)])
                }
                if (cost.linkedIncome > 0) {
                  rows.push([
                    t('items.detail.linkedIncome'),
                    `-${money(cost.linkedIncome, currency)}`,
                  ])
                }
                if (detail.item.is_disposed && cost.disposalIncome > 0) {
                  rows.push([
                    t('items.detail.disposalIncome'),
                    `-${money(cost.disposalIncome, currency)}`,
                  ])
                }
                rows.push([t('items.detail.netCost'), money(cost.net, currency)])
                rows.push([t('items.detail.dailyCost'), money(cost.daily, currency)])
                if (detail.item.warranty_until) {
                  rows.push([
                    t('items.field.warrantyUntil'),
                    `${dateOnly(detail.item.warranty_until)}（${
                      warrantyStatusOf(detail.item) === 'expired'
                        ? t('items.warranty.expired')
                        : t('items.warranty.active')
                    }）`,
                  ])
                }
                if (detail.item.note) {
                  rows.push([t('items.field.note'), detail.item.note])
                }
                return rows.map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium tabular-nums">{value}</span>
                  </div>
                ))
              })()}

              <div className="border-t border-border pt-3">
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  {t('items.detail.linkedTxs')} ({detail.txs.length})
                </p>
                {loadingDetail ? (
                  <p className="text-xs text-muted-foreground">{t('items.detail.loading')}</p>
                ) : detail.txs.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{t('items.detail.noLinkedTxs')}</p>
                ) : (
                  <div className="space-y-1">
                    {detail.txs.map((tx) => (
                      <div
                        key={tx.id}
                        className="flex items-center justify-between gap-2 rounded border border-border/50 px-2 py-1.5 text-xs"
                      >
                        <span className="truncate">
                          {dateOnly(tx.happened_at)} · {tx.category_name || tx.note || tx.tx_type}
                        </span>
                        <span className="tabular-nums">
                          {money(Number(tx.native_amount ?? tx.amount), currency)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetail(null)}>
              {t('items.button.close')}
          </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={pendingDelete !== null} onOpenChange={(open) => !open && setPendingDelete(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('items.confirm.deleteTitle')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('items.confirm.deleteDesc').replace('{name}', pendingDelete?.name || '')}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              {t('items.button.cancel')}
            </Button>
            <Button variant="destructive" onClick={() => void onDelete()} disabled={deleting}>
              {t('items.button.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
