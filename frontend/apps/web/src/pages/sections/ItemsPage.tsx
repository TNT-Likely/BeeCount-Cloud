import { useSearchParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Pencil, Plus, Trash2, Package, Search, Check, Square } from 'lucide-react'

import {
  type AttachmentRef,
  type Item,
  type ItemCostMode,
  type ItemFlow,
  type ItemFlowType,
  type ItemLinkMode,
  type WorkspaceTransaction,
  createItem,
  createItemFlow,
  deleteItem,
  deleteItemFlow,
  fetchItems,
  fetchItemFlows,
  fetchReadAccounts,
  fetchWorkspaceTransactions,
  updateItem,
  updateItemFlow,
  uploadAttachment,
} from '@beecount/api-client'
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
  Textarea,
  useT,
} from '@beecount/ui'
import {
  Amount,
  LinkedTxList,
  formatIsoDateTimeLocal,
  fromLocalDateTimeInput,
  toLocalDateTimeInput,
  SectionPageHeader,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { localizeError } from '../../i18n/errors'

const COST_MODES: ItemCostMode[] = ['none', 'daily_avg', 'per_day']
const LINK_MODES: ItemLinkMode[] = ['none', 'new', 'existing']
const INCOME_CATEGORIES = ['出租', '顺风车带客', '其他收入']
const EXPENSE_CATEGORIES = ['保养', '维修', '税', '装修', '其他支出']

const emptyTxRef = '__none__'

function costLabel(mode: ItemCostMode, value: number | null, t: (k: string) => string): string {
  if (mode === 'none' || value === null) return '—'
  return `¥${value.toFixed(2)} / ${t(`items.costMode.${mode}`)}`
}

/** 已有交易选择器(0040):支持**多选** —— 一件物品的购入可能由多笔账单组成
 *  (定金 / 尾款 / 多平台)。点行切换勾选,底部「确定」提交当前选择。 */
function ExistingTxPicker({
  open,
  onOpenChange,
  token,
  ledgerId,
  selected,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  token: string
  ledgerId: string
  /** 已选中的交易(按选择顺序) */
  selected: WorkspaceTransaction[]
  onConfirm: (txs: WorkspaceTransaction[]) => void
}) {
  const t = useT()
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<WorkspaceTransaction[]>([])
  const [loading, setLoading] = useState(false)
  const [picked, setPicked] = useState<Map<string, WorkspaceTransaction>>(new Map())

  const search = useCallback(async () => {
    if (!token || !ledgerId) return
    setLoading(true)
    try {
      const page = await fetchWorkspaceTransactions(token, {
        ledgerId,
        q: query.trim() || undefined,
        limit: 30,
      })
      setRows(page.items || [])
    } catch {
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [token, ledgerId, query])

  useEffect(() => {
    if (open) {
      // 打开时带入外部已选(编辑场景)。
      setPicked(new Map(selected.map((tx) => [tx.id, tx])))
      void search()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const toggle = (tx: WorkspaceTransaction) => {
    setPicked((prev) => {
      const next = new Map(prev)
      if (next.has(tx.id)) next.delete(tx.id)
      else next.set(tx.id, tx)
      return next
    })
  }

  const pickedList = Array.from(picked.values())
  const pickedTotal = pickedList.reduce((s, tx) => s + Number(tx.amount || 0), 0)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('items.pickTx.title')}</DialogTitle>
        </DialogHeader>
        <div className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }}
            placeholder={t('items.pickTx.placeholder')}
          />
          <Button variant="outline" onClick={search} disabled={loading}>
            <Search className="h-4 w-4" />
          </Button>
        </div>
        {pickedList.length > 0 ? (
          <div className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
            {t('items.pickTx.pickedCount', { count: String(pickedList.length) })}
            <span className="ml-2 font-medium text-foreground">
              {t('items.pickTx.pickedTotal')} ¥{pickedTotal.toFixed(2)}
            </span>
          </div>
        ) : null}
        <div className="divide-y rounded-lg border">
          {rows.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {loading ? t('common.loading') : t('items.pickTx.empty')}
            </p>
          ) : rows.map((tx) => {
            const on = picked.has(tx.id)
            return (
              <button
                key={tx.id}
                type="button"
                className={`flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm hover:bg-muted/40 ${on ? 'bg-primary/10' : ''}`}
                onClick={() => toggle(tx)}
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">{tx.note || tx.category_name || t('items.pickTx.untitled')}</span>
                  <span className="text-xs text-muted-foreground">{formatIsoDateTimeLocal(tx.happened_at)}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="font-mono">¥{Number(tx.amount || 0).toFixed(2)}</span>
                  {on ? (
                    <Check className="h-4 w-4 text-primary" />
                  ) : (
                    <Square className="h-4 w-4 text-muted-foreground/50" />
                  )}
                </span>
              </button>
            )
          })}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button onClick={() => { onConfirm(pickedList); onOpenChange(false) }} disabled={pickedList.length === 0}>
            {t('common.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 通用附件选择器 */
function AttachmentPicker({ files, onChange }: { files: File[]; onChange: (f: File[]) => void }) {
  const t = useT()
  return (
    <div className="space-y-2">
      <Input
        type="file"
        multiple
        onChange={(e) => onChange(Array.from(e.target.files || []))}
      />
      {files.length > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {files.map((f, i) => (
            <li key={i} className="flex items-center justify-between">
              <span className="truncate">{f.name}</span>
              <button
                type="button"
                className="text-destructive"
                onClick={() => onChange(files.filter((_, idx) => idx !== i))}
              >
                {t('common.delete')}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ItemsPage() {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId, ledgers, setActiveLedgerId, refreshLedgers } = useLedgers()

  const validLedgerId =
    ledgers.find((l) => l.ledger_id === activeLedgerId)?.ledger_id ||
    ledgers[0]?.ledger_id ||
    activeLedgerId ||
    ''

  const [items, setItems] = useState<Item[]>([])
  const [accounts, setAccounts] = useState<{ id: string; name: string }[]>([])
  const [categoryFilter, setCategoryFilter] = useState('__all__')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 物品表单
  const [itemDialog, setItemDialog] = useState(false)
  const [editingItemId, setEditingItemId] = useState<string | null>(null)
  const [iName, setIName] = useState('')
  const [iPrice, setIPrice] = useState('')
  const [iAt, setIAt] = useState('')
  const [iChannel, setIChannel] = useState('')
  const [iCategory, setICategory] = useState('')
  const [iLocation, setILocation] = useState('')
  const [iAverageMode, setIAverageMode] = useState<ItemCostMode>('none')
  const [iTargetMode, setITargetMode] = useState<ItemCostMode>('none')
  const [iLinkMode, setILinkMode] = useState<ItemLinkMode>('none')
  const [iAccountId, setIAccountId] = useState('')
  // 0040:可关联多笔交易流水(定金/尾款/多平台)
  const [iTxs, setITxs] = useState<WorkspaceTransaction[]>([])
  // 价格是否被用户手动改过:没改过时随所选流水金额自动汇总。
  const [iPriceTouched, setIPriceTouched] = useState(false)
  const [iNote, setINote] = useState('')
  const [iFiles, setIFiles] = useState<File[]>([])
  const [txPicker, setTxPicker] = useState(false)
  const [busy, setBusy] = useState(false)

  // 详情 / 流水
  const [detail, setDetail] = useState<Item | null>(null)
  // 关联记录跳转(0043):?item_id=xxx → 自动打开该物品详情。
  const [searchParams] = useSearchParams()
  const presetItemId = searchParams.get('item_id')
  const [flows, setFlows] = useState<ItemFlow[]>([])
  const [flowDialog, setFlowDialog] = useState(false)
  const [editingFlowId, setEditingFlowId] = useState<string | null>(null)
  const [fType, setFType] = useState<ItemFlowType>('expense')
  const [fAmount, setFAmount] = useState('')
  const [fAt, setFAt] = useState('')
  const [fCategory, setFCategory] = useState('')
  const [fLinkMode, setFLinkMode] = useState<ItemLinkMode>('none')
  const [fAccountId, setFAccountId] = useState('')
  // 0040:物品流水同样可关联多笔交易
  const [fTxs, setFTxs] = useState<WorkspaceTransaction[]>([])
  const [fNote, setFNote] = useState('')
  const [fFiles, setFFiles] = useState<File[]>([])
  const [fTxPicker, setFTxPicker] = useState(false)
  const [deleteItemId, setDeleteItemId] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!token || !validLedgerId) return
    if (ledgers.length > 0 && activeLedgerId && !ledgers.some((l) => l.ledger_id === activeLedgerId)) {
      try {
        Object.keys(localStorage)
          .filter((k) => k.startsWith('beecount.active-ledger.'))
          .forEach((k) => localStorage.removeItem(k))
      } catch { /* ignore */ }
      setActiveLedgerId(ledgers[0].ledger_id)
      return
    }
    setLoading(true)
    try {
      setItems(await fetchItems(token, validLedgerId))
      setError(null)
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setLoading(false)
    }
  }, [token, validLedgerId, ledgers, activeLedgerId, setActiveLedgerId, t])

  useEffect(() => {
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith('beecount.active-ledger.'))
        .forEach((k) => localStorage.removeItem(k))
    } catch { /* ignore */ }
    if (ledgers.length === 0) {
      refreshLedgers().then(() => load()).catch(() => {})
    } else {
      load()
    }
  }, [load, ledgers.length, refreshLedgers])

  useEffect(() => {
    if (!token || !validLedgerId) return
    let cancelled = false
    fetchReadAccounts(token, validLedgerId)
      .then((rows) => { if (!cancelled) setAccounts(rows.map((a) => ({ id: a.id, name: a.name }))) })
      .catch(() => { if (!cancelled) setAccounts([]) })
    return () => { cancelled = true }
  }, [token, validLedgerId])

  const categories = useMemo(() => {
    const set = new Set<string>()
    items.forEach((i) => { if (i.category_name) set.add(i.category_name) })
    return Array.from(set).sort()
  }, [items])

  // 0044:所选关联流水的金额合计 = 物品成本(实时显示,保存时后端也会算一遍)。
  const txTotal = useMemo(
    () => iTxs.reduce((s, tx) => s + Number(tx.amount || 0), 0),
    [iTxs],
  )
  // 物品流水侧的合计(支出/收入金额自动取所选流水之和)。
  const fTxTotal = useMemo(
    () => fTxs.reduce((s, tx) => s + Number(tx.amount || 0), 0),
    [fTxs],
  )

  const visibleItems = useMemo(
    () => categoryFilter === '__all__' ? items : items.filter((i) => i.category_name === categoryFilter),
    [items, categoryFilter],
  )

  const summary = useMemo(() => ({
    count: items.length,
    value: items.reduce((s, i) => s + (Number(i.price) || 0), 0),
    net: items.reduce((s, i) => s + (Number(i.net_cost) || 0), 0),
    income: items.reduce((s, i) => s + (Number(i.total_income) || 0), 0),
    expense: items.reduce((s, i) => s + (Number(i.total_expense) || 0), 0),
  }), [items])

  const uploadFiles = async (files: File[]): Promise<AttachmentRef[]> => {
    const out: AttachmentRef[] = []
    for (let i = 0; i < files.length; i++) {
      const r = await uploadAttachment(token, { ledger_id: validLedgerId, file: files[i] })
      out.push({
        fileName: r.file_name,
        originalName: r.file_name,
        fileSize: r.size,
        sortOrder: i,
        cloudFileId: r.file_id,
        cloudSha256: r.sha256,
      })
    }
    return out
  }

  const openItemDialog = (item?: Item) => {
    setEditingItemId(item?.id ?? null)
    setIName(item?.name ?? '')
    setIPrice(item ? String(item.price) : '')
    setIAt(toLocalDateTimeInput(item?.purchased_at) || toLocalDateTimeInput(new Date().toISOString()))
    setIChannel(item?.purchase_channel ?? '')
    setICategory(item?.category_name ?? '')
    setILocation(item?.location ?? '')
    setIAverageMode(item?.average_mode ?? 'none')
    setITargetMode(item?.target_cost_mode ?? 'none')
    setILinkMode(item?.link_mode ?? 'none')
    setIAccountId(item?.account_id ?? '')
    // 0044/0045:编辑时回填已关联的流水(带金额与备注),让用户能直接增删。
    // 金额必须带上,否则「关联流水合计」算成 0,编辑时价格会被清空。
    const details = item?.tx_sync_details ?? []
    const amounts = item?.tx_sync_amounts ?? {}
    setITxs(
      (item?.tx_sync_ids ?? []).map((id) => {
        const d = details.find((x) => x.sync_id === id)
        return {
          id,
          amount: Number(amounts[id] ?? d?.amount ?? 0),
          note: d?.note ?? '',
          category_name: d?.category_name ?? '',
        } as WorkspaceTransaction
      }),
    )
    setIPriceTouched(false)
    setINote(item?.note ?? '')
    setIFiles([])
    setItemDialog(true)
  }

  const handleSaveItem = async () => {
    // 价格允许为 0(0044):只校验名字,价格留空按 0 处理。
    if (!token || !validLedgerId || !iName.trim()) return
    setBusy(true)
    try {
      const attachments = await uploadFiles(iFiles)
      const price = iPrice.trim() === '' ? 0 : Number(iPrice)
      if (!Number.isFinite(price) || price < 0) {
        setError(t('items.field.priceInvalid'))
        return
      }
      const body = {
        ledger_id: validLedgerId,
        name: iName.trim(),
        price,
        purchased_at: fromLocalDateTimeInput(iAt),
        purchase_channel: iChannel.trim() || null,
        category_name: iCategory.trim() || null,
        location: iLocation.trim() || null,
        average_mode: iAverageMode,
        target_cost_mode: iTargetMode,
        note: iNote.trim() || null,
        attachments,
        link_mode: iLinkMode,
        ...(iLinkMode === 'new' ? { account_id: iAccountId || null, account_name: accounts.find((a) => a.id === iAccountId)?.name || null } : {}),
        ...(iLinkMode === 'existing' ? { tx_sync_ids: iTxs.map((x) => x.id) } : {}),
      }
      if (editingItemId) {
        // 0044:编辑时也能改关联流水 + 让后端重算成本汇总。
        await updateItem(token, editingItemId, {
          name: body.name, price: body.price, purchased_at: body.purchased_at,
          purchase_channel: body.purchase_channel, category_name: body.category_name,
          location: body.location, average_mode: body.average_mode,
          target_cost_mode: body.target_cost_mode, note: body.note, attachments: body.attachments,
          link_mode: iLinkMode,
          auto_price: !iPriceTouched,
          ...(iLinkMode === 'existing' ? { tx_sync_ids: iTxs.map((x) => x.id) } : {}),
          ...(iLinkMode === 'new' ? { account_id: iAccountId || null, account_name: accounts.find((a) => a.id === iAccountId)?.name || null } : {}),
        })
      } else {
        await createItem(token, { ...body, auto_price: !iPriceTouched })
      }
      setItemDialog(false)
      await load()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setBusy(false)
    }
  }

  const openDetail = async (item: Item) => {
    setDetail(item)
    try {
      setFlows(await fetchItemFlows(token, item.id))
    } catch {
      setFlows([])
    }
  }

  // 关联记录跳转(0043):拉起指定物品的详情。
  const openedItemRef = useRef<string | null>(null)
  useEffect(() => {
    if (!token || !presetItemId || !validLedgerId) return
    if (openedItemRef.current === presetItemId) return
    openedItemRef.current = presetItemId
    void fetchItems(token, validLedgerId)
      .then((rows) => {
        const hit = rows.find((i) => i.id === presetItemId)
        if (hit) return openDetail(hit)
        return undefined
      })
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, presetItemId, validLedgerId])

  const refreshDetail = async () => {
    if (!detail) return
    setFlows(await fetchItemFlows(token, detail.id))
    await load()
    const fresh = (await fetchItems(token, validLedgerId)).find((i) => i.id === detail.id)
    if (fresh) setDetail(fresh)
  }

  const openFlowDialog = (flow?: ItemFlow) => {
    setEditingFlowId(flow?.id ?? null)
    setFType(flow?.flow_type ?? 'expense')
    setFAmount(flow ? String(flow.amount) : '')
    setFAt(toLocalDateTimeInput(flow?.happened_at) || toLocalDateTimeInput(new Date().toISOString()))
    setFCategory(flow?.category_name ?? '')
    setFLinkMode(flow?.link_mode ?? 'none')
    setFAccountId(flow?.account_id ?? '')
    // 0044/0045:回填已关联的流水(带金额与备注),让用户能直接增删。
    const flowDetails = flow?.tx_sync_details ?? []
    setFTxs(
      (flow?.tx_sync_ids ?? []).map((id) => {
        const d = flowDetails.find((x) => x.sync_id === id)
        return {
          id,
          amount: Number(flow?.tx_sync_amounts?.[id] ?? d?.amount ?? 0),
          note: d?.note ?? '',
          category_name: d?.category_name ?? '',
        } as WorkspaceTransaction
      }),
    )
    setFNote(flow?.note ?? '')
    setFFiles([])
    setFlowDialog(true)
  }

  const handleSaveFlow = async () => {
    if (!detail || !token) return
    // 0044:关联已有流水时金额自动汇总;否则必须手填。
    const autoAmount = fLinkMode === 'existing' && fTxs.length > 0
    const amount = autoAmount ? fTxTotal : Number(fAmount)
    if (!Number.isFinite(amount) || amount <= 0) return
    setBusy(true)
    try {
      const attachments = await uploadFiles(fFiles)
      if (editingFlowId) {
        await updateItemFlow(token, editingFlowId, {
          flow_type: fType,
          amount,
          happened_at: fromLocalDateTimeInput(fAt),
          category_name: fCategory.trim() || null,
          note: fNote.trim() || null,
          attachments,
          link_mode: fLinkMode,
          auto_amount: autoAmount,
          ...(fLinkMode === 'existing' ? { tx_sync_ids: fTxs.map((x) => x.id) } : {}),
        })
      } else {
        await createItemFlow(token, detail.id, {
          item_id: detail.id,
          flow_type: fType,
          amount,
          happened_at: fromLocalDateTimeInput(fAt),
          category_name: fCategory.trim() || null,
          note: fNote.trim() || null,
          attachments,
          link_mode: fLinkMode,
          auto_amount: autoAmount,
          ...(fLinkMode === 'new' ? { account_id: fAccountId || null, account_name: accounts.find((a) => a.id === fAccountId)?.name || null } : {}),
          ...(fLinkMode === 'existing' ? { tx_sync_ids: fTxs.map((x) => x.id) } : {}),
        })
      }
      setFlowDialog(false)
      await refreshDetail()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setBusy(false)
    }
  }

  const handleDeleteFlow = async (flow: ItemFlow) => {
    if (!token) return
    try {
      await deleteItemFlow(token, flow.id)
      await refreshDetail()
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleDeleteItem = async () => {
    if (!token || !deleteItemId) return
    try {
      await deleteItem(token, deleteItemId)
      setDeleteItemId(null)
      if (detail?.id === deleteItemId) setDetail(null)
      await load()
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-6">
      <SectionPageHeader
        section="items"
        title={t('items.title')}
        subtitle={t('items.subtitle')}
        actions={
          <Button onClick={() => openItemDialog()}>
            <Plus className="mr-1 h-4 w-4" />
            {t('items.button.add')}
          </Button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border p-4">
          <p className="text-xs text-muted-foreground">{t('items.summary.count')}</p>
          <p className="mt-1 text-xl font-semibold">{summary.count}</p>
        </div>
        <div className="rounded-lg border p-4">
          <p className="text-xs text-muted-foreground">{t('items.summary.value')}</p>
          <p className="mt-1 text-xl font-semibold font-mono">¥{summary.value.toFixed(2)}</p>
        </div>
        <div className="rounded-lg border p-4">
          <p className="text-xs text-muted-foreground">{t('items.summary.income')}</p>
          <p className="mt-1 text-xl font-semibold font-mono text-emerald-600">¥{summary.income.toFixed(2)}</p>
        </div>
        <div className="rounded-lg border p-4">
          <p className="text-xs text-muted-foreground">{t('items.summary.expense')}</p>
          <p className="mt-1 text-xl font-semibold font-mono text-destructive">¥{summary.expense.toFixed(2)}</p>
        </div>
      </div>

      {error && <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div>}

      <div className="flex flex-wrap items-center gap-2">
        <Label className="text-xs text-muted-foreground">{t('items.filter.category')}</Label>
        <Select value={categoryFilter} onValueChange={setCategoryFilter}>
          <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">{t('items.filter.all')}</SelectItem>
            {categories.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {loading && <div className="py-8 text-center text-sm text-muted-foreground">{t('common.loading')}</div>}

      {!loading && visibleItems.length === 0 ? (
        <div className="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">
          {t('items.empty')}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {visibleItems.map((item) => (
            <div key={item.id} className="rounded-lg border p-4 transition-colors hover:bg-muted/30">
              <div className="flex items-start justify-between gap-2">
                <button type="button" className="flex min-w-0 items-center gap-2 text-left" onClick={() => openDetail(item)}>
                  <Package className="h-5 w-5 shrink-0 text-primary" />
                  <span className="truncate font-semibold">{item.name}</span>
                </button>
                <div className="flex shrink-0 gap-1">
                  <Button variant="ghost" size="sm" onClick={() => openItemDialog(item)} aria-label={t('common.edit')}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setDeleteItemId(item.id)} aria-label={t('common.delete')}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-y-1 text-xs text-muted-foreground">
                <span>{t('items.field.price')}: <span className="font-mono text-foreground">¥{item.price.toFixed(2)}</span></span>
                <span>{t('items.field.purchasedAt')}: <span className="text-foreground">{formatIsoDateTimeLocal(item.purchased_at)}</span></span>
                <span>{t('items.field.category')}: <span className="text-foreground">{item.category_name || '—'}</span></span>
                <span>{t('items.field.location')}: <span className="text-foreground">{item.location || '—'}</span></span>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3 text-xs">
                <span className="rounded bg-muted px-2 py-0.5">
                  {t('items.summary.netCost')}: <span className="font-mono">¥{item.net_cost.toFixed(2)}</span>
                </span>
                <span className="rounded bg-muted px-2 py-0.5">{costLabel(item.average_mode, item.average_cost, t)}</span>
                <span className="rounded bg-muted px-2 py-0.5">{costLabel(item.target_cost_mode, item.target_cost, t)}</span>
                <button type="button" className="ml-auto text-primary hover:underline" onClick={() => openDetail(item)}>
                  {t('items.flow.count', { count: item.flow_count })}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 物品新建/编辑 */}
      <Dialog open={itemDialog} onOpenChange={setItemDialog}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingItemId ? t('items.dialog.edit') : t('items.dialog.add')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label>{t('items.field.name')} *</Label>
              <Input value={iName} onChange={(e) => setIName(e.target.value)} />
            </div>
            <div>
              <Label>{t('items.field.price')}</Label>
              <Input type="number" step="0.01" min="0" value={iPrice} placeholder="0.00"
                onChange={(e) => { setIPrice(e.target.value); setIPriceTouched(true) }} />
              {iLinkMode === 'existing' && iTxs.length > 0 && !iPriceTouched ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {t('items.field.priceAutoHint', { total: txTotal.toFixed(2) })}
                </p>
              ) : null}
            </div>
            <div>
              <Label>{t('items.field.purchasedAt')}</Label>
              <Input type="datetime-local" step={1} value={iAt} onChange={(e) => setIAt(e.target.value)} />
            </div>
            <div>
              <Label>{t('items.field.channel')}</Label>
              <Input value={iChannel} onChange={(e) => setIChannel(e.target.value)} placeholder={t('items.field.channelHint')} />
            </div>
            <div>
              <Label>{t('items.field.category')}</Label>
              <Input value={iCategory} onChange={(e) => setICategory(e.target.value)} />
              <p className="mt-1 text-xs text-muted-foreground">{t('items.field.categoryHint')}</p>
            </div>
            <div>
              <Label>{t('items.field.location')}</Label>
              <Input value={iLocation} onChange={(e) => setILocation(e.target.value)} />
            </div>
            <div>
              <Label>{t('items.field.averageMode')}</Label>
              <Select value={iAverageMode} onValueChange={(v) => setIAverageMode(v as ItemCostMode)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {COST_MODES.map((m) => <SelectItem key={m} value={m}>{t(`items.costMode.${m}`)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t('items.field.targetCostMode')}</Label>
              <Select value={iTargetMode} onValueChange={(v) => setITargetMode(v as ItemCostMode)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {COST_MODES.map((m) => <SelectItem key={m} value={m}>{t(`items.costMode.${m}`)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="md:col-span-2">
              <Label>{t('items.field.linkMode')}</Label>
              <Select value={iLinkMode} onValueChange={(v) => { setILinkMode(v as ItemLinkMode); setIPriceTouched(false) }}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {LINK_MODES.map((m) => <SelectItem key={m} value={m}>{t(`items.linkMode.${m}`)}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">{t('items.field.linkModeHint')}</p>
            </div>
            {iLinkMode === 'new' && (
              <div className="md:col-span-2">
                <Label>{t('items.field.account')}</Label>
                <Select value={iAccountId || emptyTxRef} onValueChange={(v) => setIAccountId(v === emptyTxRef ? '' : v)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={emptyTxRef}>{t('items.account.none')}</SelectItem>
                    {accounts.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-xs text-muted-foreground">{t('items.field.newTxHint')}</p>
              </div>
            )}
            {iLinkMode === 'existing' && (
              <div className="md:col-span-2">
                <Label>{t('items.field.existingTx')}</Label>
                <Button type="button" variant="outline" className="w-full justify-start" onClick={() => setTxPicker(true)}>
                  {iTxs.length > 0
                    ? t('items.pickTx.selectedCount', { count: String(iTxs.length) })
                    : t('items.pickTx.button')}
                </Button>
                {iTxs.length > 0 ? (
                  <>
                    <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                      {iTxs.map((tx) => (
                        <li key={tx.id} className="flex items-center justify-between gap-2">
                          <span className="truncate">
                            {tx.note || tx.category_name || t('items.pickTx.untitled')}
                            {' · '}¥{Number(tx.amount || 0).toFixed(2)}
                          </span>
                          <button
                            type="button"
                            className="shrink-0 text-destructive"
                            onClick={() => setITxs((prev) => prev.filter((x) => x.id !== tx.id))}
                          >
                            {t('common.delete')}
                          </button>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {t('items.field.priceAutoHint', { total: txTotal.toFixed(2) })}
                    </p>
                  </>
                ) : null}
              </div>
            )}
            <div className="md:col-span-2">
              <Label>{t('items.field.note')}</Label>
              <Textarea value={iNote} onChange={(e) => setINote(e.target.value)} rows={2} />
            </div>
            <div className="md:col-span-2">
              <Label>{t('items.field.attachments')}</Label>
              <AttachmentPicker files={iFiles} onChange={setIFiles} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setItemDialog(false)}>{t('common.cancel')}</Button>
            <Button onClick={handleSaveItem} disabled={busy || !iName.trim()}>
              {busy ? t('common.loading') : t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 物品详情 + 收益/支出流水 */}
      <Dialog open={!!detail} onOpenChange={(v) => { if (!v) setDetail(null) }}>
        <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{detail?.name}</DialogTitle>
          </DialogHeader>
          {detail && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted/30 p-4 text-sm md:grid-cols-4">
                <div><p className="text-xs text-muted-foreground">{t('items.field.price')}</p><p className="font-mono">¥{detail.price.toFixed(2)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.field.purchasedAt')}</p><p>{formatIsoDateTimeLocal(detail.purchased_at)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.summary.income')}</p><p className="font-mono text-emerald-600">¥{detail.total_income.toFixed(2)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.summary.expense')}</p><p className="font-mono text-destructive">¥{detail.total_expense.toFixed(2)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.summary.netCost')}</p><p className="font-mono">¥{detail.net_cost.toFixed(2)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.summary.daysHeld')}</p><p>{detail.days_held.toFixed(1)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.field.averageMode')}</p><p>{costLabel(detail.average_mode, detail.average_cost, t)}</p></div>
                <div><p className="text-xs text-muted-foreground">{t('items.field.targetCostMode')}</p><p>{costLabel(detail.target_cost_mode, detail.target_cost, t)}</p></div>
              </div>

              {(detail.purchase_channel || detail.location || detail.note) && (
                <div className="space-y-1 text-sm text-muted-foreground">
                  {detail.purchase_channel && <p>{t('items.field.channel')}: {detail.purchase_channel}</p>}
                  {detail.location && <p>{t('items.field.location')}: {detail.location}</p>}
                  {detail.note && <p>{t('items.field.note')}: {detail.note}</p>}
                </div>
              )}

              {/* 关联账单(0040/0045):可关联多笔交易流水,这里列出全部。
                  显示备注 / 金额 / 时间 / 分类等可读信息,不只是 syncId。 */}
              {detail.tx_sync_ids && detail.tx_sync_ids.length > 0 ? (
                <div className="rounded-lg border border-border p-3">
                  <p className="mb-2 text-sm font-medium">
                    {t('items.field.existingTx')}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      {t('items.pickTx.selectedCount', { count: String(detail.tx_sync_ids.length) })}
                    </span>
                  </p>
                  <LinkedTxList details={detail.tx_sync_details} />
                </div>
              ) : null}

              <div className="flex items-center justify-between">
                <h3 className="font-semibold">{t('items.flow.title')}</h3>
                <Button size="sm" onClick={() => openFlowDialog()}>
                  <Plus className="mr-1 h-4 w-4" />{t('items.flow.add')}
                </Button>
              </div>

              {/* 收益与支出流水(0046):行渲染与交易流水一致(主标题 / meta / 金额),
                  关联的交易流水在行下方展开显示,不再只留一串 syncId。 */}
              <div className="divide-y rounded-lg border">
                {flows.length === 0 ? (
                  <p className="px-4 py-8 text-center text-sm text-muted-foreground">{t('items.flow.empty')}</p>
                ) : flows.map((f) => (
                  <div key={f.id} className="px-4 py-2.5">
                    <div
                      className="grid gap-x-3"
                      style={{ gridTemplateColumns: '1fr auto', gridTemplateRows: 'auto 1fr' }}
                    >
                      {/* 左上:类型 + 分类 / 备注 */}
                      <div className="flex min-w-0 items-center gap-2 self-start">
                        <span className="shrink-0 text-[11px] font-medium leading-none text-muted-foreground">
                          [{t(`items.flowType.${f.flow_type}`)}]
                        </span>
                        <span className="truncate text-sm font-medium">
                          {f.category_name || '—'}
                        </span>
                        {f.note ? (
                          <span className="truncate text-xs text-muted-foreground">({f.note})</span>
                        ) : null}
                      </div>
                      {/* 右上:金额(收支语义色,同交易流水行) */}
                      <div className="flex shrink-0 items-center justify-end gap-2 self-start">
                        <Amount
                          value={Number(f.amount)}
                          compact={false}
                          bold
                          size="sm"
                          tone={f.flow_type === 'income' ? 'positive' : 'negative'}
                        />
                        <Button variant="ghost" size="sm" onClick={() => openFlowDialog(f)} aria-label={t('common.edit')}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => handleDeleteFlow(f)} aria-label={t('common.delete')}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                      {/* 左下:时间 */}
                      <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 self-end text-xs text-muted-foreground">
                        <span className="font-mono tabular-nums">
                          {formatIsoDateTimeLocal(f.happened_at)}
                        </span>
                        {f.account_name ? (
                          <span className="truncate">· {f.account_name}</span>
                        ) : null}
                      </div>
                    </div>
                    {/* 关联的交易流水(0046) */}
                    {f.tx_sync_details && f.tx_sync_details.length > 0 ? (
                      <div className="mt-2 pl-3">
                        <p className="mb-1 text-[11px] text-muted-foreground">
                          {t('linkedTx.title')}
                        </p>
                        <LinkedTxList details={f.tx_sync_details} />
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 流水新建/编辑 */}
      <Dialog open={flowDialog} onOpenChange={setFlowDialog}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingFlowId ? t('items.flow.edit') : t('items.flow.add')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label>{t('items.flow.field.type')}</Label>
              <Select value={fType} onValueChange={(v) => { setFType(v as ItemFlowType); setFCategory('') }}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="expense">{t('items.flowType.expense')}</SelectItem>
                  <SelectItem value="income">{t('items.flowType.income')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>{t('items.flow.field.amount')} *</Label>
              <Input type="number" step="0.01" value={fAmount} onChange={(e) => setFAmount(e.target.value)}
                disabled={fLinkMode === 'existing' && fTxs.length > 0} />
              {fLinkMode === 'existing' && fTxs.length > 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {t('items.field.priceAutoHint', { total: fTxTotal.toFixed(2) })}
                </p>
              ) : null}
            </div>
            <div>
              <Label>{t('items.flow.field.time')}</Label>
              <Input type="datetime-local" step={1} value={fAt} onChange={(e) => setFAt(e.target.value)} />
            </div>
            <div>
              <Label>{t('items.flow.field.category')}</Label>
              <Input
                value={fCategory}
                onChange={(e) => setFCategory(e.target.value)}
                placeholder={t('items.flow.categoryHint')}
              />
              <div className="mt-2 flex flex-wrap gap-1">
                {(fType === 'income' ? INCOME_CATEGORIES : EXPENSE_CATEGORIES).map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`rounded-full border px-2 py-0.5 text-xs ${
                      fCategory === c ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:border-primary'
                    }`}
                    onClick={() => setFCategory(c)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
            <div className="md:col-span-2">
              <Label>{t('items.field.linkMode')}</Label>
              <Select value={fLinkMode} onValueChange={(v) => setFLinkMode(v as ItemLinkMode)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {LINK_MODES.map((m) => <SelectItem key={m} value={m}>{t(`items.linkMode.${m}`)}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">{t('items.field.linkModeHint')}</p>
            </div>
            {fLinkMode === 'new' && (
              <div className="md:col-span-2">
                <Label>{t('items.field.account')}</Label>
                <Select value={fAccountId || emptyTxRef} onValueChange={(v) => setFAccountId(v === emptyTxRef ? '' : v)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={emptyTxRef}>{t('items.account.none')}</SelectItem>
                    {accounts.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            {fLinkMode === 'existing' && (
              <div className="md:col-span-2">
                <Label>{t('items.field.existingTx')}</Label>
                <Button type="button" variant="outline" className="w-full justify-start" onClick={() => setFTxPicker(true)}>
                  {fTxs.length > 0
                    ? t('items.pickTx.selectedCount', { count: String(fTxs.length) })
                    : t('items.pickTx.button')}
                </Button>
                {fTxs.length > 0 ? (
                  <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {fTxs.map((tx) => (
                      <li key={tx.id} className="flex items-center justify-between gap-2">
                        <span className="truncate">
                          {tx.note || tx.category_name || t('items.pickTx.untitled')}
                          {' · '}¥{Number(tx.amount || 0).toFixed(2)}
                        </span>
                        <button
                          type="button"
                          className="shrink-0 text-destructive"
                          onClick={() => setFTxs((prev) => prev.filter((x) => x.id !== tx.id))}
                        >
                          {t('common.delete')}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            )}
            <div className="md:col-span-2">
              <Label>{t('items.field.note')}</Label>
              <Textarea value={fNote} onChange={(e) => setFNote(e.target.value)} rows={2} />
            </div>
            <div className="md:col-span-2">
              <Label>{t('items.field.attachments')}</Label>
              <AttachmentPicker files={fFiles} onChange={setFFiles} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFlowDialog(false)}>{t('common.cancel')}</Button>
            <Button onClick={handleSaveFlow} disabled={busy || !(fLinkMode === 'existing' && fTxs.length > 0 ? fTxTotal > 0 : Number(fAmount) > 0)}>
              {busy ? t('common.loading') : t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 交易选择器（物品 / 流水共用，0040 支持多选） */}
      <ExistingTxPicker
        open={txPicker || fTxPicker}
        onOpenChange={(v) => { setTxPicker(v); setFTxPicker(v) }}
        token={token || ''}
        ledgerId={validLedgerId}
        selected={txPicker ? iTxs : fTxs}
        onConfirm={(txs) => {
          if (txPicker) {
            setITxs(txs)
            // 价格未手动改过时,自动汇总所选流水金额(定金+尾款=购入价)。
            if (!iPriceTouched) {
              const total = txs.reduce((s, x) => s + Number(x.amount || 0), 0)
              setIPrice(total > 0 ? String(Math.round(total * 100) / 100) : '')
            }
          } else {
            setFTxs(txs)
          }
        }}
      />

      {/* 删除确认 */}
      <Dialog open={!!deleteItemId} onOpenChange={(v) => { if (!v) setDeleteItemId(null) }}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('items.delete.title')}</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">{t('items.delete.desc')}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteItemId(null)}>{t('common.cancel')}</Button>
            <Button variant="destructive" onClick={handleDeleteItem}>{t('common.delete')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
