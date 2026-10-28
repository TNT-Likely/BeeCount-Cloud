import { useMemo, useState } from 'react'

import { Check, ExternalLink, Pencil, Plus, Search, Trash2, Undo2 } from 'lucide-react'

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
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

import type { ReadRefund, ReadTransaction } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { RefundDestinationFields } from '../components/RefundDestinationFields'
import type { RefundForm } from '../forms'
import { refundDefaults } from '../forms'
import {
  matchRefundTxCandidates,
  nextRefundStatus,
  refundProgress,
  refundTxLabel,
  validateRefundInput,
} from '../lib/refunds'
import { refundLegsFromWire } from '../lib/txExtensions'

/**
 * 0022 交易退款面板(App issue #465 退款功能)。
 *
 * 退款挂在原始支出交易上,支持同单多笔部分退;「下一步」按钮把 pending
 * 推进到 received(实际 PATCH 由 page 层 onAdvanceStatus 完成)。
 * 保存前用 validateRefundInput 做与服务端同规则的预校验(超退即时提示),
 * 服务端仍是最终权威。
 */

export type RefundAccountOption = {
  id: string
  name: string
}

type RefundsPanelProps = {
  refunds: readonly ReadRefund[]
  /** 原单候选(workspace 交易;只用支出交易)。 */
  txs: readonly ReadTransaction[]
  accountOptions: readonly RefundAccountOption[]
  currency: string
  form: RefundForm
  onFormChange: (next: RefundForm) => void
  onSubmit: () => Promise<boolean> | boolean
  /** 状态推进:page 层 PATCH status。 */
  onAdvanceStatus: (row: ReadRefund, nextStatus: 'received') => Promise<void> | void
  onDelete: (row: ReadRefund) => Promise<void> | void
  /** 点「原单」打开关联交易的详情弹窗(传原单 txId)。不传则不显示该入口。 */
  onOpenOriginalTx?: (txId: string) => void
  canManage: boolean
  /** 搜索框初始值。交易详情「查看退款」跳过来时预填原单 id,列表自动
   *  过滤出该原单的退款;只作初始值,之后用户可自由改。 */
  initialQuery?: string
  /** 需要高亮的原单 id(来源同上),命中行加 primary 描边,帮用户一眼定位。 */
  highlightTxId?: string | null
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  pending: 'secondary',
  received: 'default',
  cancelled: 'destructive',
}

function dateOnly(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 10)
}

/** ISO(UTC) → datetime-local 输入框需要的本地 'YYYY-MM-DDTHH:mm'。 */
function isoToDatetimeLocal(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** datetime-local 的本地串 → 后端要的 ISO(保留秒与时区,避免跨时区丢精度)。 */
function datetimeLocalToIso(local: string, fallback: string): string {
  if (!local) return fallback
  const d = new Date(local)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toISOString()
}

export function RefundsPanel({
  refunds,
  txs,
  accountOptions,
  currency,
  form,
  onFormChange,
  onSubmit,
  onAdvanceStatus,
  onDelete,
  onOpenOriginalTx,
  canManage,
  initialQuery = '',
  highlightTxId = null,
}: RefundsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [txPickerOpen, setTxPickerOpen] = useState(false)
  const [txQuery, setTxQuery] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadRefund | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [advancingId, setAdvancingId] = useState<string | null>(null)
  // 列表筛选(前端内存过滤,refunds 已全量在内存):关键词 + 状态。
  // 布局对齐流水页顶部 toolbar:搜索 + 状态下拉 + 右侧计数/新建。
  const [query, setQuery] = useState(initialQuery)
  const [statusFilter, setStatusFilter] = useState<'' | 'pending' | 'received' | 'cancelled'>('')

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.id, a.name)
    return map
  }, [accountOptions])

  const txById = useMemo(() => {
    const map = new Map<string, ReadTransaction>()
    for (const tx of txs) map.set(tx.id, tx)
    return map
  }, [txs])

  /** 每个原单的有效退款合计(非 cancelled),给选择器显示「可退」用。 */
  const refundedByTx = useMemo(() => {
    const map = new Map<string, number>()
    for (const r of refunds) {
      if (r.status === 'cancelled') continue
      map.set(r.original_tx_id, (map.get(r.original_tx_id) || 0) + (r.amount || 0))
    }
    return map
  }, [refunds])

  const candidates = useMemo(
    () => matchRefundTxCandidates(txs, txQuery, 30),
    [txs, txQuery],
  )

  /**
   * 工具栏筛选:关键词命中 原因/备注/原单 id/去向账户/原单分类备注,
   * 叠加状态下拉。空 keyword = 只看状态。
   */
  const filteredRefunds = useMemo(() => {
    const q = query.trim().toLowerCase()
    return refunds.filter((row) => {
      if (statusFilter && row.status !== statusFilter) return false
      if (!q) return true
      const tx = txById.get(row.original_tx_id)
      const haystack = [
        row.reason,
        row.note,
        row.original_tx_id,
        resolveAccountName(row),
        destinationLabel(row),
        tx?.category_name,
        tx?.note,
        tx?.account_name,
        String(row.amount),
        row.original_amount == null ? '' : String(row.original_amount),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
    // resolveAccountName / destinationLabel 只依赖 accountOptions + t,两者
    // 都在下面 memo 的依赖里隐含稳定;显式列出避免 lint 误报。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refunds, query, statusFilter, txById, accountOptions])

  const resolveAccountName = (row: ReadRefund) =>
    row.account_name ||
    (row.account_id ? accountNameById.get(row.account_id) || '' : '')

  /** 列表里展示退款去向:分别退款时列出各卡名,否则退回账户名。 */
  const destinationLabel = (row: ReadRefund): string => {
    if (row.refund_mode === 'split' && row.refund_legs && row.refund_legs.length) {
      const names = row.refund_legs
        .map((l) => l.accountName || l.accountId)
        .filter(Boolean)
      return names.length ? `${t('refunds.mode.split')} · ${names.join('、')}` : t('refunds.mode.split')
    }
    return resolveAccountName(row)
  }

  // --- 表单派生:当前原单的可退额度 + 预校验 ------------------------------ #
  // 原单金额:优先取实时 workspace 交易(原始 tx 未删),否则回退到记录里
  // 存的原单金额;两者皆不可得 = null(原单已删且当初未记录金额)。
  // 返回 null 时 formRemaining 也为 null,保存预校验跳过超额上限
  // (服务端仍是最终权威,见 validateRefundInput 的 remaining===null 分支)。
  const formOriginalAmount = useMemo(() => {
    const tx = txById.get(form.original_tx_id)
    if (tx) {
      const a = Number(tx.amount)
      return Number.isFinite(a) && a > 0 ? a : null
    }
    const raw = Number(form.original_amount)
    return Number.isFinite(raw) && raw > 0 ? raw : null
  }, [txById, form.original_tx_id, form.original_amount])

  const formRemaining = useMemo(() => {
    if (formOriginalAmount === null) return null
    // 同单其他(非本笔)有效退款
    let others = 0
    for (const r of refunds) {
      if (r.status === 'cancelled') continue
      if (r.id === form.editingId) continue
      if (r.original_tx_id !== form.original_tx_id) continue
      others += r.amount || 0
    }
    return Math.max(formOriginalAmount - others, 0)
  }, [refunds, form.editingId, form.original_tx_id, formOriginalAmount])

  const formError = validateRefundInput({
    originalTxId: form.original_tx_id,
    amount: Number(form.amount),
    remainingRefundable: form.original_tx_id ? formRemaining : null,
    refundMode: form.refund_mode,
    refundLegs: form.refund_legs,
  })

  const handleOpenCreate = () => {
    onFormChange(refundDefaults())
    setTxQuery('')
    setDialogOpen(true)
  }

  const handleOpenEdit = (row: ReadRefund) => {
    const mode: RefundForm['refund_mode'] = row.refund_mode === 'split' ? 'split' : 'unified'
    onFormChange({
      editingId: row.id,
      original_tx_id: row.original_tx_id || '',
      original_tx_label: row.original_tx_id
        ? refundTxLabel({
            amount: row.original_amount ?? undefined,
            category_name: txById.get(row.original_tx_id)?.category_name ?? null,
            note: txById.get(row.original_tx_id)?.note ?? null,
            happened_at: txById.get(row.original_tx_id)?.happened_at,
          })
        : '',
      original_amount:
        row.original_amount === null || row.original_amount === undefined
          ? ''
          : String(row.original_amount),
      amount: String(row.amount ?? 0),
      currency: row.currency || '',
      account_id: row.account_id || '',
      account_name:
        row.account_name || (row.account_id ? accountNameById.get(row.account_id) || '' : ''),
      refund_mode: mode,
      refund_legs: mode === 'split' ? refundLegsFromWire(row.refund_legs) : [],
      happened_at: row.happened_at,
      reason: row.reason || '',
      status: (row.status as RefundForm['status']) || 'pending',
      note: row.note || '',
    })
    setTxQuery('')
    setDialogOpen(true)
  }

  const handleSelectTx = (tx: ReadTransaction) => {
    // 原单用合并付款(两账户分摊)时,默认「分别退款」并按原分摊预填腿,
    // 直接对应「两账户分摊 → 分别退至各卡」的需求(0025)。
    const legs = tx.payment_legs && tx.payment_legs.length ? refundLegsFromWire(
      tx.payment_legs.map((l) => ({ accountId: l.accountId, accountName: l.accountName, amount: l.amount })),
    ) : []
    const useSplit = legs.length > 0
    onFormChange({
      ...form,
      original_tx_id: tx.id,
      original_tx_label: refundTxLabel(tx),
      original_amount: String(tx.amount ?? 0),
      currency: form.currency || tx.currency_code || '',
      refund_mode: useSplit ? 'split' : 'unified',
      refund_legs: useSplit ? legs : [],
      account_id: form.account_id || tx.account_id || '',
      account_name: form.account_name || tx.account_name || '',
    })
    setTxPickerOpen(false)
  }

  const handleSubmit = async () => {
    if (formError) return
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const handleAdvance = async (row: ReadRefund) => {
    const next = nextRefundStatus(row.status)
    if (next !== 'received') return
    setAdvancingId(row.id)
    try {
      await onAdvanceStatus(row, next)
    } finally {
      setAdvancingId(null)
    }
  }

  const errorText = formError ? t(`refunds.error.${formError}`) : null

  return (
    <div className="space-y-3">
      {/* 顶部 toolbar:对齐流水页一行布局 —— 搜索 + 状态筛选 + 右侧计数/新建 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-9 w-[240px] bg-muted pl-8 lg:w-[340px]"
              placeholder={t('refunds.toolbar.search')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <Select
            value={statusFilter || 'all'}
            onValueChange={(value) =>
              setStatusFilter(value === 'all' ? '' : (value as typeof statusFilter))
            }
          >
            <SelectTrigger className="h-9 w-[120px] bg-muted">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('refunds.toolbar.statusAll')}</SelectItem>
              <SelectItem value="pending">{t('refunds.status.pending')}</SelectItem>
              <SelectItem value="received">{t('refunds.status.received')}</SelectItem>
              <SelectItem value="cancelled">{t('refunds.status.cancelled')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {t('refunds.toolbar.count').replace('{count}', String(filteredRefunds.length))}
          </span>
          <Button className="h-9" disabled={!canManage} onClick={handleOpenCreate}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t('refunds.button.create')}
          </Button>
        </div>
      </div>

      {/* 行式列表:密度对齐流水页 TransactionRow(hover 底色 + 细分割线 +
          圆角容器);空态按「无数据 / 筛选无结果」给不同文案。 */}
      {filteredRefunds.length === 0 ? (
        <div className="rounded-xl border border-border/70 bg-background">
          <EmptyState
            icon={<Undo2 className="h-6 w-6" />}
            title={refunds.length === 0 ? t('refunds.empty') : t('refunds.emptyFiltered')}
            description={refunds.length === 0 ? t('refunds.emptyDesc') : t('refunds.emptyFilteredDesc')}
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border/70 bg-background">
          <div className="divide-y divide-border/60">
            {filteredRefunds.map((row) => {
              const progress = refundProgress(row.original_amount, row.refunded_total)
              const next = nextRefundStatus(row.status)
              const cancelled = row.status === 'cancelled'
              return (
                <div
                  key={row.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleOpenEdit(row)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      handleOpenEdit(row)
                    }
                  }}
                  className={`group flex cursor-pointer items-start gap-3 px-4 py-2.5 transition-colors hover:bg-accent/30 ${
                    cancelled ? 'opacity-60' : ''
                  } ${
                    highlightTxId && row.original_tx_id === highlightTxId
                      ? 'bg-primary/5 ring-1 ring-inset ring-primary/30'
                      : ''
                  }`}
                >
                  <span
                    aria-hidden
                    className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  >
                    <Undo2 className="h-4 w-4" />
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium">
                        {row.reason || t('refunds.untitled')}
                      </span>
                      <Badge variant={STATUS_VARIANT[row.status] || 'outline'} className="text-[11px]">
                        {t(`refunds.status.${row.status}`)}
                      </Badge>
                      {row.refund_mode === 'split' ? (
                        <Badge variant="outline" className="text-[11px]">
                          {t('refunds.mode.split')}
                        </Badge>
                      ) : null}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-muted-foreground">
                      <span>
                        {isoToDatetimeLocal(row.happened_at).replace('T', ' ') ||
                          dateOnly(row.happened_at)}
                      </span>
                      {destinationLabel(row) ? (
                        <>
                          <span aria-hidden>·</span>
                          <span className="max-w-[220px] truncate">{destinationLabel(row)}</span>
                        </>
                      ) : null}
                      <span aria-hidden>·</span>
                      {onOpenOriginalTx ? (
                        // 0028:就地打开关联交易详情(不跳页面);stopPropagation
                        // 避免触发整行的「编辑退款」。
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation()
                            onOpenOriginalTx(row.original_tx_id)
                          }}
                          title={t('refunds.viewOriginalTx') as string}
                          className="inline-flex items-center gap-0.5 rounded text-primary transition-colors hover:text-primary/80 hover:underline"
                        >
                          {t('refunds.originalTxShort')} {row.original_tx_id.slice(0, 8)}
                          <ExternalLink className="h-3 w-3" />
                        </button>
                      ) : (
                        <span className="truncate">
                          {t('refunds.originalTxShort')} {row.original_tx_id.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    {/* 原单退款进度:已退 / 原单 */}
                    {row.original_amount ? (
                      <div className="mt-1.5 flex items-center gap-2">
                        <div className="h-1.5 w-full max-w-[160px] overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${Math.round(progress.ratio * 100)}%` }}
                          />
                        </div>
                        <span className="text-[11px] text-muted-foreground">
                          {t('refunds.progress')
                            .replace('{refunded}', row.refunded_total.toFixed(2))
                            .replace('{original}', String(row.original_amount))}
                        </span>
                      </div>
                    ) : null}
                  </div>

                  <div className="flex shrink-0 flex-col items-end gap-0.5">
                    {/* 操作:桌面 hover 显现;小屏(无 hover)常驻可点 */}
                    <div className="flex items-center gap-0.5 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
                      {next ? (
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7"
                          aria-label={t('refunds.advance.received') as string}
                          disabled={!canManage || advancingId === row.id}
                          onClick={(event) => {
                            event.stopPropagation()
                            void handleAdvance(row)
                          }}
                        >
                          <Check className="h-3.5 w-3.5" />
                        </Button>
                      ) : null}
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7"
                        aria-label={t('common.edit') as string}
                        disabled={!canManage}
                        onClick={(event) => {
                          event.stopPropagation()
                          handleOpenEdit(row)
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-destructive hover:text-destructive"
                        aria-label={t('common.delete') as string}
                        disabled={!canManage}
                        onClick={(event) => {
                          event.stopPropagation()
                          setPendingDelete(row)
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Amount
                      value={row.amount}
                      currency={row.currency || currency}
                      size="md"
                      bold
                      tone="positive"
                    />
                    {row.original_amount ? (
                      <span className="text-[11px] text-muted-foreground line-through">
                        <Amount
                          value={row.original_amount}
                          currency={row.currency || currency}
                          size="xs"
                          tone="muted"
                        />
                      </span>
                    ) : null}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('refunds.button.update') : t('refunds.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            {/* 原单选择 */}
            <div className="space-y-1">
              <Label>{t('refunds.field.originalTx')}</Label>
              <button
                type="button"
                onClick={() => setTxPickerOpen(true)}
                className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
              >
                <span className={`truncate ${form.original_tx_label ? '' : 'text-muted-foreground'}`}>
                  {form.original_tx_label || t('refunds.field.originalTxPlaceholder')}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
              {form.original_tx_id && formRemaining !== null ? (
                <p className="text-[11px] text-muted-foreground">
                  {t('refunds.field.refundable').replace('{remaining}', formRemaining.toFixed(2))}
                </p>
              ) : null}
              {/* 0028:编辑 / 新建时也能就地看一眼原单,确认退的是哪笔支出。 */}
              {form.original_tx_id && onOpenOriginalTx ? (
                <button
                  type="button"
                  onClick={() => onOpenOriginalTx(form.original_tx_id)}
                  className="inline-flex items-center gap-0.5 text-[11px] font-medium text-primary transition-colors hover:text-primary/80 hover:underline"
                >
                  {t('refunds.viewOriginalTx')}
                  <ExternalLink className="h-3 w-3" />
                </button>
              ) : null}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('refunds.field.amount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0.00"
                  value={form.amount}
                  onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('refunds.field.happenedAt')}</Label>
                <Input
                  type="datetime-local"
                  value={isoToDatetimeLocal(form.happened_at)}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      happened_at: datetimeLocalToIso(e.target.value, form.happened_at)
                    })
                  }
                />
              </div>
            </div>

            {/* 退款去向:统一退至一张卡 / 分别退至多张卡(0025) */}
            <RefundDestinationFields
              form={form}
              onFormChange={onFormChange}
              accountOptions={accountOptions}
              originalPaymentLegs={
                form.original_tx_id ? (txById.get(form.original_tx_id)?.payment_legs ?? null) : null
              }
            />

            <div className="space-y-1">
              <Label>{t('refunds.field.status')}</Label>
              <Select
                value={form.status}
                onValueChange={(value) =>
                  onFormChange({ ...form, status: value as RefundForm['status'] })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pending">{t('refunds.status.pending')}</SelectItem>
                  <SelectItem value="received">{t('refunds.status.received')}</SelectItem>
                  <SelectItem value="cancelled">{t('refunds.status.cancelled')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label>{t('refunds.field.reason')}</Label>
              <Input
                value={form.reason}
                placeholder={t('refunds.field.reasonPlaceholder')}
                onChange={(e) => onFormChange({ ...form, reason: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('refunds.field.note')}</Label>
              <Textarea
                rows={2}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            {errorText ? (
              <p className="text-xs text-destructive">{errorText}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={submitting || !canManage || formError !== null}
              onClick={() => void handleSubmit()}
            >
              {form.editingId ? t('refunds.button.update') : t('refunds.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 原单选择器:搜索 + 支出交易列表 */}
      <Dialog open={txPickerOpen} onOpenChange={setTxPickerOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('refunds.field.originalTx')}</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={txQuery}
            placeholder={t('refunds.field.searchPlaceholder')}
            onChange={(e) => setTxQuery(e.target.value)}
          />
          <div className="max-h-72 space-y-1 overflow-y-auto">
            {candidates.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                {t('refunds.field.noMatch')}
              </p>
            ) : (
              candidates.map((tx) => {
                const refunded = refundedByTx.get(tx.id) || 0
                const remaining = Math.max((Number(tx.amount) || 0) - refunded, 0)
                return (
                  <button
                    key={tx.id}
                    type="button"
                    onClick={() => handleSelectTx(tx)}
                    className="flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-accent/40"
                  >
                    <span className="truncate">{refundTxLabel(tx)}</span>
                    {refunded > 0 ? (
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {t('refunds.field.refundable').replace('{remaining}', remaining.toFixed(2))}
                      </span>
                    ) : null}
                  </button>
                )
              })
            )}
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('refunds.delete.title')}
        description={t('refunds.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
