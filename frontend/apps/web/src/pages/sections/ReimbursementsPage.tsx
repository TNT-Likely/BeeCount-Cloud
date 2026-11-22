import { useSearchParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useState, useRef } from 'react'
import { Plus, ReceiptText, Trash2, Building2, Pencil } from 'lucide-react'

import {
  type AttachmentRef,
  type Company,
  type Reimbursement,
  type ReimbursementItem,
  createCompany,
  createReimbursement,
  deleteCompany,
  deleteReimbursement,
  deleteReimbursementItem,
  fetchCompanies,
  fetchReadAccounts,
  fetchReimbursementItems,
  fetchReimbursements,
  updateCompany,
  updateReimbursement,
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
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  useT,
} from '@beecount/ui'
import {
  CategoryIcon,
  formatBalanceCompact,
  formatIsoDateTimeLocal,
  fromLocalDateTimeInput,
  toLocalDateTimeInput,
  LinkedTxList,
  SectionPageHeader,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { localizeError } from '../../i18n/errors'
import {
  onOpenDetailReimbursement,
  openTxDetailBySyncId,
  type ReimbursementDetailTarget,
} from '../../lib/txDialogEvents'

/** 报销单可选图标（均为已加载的 Material Symbols 字形）。 */
const RB_ICONS = [
  'receipt_long', 'money', 'paid', 'wallet', 'credit_card',
  'account_balance_wallet', 'local_taxi', 'flight', 'train',
  'restaurant', 'shopping_bag', 'medical_services', 'school',
  'apartment', 'handshake', 'work',
]

/** 统计卡片。tone 区分支出(红)/收入(绿)/中性。 */
function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'rose' | 'emerald' | 'default'
}) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={`mt-1 text-base font-semibold ${
          tone === 'rose' ? 'text-rose-600' : tone === 'emerald' ? 'text-emerald-600' : ''
        }`}
      >
        {value}
      </div>
    </div>
  )
}

/** 附件列表（报销凭证）：只列文件名,点开由上层处理。 */
function AttachmentList({ files, label }: { files: AttachmentRef[] | null; label: string }) {
  const t = useT()
  if (!files || files.length === 0) return null
  return (
    <div>
      <div className="mb-1 text-sm font-medium">{label}</div>
      <ul className="space-y-1 rounded-lg border border-border p-2 text-xs text-muted-foreground">
        {files.map((f, i) => (
          <li key={f.cloudFileId || `${f.fileName}-${i}`} className="truncate">
            📎 {f.originalName || f.fileName}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 报销单详情弹窗（0056）。
 *
 * 版式与联系人详情弹窗完全对齐：头部固定 / 中间独立滚动 / 底部操作栏固定。
 *
 * 统计维度用**报销口径**（与联系人「人」的借还口径、成员「收支归属」口径都不同）：
 *   报销金额 / 报销项笔数 / 涉及公司 / 到账账户 —— 关心的是「这批账单报掉了多少」。
 *
 * 流水双向跳转：报销到账流水 + 每笔报销项的原支出流水都可点开原交易详情；
 * 反向由交易详情的「关联记录」跳回本弹窗。
 */
function ReimbursementDetailDialog({
  open,
  reimbursement,
  currency,
  ledgerId,
  onOpenChange,
  onDelete,
}: {
  open: boolean
  reimbursement: Reimbursement | null
  currency: string
  ledgerId: string | null
  onOpenChange: (v: boolean) => void
  onDelete: (rb: Reimbursement) => void
}) {
  const t = useT()
  const { token } = useAuth()
  if (!reimbursement) {
    return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent /></Dialog>
  }
  const rb = reimbursement
  const itemCount = rb.items?.length ?? rb.item_count ?? 0
  const itemTotal = (rb.items ?? []).reduce((s, it) => s + (Number(it.amount) || 0), 0)
  // 每笔报销项的原支出流水汇总(点行跳原交易详情)。
  const itemLinked = (rb.items ?? []).flatMap((it) => it.tx_sync_details ?? [])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex shrink-0 flex-row items-start justify-between gap-3 border-b border-border/60 bg-gradient-to-br from-muted/40 to-transparent px-6 py-4">
          <DialogTitle className="flex items-center gap-3 pr-8">
            <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary/10">
              <CategoryIcon icon={rb.icon} iconType="material" size={24} className="text-primary" />
            </span>
            <div className="flex min-w-0 flex-col">
              <span className="text-lg font-semibold">
                {formatBalanceCompact(rb.amount, currency)}
              </span>
              <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] font-normal">
                <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-emerald-600">
                  {t('reimbursement.tab.done')}
                </span>
                {rb.company_name ? (
                  <span className="rounded bg-primary/10 px-2 py-0.5 text-primary">
                    {t('reimbursement.field.company')}: {rb.company_name}
                  </span>
                ) : null}
                <span className="text-muted-foreground">
                  {formatIsoDateTimeLocal(rb.happened_at)}
                </span>
              </span>
            </div>
          </DialogTitle>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-6 py-4">
          {/* 统计概览:报销口径。 */}
          <div>
            <div className="mb-2 text-sm font-medium">{t('reimbursement.detail.summary')}</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat
                label={t('reimbursement.detail.amount')}
                value={formatBalanceCompact(rb.amount, currency)}
                tone="emerald"
              />
              <Stat label={t('reimbursement.detail.itemCount')} value={String(itemCount)} />
              <Stat
                label={t('reimbursement.detail.itemTotal')}
                value={formatBalanceCompact(itemTotal, currency)}
              />
              <Stat
                label={t('reimbursement.detail.receiveAccount')}
                value={rb.receive_account_name || t('reimbursement.accountNone')}
              />
            </div>
          </div>

          {rb.note ? (
            <div className="break-words whitespace-pre-wrap rounded-lg border border-border p-3 text-sm text-muted-foreground">
              {rb.note}
            </div>
          ) : null}

          <AttachmentList files={rb.attachments} label={t('reimbursement.field.attachments')} />

          {/* 报销到账流水:点行跳原交易详情。 */}
          {rb.tx_sync_id ? (
            <div className="min-w-0">
              <div className="mb-2 text-sm font-medium">{t('reimbursement.detail.receiveFlow')}</div>
              <LinkedTxList
                details={rb.tx_sync_details}
                searchable={false}
                onSelect={(tx) =>
                  openTxDetailBySyncId(token, tx.sync_id, ledgerId ?? undefined, () => onOpenChange(false))
                }
              />
            </div>
          ) : null}

          {/* 每笔报销项的原支出流水。 */}
          {itemLinked.length > 0 ? (
            <div className="min-w-0">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">{t('reimbursement.detail.itemFlows')}</span>
                <span className="text-xs text-muted-foreground">
                  {itemLinked.length} {t('loans.group.flowCount')}
                </span>
              </div>
              <LinkedTxList
                details={itemLinked}
                searchable={false}
                onSelect={(tx) =>
                  openTxDetailBySyncId(token, tx.sync_id, ledgerId ?? undefined, () => onOpenChange(false))
                }
              />
            </div>
          ) : null}
        </div>

        {/* 底部操作栏固定可见。 */}
        <div className="shrink-0 border-t border-border/60 px-6 py-3">
          <div className="flex flex-wrap gap-2">
            {rb.tx_sync_id ? (
              <Button
                variant="outline"
                onClick={() =>
                  openTxDetailBySyncId(token, rb.tx_sync_id as string, ledgerId ?? undefined, () =>
                    onOpenChange(false),
                  )
                }
              >
                {t('deposits.detail.viewFlow')}
              </Button>
            ) : null}
            <Button variant="ghost" onClick={() => onDelete(rb)}>
              {t('confirm.delete')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 待报销项详情弹窗（0056）。
 *
 * 统计维度用「待报销」口径:金额 / 状态 / 公司 / 原支出账户 —— 关心的是
 * 「这一笔要不要报、报给谁」。原支出流水可点开原交易详情。
 */
function ReimbursementItemDetailDialog({
  open,
  item,
  currency,
  ledgerId,
  onOpenChange,
  onCancelMark,
}: {
  open: boolean
  item: ReimbursementItem | null
  currency: string
  ledgerId: string | null
  onOpenChange: (v: boolean) => void
  onCancelMark: (item: ReimbursementItem) => void
}) {
  const t = useT()
  const { token } = useAuth()
  if (!item) {
    return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent /></Dialog>
  }
  const it = item
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex shrink-0 flex-row items-start justify-between gap-3 border-b border-border/60 bg-gradient-to-br from-muted/40 to-transparent px-6 py-4">
          <DialogTitle className="flex items-center gap-3 pr-8">
            <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-amber-500/15">
              <ReceiptText className="h-6 w-6 text-amber-600" />
            </span>
            <div className="flex min-w-0 flex-col">
              <span className="text-lg font-semibold">
                {formatBalanceCompact(it.amount, currency)}
              </span>
              <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] font-normal">
                <span className="rounded bg-amber-500/15 px-2 py-0.5 text-amber-600">
                  {t('reimbursement.tab.pending')}
                </span>
                <span className="text-muted-foreground">
                  {it.category_name || t('reimbursement.uncategorized')}
                </span>
              </span>
            </div>
          </DialogTitle>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-6 py-4">
          {/* 统计概览:待报销口径。 */}
          <div>
            <div className="mb-2 text-sm font-medium">{t('reimbursement.detail.summary')}</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat
                label={t('reimbursement.detail.amount')}
                value={formatBalanceCompact(it.amount, currency)}
                tone="rose"
              />
              <Stat label={t('reimbursement.detail.status')} value={t('reimbursement.tab.pending')} />
              <Stat
                label={t('reimbursement.detail.company')}
                value={it.company_name || t('reimbursement.filter.companyNone')}
              />
              <Stat
                label={t('reimbursement.detail.originAccount')}
                value={it.account_name || '-'}
              />
            </div>
          </div>

          {it.note ? (
            <div className="break-words whitespace-pre-wrap rounded-lg border border-border p-3 text-sm text-muted-foreground">
              {it.note}
            </div>
          ) : null}

          <AttachmentList files={it.attachments} label={t('reimbursement.field.attachments')} />

          {/* 原支出流水:点行跳原交易详情。 */}
          {it.tx_sync_details && it.tx_sync_details.length > 0 ? (
            <div className="min-w-0">
              <div className="mb-2 text-sm font-medium">{t('reimbursement.detail.originFlow')}</div>
              <LinkedTxList
                details={it.tx_sync_details}
                searchable={false}
                onSelect={(tx) =>
                  openTxDetailBySyncId(token, tx.sync_id, ledgerId ?? undefined, () => onOpenChange(false))
                }
              />
            </div>
          ) : null}
        </div>

        {/* 底部操作栏固定可见。 */}
        <div className="shrink-0 border-t border-border/60 px-6 py-3">
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => onCancelMark(it)}>
              <Trash2 className="mr-1 h-4 w-4" />
              {t('reimbursement.button.cancelMark')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function ReimbursementsPage() {
  const t = useT()
  const { token } = useAuth()
  const { currency, activeLedgerId, ledgers, setActiveLedgerId, refreshLedgers } = useLedgers()

  // 与储蓄罐一致的 stale ledger id 兜底
  const validLedgerId =
    ledgers.find((l) => l.ledger_id === activeLedgerId)?.ledger_id ||
    ledgers[0]?.ledger_id ||
    activeLedgerId ||
    ''

  const [tab, setTab] = useState<'pending' | 'done' | 'companies'>('pending')
  // 关联记录跳转(0043):定位到指定报销项 / 报销单并高亮。
  const [searchParams] = useSearchParams()
  const presetReimbursementId = searchParams.get('reimbursement_id')
  const presetReimbursementItemId = searchParams.get('reimbursement_item_id')
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const [pending, setPending] = useState<ReimbursementItem[]>([])
  const [done, setDone] = useState<Reimbursement[]>([])
  const [accounts, setAccounts] = useState<{ id: string; name: string }[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [companyFilter, setCompanyFilter] = useState('__all__')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // 详情弹窗(0056):点每一条记录都开弹窗(待报销项 / 报销单)。
  const [detailItem, setDetailItem] = useState<ReimbursementItem | null>(null)
  const [detailRb, setDetailRb] = useState<Reimbursement | null>(null)

  // 关联记录跳转(0043):?reimbursement_id= → 已报 Tab;?reimbursement_item_id= → 待报销 Tab。
  // 0056 起不再只「高亮」,而是对应 Tab 的数据到位后**直接打开详情弹窗**。
  const jumpedRef = useRef<string | null>(null)
  const openedDetailRef = useRef<string | null>(null)
  useEffect(() => {
    const key = presetReimbursementId || presetReimbursementItemId
    if (!key || jumpedRef.current === key) return
    jumpedRef.current = key
    if (presetReimbursementId) {
      setTab('done')
      setHighlightId(presetReimbursementId)
    } else if (presetReimbursementItemId) {
      setTab('pending')
      setHighlightId(presetReimbursementItemId)
    }
  }, [presetReimbursementId, presetReimbursementItemId])
  // 数据到位后开弹窗(深链 / 同页面事件兜底都走这里)。
  useEffect(() => {
    const key = presetReimbursementId || presetReimbursementItemId
    if (!key || openedDetailRef.current === key) return
    if (presetReimbursementId) {
      const hit = done.find((r) => r.id === presetReimbursementId)
      if (!hit) return
      openedDetailRef.current = key
      setDetailRb(hit)
    } else if (presetReimbursementItemId) {
      const hit = pending.find((i) => i.id === presetReimbursementItemId)
      if (!hit) return
      openedDetailRef.current = key
      setDetailItem(hit)
    }
  }, [presetReimbursementId, presetReimbursementItemId, done, pending])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 公司管理弹窗
  const [companyDialogOpen, setCompanyDialogOpen] = useState(false)
  const [companyEditingId, setCompanyEditingId] = useState<string | null>(null)
  const [companyName, setCompanyName] = useState('')
  const [companyNote, setCompanyNote] = useState('')
  const [companyDeleteId, setCompanyDeleteId] = useState<string | null>(null)

  // 报销对话框
  const [open, setOpen] = useState(false)
  const [rbAmount, setRbAmount] = useState('')
  const [rbAt, setRbAt] = useState('')
  const [rbNote, setRbNote] = useState('')
  const [rbIcon, setRbIcon] = useState('receipt_long')
  const [rbAccountId, setRbAccountId] = useState('')
  const [rbCompanyId, setRbCompanyId] = useState('')
  const [rbFiles, setRbFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)

  const loadCompanies = useCallback(async () => {
    if (!token) return
    try {
      setCompanies(await fetchCompanies(token))
    } catch {
      setCompanies([])
    }
  }, [token])

  useEffect(() => {
    loadCompanies()
  }, [loadCompanies])

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
      const [items, rbs] = await Promise.all([
        fetchReimbursementItems(token, validLedgerId, 'pending'),
        fetchReimbursements(token, validLedgerId),
      ])
      setPending(items)
      setDone(rbs)
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

  // 交易详情「关联记录」跳回报销详情(0056):同页面时 URL 参数 effect 的 ref guard
  // 会拦截,靠此事件直接打开;跨页面时两条通道都会触发(setState 幂等)。
  useEffect(() => {
    if (!token || !validLedgerId) return
    return onOpenDetailReimbursement((target: ReimbursementDetailTarget) => {
      if (target.kind === 'reimbursement') {
        const hit = done.find((r) => r.id === target.id)
        if (hit) {
          setTab('done')
          setHighlightId(hit.id)
          setDetailRb(hit)
          return
        }
        void fetchReimbursements(token, validLedgerId)
          .then((rows) => {
            const h = rows.find((r) => r.id === target.id)
            if (!h) return
            setTab('done')
            setHighlightId(h.id)
            setDetailRb(h)
          })
          .catch(() => undefined)
      } else {
        const hit = pending.find((i) => i.id === target.id)
        if (hit) {
          setTab('pending')
          setHighlightId(hit.id)
          setDetailItem(hit)
          return
        }
        void fetchReimbursementItems(token, validLedgerId, 'pending')
          .then((rows) => {
            const h = rows.find((i) => i.id === target.id)
            if (!h) return
            setTab('pending')
            setHighlightId(h.id)
            setDetailItem(h)
          })
          .catch(() => undefined)
      }
    })
  }, [token, validLedgerId, done, pending])

  // 公司维度(0037)：待报销列表按公司筛选，合并报销须同公司。
  const filteredPending = useMemo(
    () =>
      companyFilter === '__all__'
        ? pending
        : pending.filter((i) => (i.company_id || '__none__') === companyFilter),
    [pending, companyFilter],
  )
  const selectedItems = useMemo(
    () => pending.filter((i) => selected.has(i.id)),
    [pending, selected],
  )
  const selectedCompanyIds = useMemo(
    () => new Set(selectedItems.map((i) => i.company_id || '')),
    [selectedItems],
  )
  const mixedCompanies = selectedCompanyIds.size > 1
  const companyMismatch = [...selectedCompanyIds].some((id) => id && id !== rbCompanyId)
  // 页面级报销口径统计。
  const pendingTotal = useMemo(
    () => pending.reduce((s, i) => s + (Number(i.amount) || 0), 0),
    [pending],
  )
  const doneTotal = useMemo(
    () => done.reduce((s, r) => s + (Number(r.amount) || 0), 0),
    [done],
  )
  const selectedTotal = useMemo(
    () => selectedItems.reduce((s, i) => s + (Number(i.amount) || 0), 0),
    [selectedItems],
  )

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const openDialog = () => {
    if (selectedItems.length === 0 || mixedCompanies) return
    setRbAmount(selectedTotal.toFixed(2))
    setRbAt(toLocalDateTimeInput(new Date().toISOString()))
    setRbNote('')
    setRbIcon('receipt_long')
    setRbAccountId('')
    setRbCompanyId([...selectedCompanyIds][0] || '')
    setRbFiles([])
    setOpen(true)
  }

  const handleCreate = async () => {
    if (!token || !validLedgerId || !Number(rbAmount) || !rbCompanyId || companyMismatch) return
    setBusy(true)
    try {
      const attachments: AttachmentRef[] = []
      for (let i = 0; i < rbFiles.length; i++) {
        const out = await uploadAttachment(token, { ledger_id: validLedgerId, file: rbFiles[i] })
        attachments.push({
          fileName: out.file_name,
          originalName: out.file_name,
          fileSize: out.size,
          sortOrder: i,
          cloudFileId: out.file_id,
          cloudSha256: out.sha256,
        })
      }
      const acc = accounts.find((a) => a.id === rbAccountId)
      await createReimbursement(token, {
        ledger_id: validLedgerId,
        item_ids: selectedItems.map((i) => i.id),
        amount: Number(rbAmount),
        happened_at: fromLocalDateTimeInput(rbAt),
        note: rbNote.trim() || null,
        icon: rbIcon,
        attachments,
        receive_account_id: rbAccountId || null,
        receive_account_name: acc?.name || null,
        company_id: rbCompanyId,
      })
      setOpen(false)
      setSelected(new Set())
      await load()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setBusy(false)
    }
  }

  const handleCancelItem = async (item: ReimbursementItem) => {
    if (!token) return
    try {
      await deleteReimbursementItem(token, item.id)
      setPending((prev) => prev.filter((i) => i.id !== item.id))
      setSelected((prev) => { const n = new Set(prev); n.delete(item.id); return n })
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleDeleteRb = async (rb: Reimbursement) => {
    if (!token) return
    try {
      await deleteReimbursement(token, rb.id)
      await load()
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleChangeIcon = async (rb: Reimbursement, icon: string) => {
    if (!token) return
    try {
      const updated = await updateReimbursement(token, rb.id, { icon })
      setDone((prev) => prev.map((r) => (r.id === rb.id ? updated : r)))
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  // 公司档案 CRUD
  const openCompanyDialog = (c?: Company) => {
    setCompanyEditingId(c?.id || null)
    setCompanyName(c?.name || '')
    setCompanyNote(c?.note || '')
    setCompanyDialogOpen(true)
  }

  const handleSaveCompany = async () => {
    if (!token || !companyName.trim()) return
    try {
      const body = { name: companyName.trim(), note: companyNote.trim() || null }
      if (companyEditingId) await updateCompany(token, companyEditingId, body)
      else await createCompany(token, body)
      setCompanyDialogOpen(false)
      await loadCompanies()
      setError(null)
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleDeleteCompany = async () => {
    if (!token || !companyDeleteId) return
    try {
      await deleteCompany(token, companyDeleteId)
      setCompanyDeleteId(null)
      await loadCompanies()
      setError(null)
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-6">
      <SectionPageHeader
        section="reimbursements"
        title={t('reimbursement.title')}
        subtitle={t('reimbursement.subtitle')}
      />

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* 页面级概览:报销口径(待报销 / 已报销的金额与笔数),
          与联系人「借还」口径、成员「收支归属」口径都不同。 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label={t('reimbursement.detail.pendingAmount')}
          value={formatBalanceCompact(pendingTotal, currency)}
          tone="rose"
        />
        <Stat label={t('reimbursement.detail.pendingCount')} value={String(pending.length)} />
        <Stat
          label={t('reimbursement.detail.doneAmount')}
          value={formatBalanceCompact(doneTotal, currency)}
          tone="emerald"
        />
        <Stat label={t('reimbursement.detail.doneCount')} value={String(done.length)} />
      </div>

      <Tabs>
        <TabsList>
          <TabsTrigger active={tab === 'pending'} onClick={() => setTab('pending')}>
            {t('reimbursement.tab.pending')} ({pending.length})
          </TabsTrigger>
          <TabsTrigger active={tab === 'done'} onClick={() => setTab('done')}>
            {t('reimbursement.tab.done')} ({done.length})
          </TabsTrigger>
          <TabsTrigger active={tab === 'companies'} onClick={() => setTab('companies')}>
            <Building2 className="mr-1 h-4 w-4" />
            {t('reimbursement.tab.companies')} ({companies.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent>
          {tab === 'pending' ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={filteredPending.length > 0 && filteredPending.every((i) => selected.has(i.id))}
                    onChange={(e) =>
                      setSelected((prev) => {
                        const next = new Set(prev)
                        filteredPending.forEach((i) => (e.target.checked ? next.add(i.id) : next.delete(i.id)))
                        return next
                      })
                    }
                  />
                  {t('reimbursement.selectAll')}
                </label>
                <div className="flex flex-wrap items-center gap-3">
                  <Select
                    value={companyFilter}
                    onValueChange={(v) => { setCompanyFilter(v); setSelected(new Set()) }}
                  >
                    <SelectTrigger className="h-8 w-40 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__all__">{t('reimbursement.filter.companyAll')}</SelectItem>
                      {companies.map((c) => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                      <SelectItem value="__none__">{t('reimbursement.filter.companyNone')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <span className="text-sm text-muted-foreground">
                    {t('reimbursement.selected')} {selectedItems.length} · {t('reimbursement.total')}{' '}
                    <span className="font-mono font-semibold text-foreground">
                      ¥{selectedTotal.toFixed(2)}
                    </span>
                  </span>
                  <Button
                    onClick={openDialog}
                    disabled={selectedItems.length === 0 || mixedCompanies}
                    title={mixedCompanies ? t('reimbursement.mixedCompanyHint') : undefined}
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    {t('reimbursement.button.reimburse')}
                  </Button>
                </div>
              </div>
              {mixedCompanies && (
                <p className="text-xs text-destructive">{t('reimbursement.mixedCompanyHint')}</p>
              )}

              {filteredPending.length === 0 ? (
                <div className="rounded-lg border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
                  {t('reimbursement.pendingEmpty')}
                </div>
              ) : (
                <div className="divide-y rounded-lg border">
                  {filteredPending.map((item) => (
                    <div
                      key={item.id}
                      className={`flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/40 ${
                        highlightId === item.id ? 'bg-primary/10 ring-1 ring-primary/40' : ''
                      }`}
                      onClick={() => setDetailItem(item)}
                      title={t('reimbursement.detail.open') as string}
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(item.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggle(item.id)}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">
                            {item.category_name || t('reimbursement.uncategorized')}
                          </span>
                          {item.company_name && (
                            <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                              {item.company_name}
                            </span>
                          )}
                          <span className="truncate text-sm text-muted-foreground">{item.note || ''}</span>
                        </div>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          {formatIsoDateTimeLocal(item.happened_at)}
                          {item.account_name ? ` · ${item.account_name}` : ''}
                          {item.attachments?.length ? ` · 📎 ${item.attachments.length}` : ''}
                        </div>
                        {/* 0046:原始支出流水已在上方分类/公司/备注/时间/账户完整展示,
                            不再重复渲染同一笔流水,避免「一条信息显示两遍」。 */}
                      </div>
                      <span className="font-mono font-semibold">¥{item.amount.toFixed(2)}</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        title={t('reimbursement.button.cancelMark')}
                        onClick={(e) => {
                          e.stopPropagation()
                          void handleCancelItem(item)
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              {loading && <p className="text-sm text-muted-foreground">{t('common.loading')}</p>}
            </div>
          ) : tab === 'done' ? (
            <div className="space-y-3">
              {done.length === 0 ? (
                <div className="rounded-lg border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
                  {t('reimbursement.doneEmpty')}
                </div>
              ) : (
                done.map((rb) => (
                  <div
                    key={rb.id}
                    className={`cursor-pointer rounded-lg border p-4 transition-colors hover:border-primary/50 hover:bg-muted/30 ${
                      highlightId === rb.id ? 'border-primary bg-primary/5 ring-1 ring-primary/40' : ''
                    }`}
                    onClick={() => setDetailRb(rb)}
                    title={t('reimbursement.detail.open') as string}
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                        <CategoryIcon icon={rb.icon} iconType="material" size={22} className="text-primary" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold">¥{rb.amount.toFixed(2)}</span>
                          <span className="text-xs text-muted-foreground">
                            {formatIsoDateTimeLocal(rb.happened_at)}
                          </span>
                          {rb.company_name && (
                            <span className="rounded bg-primary/10 px-2 py-0.5 text-xs text-primary">
                              {t('reimbursement.field.company')}: {rb.company_name}
                            </span>
                          )}
                          {rb.receive_account_name && (
                            <span className="rounded bg-muted px-2 py-0.5 text-xs">
                              {t('reimbursement.receiveAccount')}: {rb.receive_account_name}
                            </span>
                          )}
                          <span className="rounded bg-muted px-2 py-0.5 text-xs">
                            {rb.item_count} {t('reimbursement.itemUnit')}
                          </span>
                          {rb.attachments?.length ? (
                            <span className="text-xs text-muted-foreground">📎 {rb.attachments.length}</span>
                          ) : null}
                        </div>
                        {rb.note && <p className="mt-1 text-sm text-muted-foreground">{rb.note}</p>}
                        {/* 图标设置 */}
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          <span className="mr-1 text-xs text-muted-foreground">{t('reimbursement.icon')}:</span>
                          {RB_ICONS.map((icon) => (
                            <button
                              key={icon}
                              type="button"
                              title={icon}
                              onClick={() => handleChangeIcon(rb, icon)}
                              className={`flex h-7 w-7 items-center justify-center rounded border ${
                                rb.icon === icon ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-muted'
                              }`}
                            >
                              <CategoryIcon icon={icon} iconType="material" size={16} />
                            </button>
                          ))}
                        </div>
                        {/* 明细 */}
                        {rb.items?.length > 0 && (
                          <ul className="mt-3 space-y-1 border-t pt-2 text-xs text-muted-foreground">
                            {rb.items.map((it) => (
                              <li key={it.id}>
                                <div className="flex justify-between gap-2">
                                  <span className="truncate">
                                    {formatIsoDateTimeLocal(it.happened_at)}
                                    {it.note ? ` · ${it.note}` : ''}
                                  </span>
                                  <span className="font-mono">¥{it.amount.toFixed(2)}</span>
                                </div>
                                {/* 0046:原始支出流水已在上方时间/备注/金额完整展示,
                                    不再重复渲染同一笔流水。 */}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          void handleDeleteRb(rb)
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : (
            /* 公司档案管理 */
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{t('reimbursement.companiesHint')}</p>
                <Button onClick={() => openCompanyDialog()}>
                  <Plus className="mr-1 h-4 w-4" />
                  {t('reimbursement.company.button.add')}
                </Button>
              </div>
              {companies.length === 0 ? (
                <div className="rounded-lg border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
                  {t('reimbursement.companiesEmpty')}
                </div>
              ) : (
                <div className="divide-y rounded-lg border">
                  {companies.map((c) => (
                    <div key={c.id} className="flex items-center gap-3 px-4 py-3">
                      <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">{c.name}</div>
                        {c.note && <div className="text-xs text-muted-foreground">{c.note}</div>}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        title={t('reimbursement.company.button.edit')}
                        onClick={() => openCompanyDialog(c)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        title={t('reimbursement.company.button.delete')}
                        onClick={() => setCompanyDeleteId(c.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </TabsContent>
      </Tabs>

      <ReimbursementItemDetailDialog
        open={!!detailItem}
        item={detailItem}
        currency={currency}
        ledgerId={validLedgerId || null}
        onOpenChange={(v) => {
          if (!v) setDetailItem(null)
        }}
        onCancelMark={(item) => {
          setDetailItem(null)
          void handleCancelItem(item)
        }}
      />
      <ReimbursementDetailDialog
        open={!!detailRb}
        reimbursement={detailRb}
        currency={currency}
        ledgerId={validLedgerId || null}
        onOpenChange={(v) => {
          if (!v) setDetailRb(null)
        }}
        onDelete={(rb) => {
          setDetailRb(null)
          void handleDeleteRb(rb)
        }}
      />
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('reimbursement.dialog.title')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="rounded-lg bg-muted/40 px-3 py-2 text-sm">
              {t('reimbursement.dialog.selected')}: {selectedItems.length} ·{' '}
              <span className="font-mono font-semibold">¥{selectedTotal.toFixed(2)}</span>
            </div>
            <div>
              <Label>{t('reimbursement.field.amount')}</Label>
              <Input
                type="number"
                step="0.01"
                value={rbAmount}
                onChange={(e) => setRbAmount(e.target.value)}
              />
            </div>
            <div>
              <Label>{t('reimbursement.field.time')}</Label>
              <Input
                type="datetime-local"
                step={1}
                value={rbAt}
                onChange={(e) => setRbAt(e.target.value)}
              />
            </div>
            <div>
              <Label>{t('reimbursement.field.company')} <span className="text-destructive">*</span></Label>
              <Select value={rbCompanyId || '__none__'} onValueChange={(v) => setRbCompanyId(v === '__none__' ? '' : v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{t('reimbursement.companyNone')}</SelectItem>
                  {companies.map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {companies.length === 0 && (
                <p className="mt-1 text-xs text-destructive">{t('reimbursement.companyEmpty')}</p>
              )}
              {companyMismatch && (
                <p className="mt-1 text-xs text-destructive">{t('reimbursement.companyMismatchHint')}</p>
              )}
            </div>
            <div>
              <Label>{t('reimbursement.field.receiveAccount')}</Label>
              <Select value={rbAccountId || '__none__'} onValueChange={(v) => setRbAccountId(v === '__none__' ? '' : v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">{t('reimbursement.accountNone')}</SelectItem>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">{t('reimbursement.accountHint')}</p>
            </div>
            <div>
              <Label>{t('reimbursement.field.icon')}</Label>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {RB_ICONS.map((icon) => (
                  <button
                    key={icon}
                    type="button"
                    onClick={() => setRbIcon(icon)}
                    className={`flex h-8 w-8 items-center justify-center rounded border ${
                      rbIcon === icon ? 'border-primary bg-primary/10' : 'border-border hover:bg-muted'
                    }`}
                  >
                    <CategoryIcon icon={icon} iconType="material" size={18} />
                  </button>
                ))}
              </div>
            </div>
            <div>
              <Label>{t('reimbursement.field.note')}</Label>
              <Textarea value={rbNote} onChange={(e) => setRbNote(e.target.value)} rows={2} />
            </div>
            <div>
              <Label>{t('reimbursement.field.attachments')}</Label>
              <input
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setRbFiles(Array.from(e.target.files || []))}
                className="mt-1 block w-full text-sm"
              />
            </div>
            <p className="text-xs text-muted-foreground">{t('reimbursement.noStatsHint')}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
            <Button onClick={handleCreate} disabled={busy || !Number(rbAmount) || !rbCompanyId || companyMismatch}>
              {busy ? t('reimbursement.busy') : t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 公司档案新增/编辑 */}
      <Dialog open={companyDialogOpen} onOpenChange={setCompanyDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {companyEditingId ? t('reimbursement.company.dialog.edit') : t('reimbursement.company.dialog.add')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>{t('reimbursement.company.field.name')} <span className="text-destructive">*</span></Label>
              <Input
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder={t('reimbursement.company.field.namePlaceholder')}
              />
            </div>
            <div>
              <Label>{t('reimbursement.company.field.note')}</Label>
              <Textarea value={companyNote} onChange={(e) => setCompanyNote(e.target.value)} rows={2} />
            </div>
            <p className="text-xs text-muted-foreground">{t('reimbursement.company.dialogHint')}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCompanyDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={handleSaveCompany} disabled={!companyName.trim()}>
              {t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除公司确认：历史报销记录保留公司名快照 */}
      <Dialog open={companyDeleteId !== null} onOpenChange={(v) => { if (!v) setCompanyDeleteId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('reimbursement.company.delete.title')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t('reimbursement.company.delete.desc')}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCompanyDeleteId(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="destructive" onClick={handleDeleteCompany}>
              {t('reimbursement.company.button.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
