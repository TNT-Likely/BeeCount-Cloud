import { useEffect, useMemo, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Pagination,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT
} from '@beecount/ui'

import type {
  AttachmentRef,
  Company,
  Item,
  ReadAccount,
  ReadCategory,
  ReadMember,
  ReadTag,
  ReadTransaction,
  WorkspaceCategory,
} from '@beecount/api-client'

import { TransactionAttachmentEditor, type TransactionAttachmentEditorHandle } from '../components/TransactionAttachmentEditor'
import { CurrencySelectorTrigger } from '../components/CurrencySelector'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { CategoryIcon } from '../components/CategoryIcon'
import { TagPickerDialog } from '../components/TagPickerDialog'
import { TransactionList } from '../components/TransactionList'
import { useSingleFlight } from '../lib/singleFlight'
import { tagTextColorOn } from '../lib/tagColorPalette'
import type { TxForm } from '../forms'

type TransactionsPanelProps = {
  form: TxForm
  /** 账本本位币(大写 ISO)。币种下拉默认值;选=本位币时 form.currency 存 ''。 */
  baseCurrency?: string
  /** v30 多币种:各币种对 baseCurrency 的汇率(1 quote ≈ x base),透传币种选择弹窗展示。 */
  currencyRates?: Record<string, number>
  rows: ReadTransaction[]
  total: number
  page: number
  pageSize: number
  accounts: ReadAccount[]
  categories: ReadCategory[]
  tags: ReadTag[]
  /** 成员名单(0024)。不传时表单隐藏成员区块。 */
  members?: ReadMember[]
  /** 报销公司档案(0037)。勾选报销时必选其一。 */
  companies?: Company[]
  /** 0044:可选关联的物品(新建/编辑账单时选一件物品,该交易成为它的一条流水)。 */
  items?: Item[]
  ledgerOptions: Array<{ ledger_id: string; ledger_name: string }>
  writeLedgerId: string
  onWriteLedgerIdChange: (ledgerId: string) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  canWrite: boolean
  /** 锁账截止时刻(UTC ISO):<= 该时刻的交易行禁用编辑/删除。 */
  lockCutoff?: string | null
  dictionariesLoading?: boolean
  showCreatorColumn?: boolean
  showLedgerColumn?: boolean
  onFormChange: (next: TxForm) => void
  /** Dialog 显隐由外层控制。新建/编辑入口都靠 parent 在 setOpen(true) 之前
   *  把 form 初始化好,然后 setOpen(true)。这样 page 可以把"新建交易"按钮
   *  跟搜索/筛选放同一行,跟内嵌在 panel 内的 onCreate 解耦。 */
  dialogOpen: boolean
  onDialogOpenChange: (open: boolean) => void
  onSave: (attachments?: AttachmentRef[]) => Promise<boolean> | boolean
  onUploadAttachment: (file: File) => Promise<AttachmentRef>
  onReset: () => void
  onReload: () => void
  onPreviewAttachment: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
  resolveAttachmentPreviewUrl: (ref: AttachmentRef) => Promise<string | null>
  /** 自定义分类图标的预签预览 URL 字典,TransactionList 里每行 CategoryIcon
   *  用来拿 blob URL 显示云端上传的 PNG 图标。material icon 不需要。 */
  iconPreviewUrlByFileId?: Record<string, string>
  onEdit: (row: ReadTransaction) => void
  onDelete: (row: ReadTransaction) => void
  /** 行整体点击 → 打开详情弹窗。如果不传则点行无效果(保留兼容性)。 */
  onSelect?: (row: ReadTransaction) => void
  /** dialogOnly:不渲染交易列表/分页,只挂编辑 Dialog + 内嵌 picker。
   *  让全局 edit 容器(GlobalEditTxDialog)能复用 panel 内置的所有字段渲染 +
   *  picker 联动逻辑,不必从头实现。 */
  dialogOnlyMode?: boolean
  /** 批量选择模式 —— 透传到 TransactionList。 */
  selectionMode?: boolean
  selectedIds?: Set<string>
  onToggleSelect?: (row: ReadTransaction, event: React.MouseEvent) => void
  /** §7 共享账本:开启后 tx 列表行末显示"谁记的"chip。 */
  showCreator?: boolean
  /** §7 共享账本:当前 caller user_id,自己创建+编辑的 tx 不显示 chip。 */
  currentUserId?: string | null
  /** 备注显示方式,透传到 TransactionList。默认 'category'。 */
  noteDisplayMode?: 'category' | 'note'
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
  baseCurrency = 'CNY',
  currencyRates,
  rows,
  total,
  page,
  pageSize,
  accounts,
  categories,
  tags,
  members = [],
  companies = [],
  items = [],
  ledgerOptions,
  writeLedgerId,
  onWriteLedgerIdChange,
  onPageChange,
  onPageSizeChange,
  canWrite,
  lockCutoff = null,
  dictionariesLoading = false,
  showCreatorColumn = false,
  showLedgerColumn = false,
  onFormChange,
  dialogOpen,
  onDialogOpenChange,
  onSave,
  onUploadAttachment,
  onReset,
  onReload,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  iconPreviewUrlByFileId,
  onEdit,
  onDelete,
  onSelect,
  dialogOnlyMode,
  selectionMode = false,
  selectedIds,
  onToggleSelect,
  showCreator = false,
  currentUserId,
  noteDisplayMode = 'category'
}: TransactionsPanelProps) {
  const t = useT()
  const open = dialogOpen
  const setOpen = (next: boolean) => { if (!saving) onDialogOpenChange(next) }
  const [selectingImages, setSelectingImages] = useState(false)
  const attachmentEditor = useRef<TransactionAttachmentEditorHandle>(null)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [tagPickerOpen, setTagPickerOpen] = useState(false)
  const [feeExpanded, setFeeExpanded] = useState(false)

  useEffect(() => {
    if (!open) return
    const hasFeeData =
      Boolean(form.transfer_fee_amount?.trim()) ||
      Boolean(form.transfer_discount_amount?.trim()) ||
      Boolean(form.transfer_fee_account_name?.trim())
    setFeeExpanded(form.tx_type === 'transfer' && hasFeeData)
  }, [open, form.editingId, form.tx_type])
  // #450:保存是网络请求,请求返回前的连点会重复建交易 —— 单飞守卫 + 按钮禁用。
  const { saving, guard } = useSingleFlight()
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  const dedupSortNames = (names: string[]) =>
    names
      .filter((name) => name.length > 0)
      .filter((name, index, self) => self.indexOf(name) === index)
      .sort((a, b) => a.localeCompare(b))
  // 账户隐藏(issue #240):记账/转账选择器排除隐藏账户 —— 不能再往它记新账。
  const hiddenAccountNames = new Set(
    accounts.filter((row) => row.hidden).map((row) => row.name.trim())
  )
  const accountOptions = dedupSortNames(accounts.map((row) => row.name.trim()).filter((name) => !hiddenAccountNames.has(name)))
  // E1 唯一例外:编辑一笔本就挂在某隐藏账户上的历史交易时,选择器钉住显示该
  // 隐藏账户(带「已隐藏」灰标),让用户可原样保存;其它隐藏账户仍不出现。
  // 一旦改选走其它选项,该隐藏名字就不会再被钉回来(下次渲染 currentValue 已变)。
  const accountOptionsWithPinned = (currentValue: string) => {
    const trimmed = currentValue.trim()
    if (trimmed && hiddenAccountNames.has(trimmed)) {
      return dedupSortNames([...accountOptions, trimmed])
    }
    return accountOptions
  }
  const categoryOptions = categories
    .filter((row) => row.kind === form.tx_type)
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0)
    .filter((name, index, self) => self.indexOf(name) === index)
    .sort((a, b) => a.localeCompare(b))
  // tag 选择改用 TagPickerDialog,组件自己做 dedup + 搜索 + chip 渲染,这里
  // 不再需要 tagOptions 派生(只保留 tagColorByName 给 trigger 按钮的小 chip
  // 渲染上色用)。
  // 按 name 反查 tag 颜色，tx 列表行里给每个标签 badge 上色。大小写不敏感。
  const tagColorByName = new Map<string, string>()
  for (const row of tags) {
    const key = (row.name || '').trim().toLowerCase()
    if (!key) continue
    if (row.color && !tagColorByName.has(key)) tagColorByName.set(key, row.color)
  }

  // 当前选中的分类 row(按 name + kind 反查),给 CategoryPicker 高亮 + 触发
  // 按钮显示图标。和 TransactionList 行内渲染保持同源。
  const selectedCategoryRow = useMemo<WorkspaceCategory | null>(() => {
    const name = (form.category_name || '').trim().toLowerCase()
    if (!name) return null
    return (
      (categories as WorkspaceCategory[]).find(
        (row) =>
          row.kind === form.tx_type &&
          (row.name || '').trim().toLowerCase() === name,
      ) ?? null
    )
  }, [categories, form.category_name, form.tx_type])

  const isTransfer = form.tx_type === 'transfer'
  const isRefund = form.tx_type === 'refund'
  // 支出组合付款(0028):组合付款时主账户下拉隐藏、不强制 account_name,
  // 改由各付款渠道校验:≥2 条、账户不重复、金额>0、合计=总额(容差 0.01)。
  // 刻意用 form.amount 而非后面才定义的 amountNum,避免 TDZ。
  const paymentSplitReady =
    !(form.payment_split_enabled && form.tx_type === 'expense') ||
    (form.payment_splits.length >= 2 &&
      form.payment_splits.every((r) => Boolean(r.account_id) && Number(r.amount) > 0) &&
      new Set(form.payment_splits.map((r) => r.account_id)).size === form.payment_splits.length &&
      Math.abs(
        form.payment_splits.reduce((s, r) => s + (Number(r.amount) || 0), 0) - (Number(form.amount) || 0)
      ) <= 0.01)
  // 非转账允许不选账户（与 mobile 保持一致，tx.accountId 本来就是 nullable）；
  // 转账必须两端都选（否则无法表达方向）；退款要求选退回账户、不要求分类。
  const companyReady = !(form.tx_type === 'expense' && form.is_reimbursement) || Boolean(form.reimbursement_company_id.trim())
  const canSubmit = Boolean(writeLedgerId.trim()) && Number.isFinite(Number(form.amount)) && Number(form.amount) > 0 && paymentSplitReady && companyReady && (isTransfer
    ? Boolean(form.from_account_name.trim()) && Boolean(form.to_account_name.trim())
    : isRefund
      ? Boolean(form.account_name.trim())
      : Boolean(form.category_name.trim()))
  const selectedTags = form.tags
  const categoryValue = form.category_name.trim()

  const applyTxType = (nextType: TxForm['tx_type']) => {
    if (nextType === 'transfer') {
      // 转账两个标记都隐藏 → 清掉,避免残留脏值。
      // currency 一并清空:转账不支持跨币种且币种控件隐藏,不清会把转出/
      // 转入账户下拉锁死在之前手选的外币过滤里(审查发现)。同时清理支出优惠字段。
      onFormChange({
        ...form,
        tx_type: nextType,
        account_name: '',
        currency: '',
        category_name: '',
        category_kind: 'transfer',
        exclude_from_stats: false,
        exclude_from_budget: false,
        discount_amount: '',
        // 转账不支持成员归属
        member_id: '',
        split_enabled: false,
        member_splits: [],
        // 转账/非支出不支持组合付款
        payment_split_enabled: false,
        payment_splits: []
      })
      return
    }
    const keepCategory = form.category_kind === nextType ? form.category_name : ''
    onFormChange({
      ...form,
      tx_type: nextType,
      category_kind: nextType === 'refund' ? 'expense' : nextType,
      category_name: keepCategory,
      from_account_name: '',
      to_account_name: '',
      // 不计入预算仅 expense 显示;切到 income 时清掉
      exclude_from_budget: nextType === 'expense' ? form.exclude_from_budget : false,
      // 支出优惠仅 expense 有效
      discount_amount: nextType === 'expense' ? form.discount_amount : '',
      // 非转账不应保留转账手续费/优惠字段
      transfer_fee_amount: '',
      transfer_discount_amount: '',
      transfer_fee_account_name: '',
      // 组合付款仅 expense
      payment_split_enabled: nextType === 'expense' ? form.payment_split_enabled : false,
      payment_splits: nextType === 'expense' ? form.payment_splits : []
    })
  }

  // ---- 成员关联 / 多人分摊(0024) ----------------------------------------
  const memberNameById = useMemo(
    () => new Map(members.map((m) => [m.id, m.name])),
    [members]
  )
  const amountNum = Number((form.amount || '').toString().trim()) || 0
  const splitTotal = form.member_splits.reduce(
    (sum, row) => sum + (Number(row.amount) || 0),
    0
  )
  const splitMismatch =
    form.split_enabled &&
    form.member_splits.length > 1 &&
    Math.abs(splitTotal - amountNum) > 0.01

  const toggleSplit = () => {
    if (form.split_enabled) {
      // 关掉分摊 → 退回单成员(取第一行的成员，金额不再需要)
      onFormChange({
        ...form,
        split_enabled: false,
        member_id: form.member_splits[0]?.member_id || form.member_id,
        member_splits: []
      })
      return
    }
    // 打开分摊 → 单成员(若已选)作为第一行，未选则给一个空行
    const first = form.member_id
      ? [{ member_id: form.member_id, name: memberNameById.get(form.member_id) || '', amount: '' }]
      : [{ member_id: '', name: '', amount: '' }]
    onFormChange({ ...form, split_enabled: true, member_splits: first })
  }

  const patchSplitRow = (index: number, patch: Partial<{ member_id: string; amount: string }>) => {
    const next = form.member_splits.map((row, i) => (i === index ? { ...row, ...patch } : row))
    if (patch.member_id !== undefined) {
      next[index] = { ...next[index], name: memberNameById.get(patch.member_id) || '' }
    }
    onFormChange({ ...form, member_splits: next })
  }

  const splitEvenly = () => {
    if (!amountNum || form.member_splits.length === 0) return
    const n = form.member_splits.length
    const each = Math.floor((amountNum * 100) / n) / 100
    const last = Math.round((amountNum - each * (n - 1)) * 100) / 100
    onFormChange({
      ...form,
      member_splits: form.member_splits.map((row, i) => ({
        ...row,
        amount: (i === n - 1 ? last : each).toFixed(2)
      }))
    })
  }

  // ---- 支出组合付款(0028) -----------------------------------------------
  const accountNameById = useMemo(
    () => new Map(accounts.map((a) => [a.id, a.name])),
    [accounts]
  )
  const paymentSplitTotal = form.payment_splits.reduce(
    (sum, row) => sum + (Number(row.amount) || 0),
    0
  )
  const paymentSplitMismatch =
    form.payment_split_enabled &&
    form.payment_splits.length >= 2 &&
    Math.abs(paymentSplitTotal - amountNum) > 0.01
  const paymentSplitDuplicate =
    form.payment_split_enabled &&
    new Set(form.payment_splits.map((r) => r.account_id).filter(Boolean)).size !==
      form.payment_splits.filter((r) => r.account_id).length

  const togglePaymentSplit = () => {
    if (form.payment_split_enabled) {
      const firstId = form.payment_splits[0]?.account_id || ''
      const firstName = form.payment_splits[0]?.account_name || accountNameById.get(firstId) || ''
      onFormChange({
        ...form,
        payment_split_enabled: false,
        payment_splits: [],
        // 关闭组合付款时回填主账户(若首行已选)
        account_name: firstName || form.account_name
      })
      return
    }
    // 开启:用当前主账户作为第一行,第二行留空待选,并自动补差到总额
    const primary = accounts.find((a) => a.name.trim().toLowerCase() === form.account_name.trim().toLowerCase())
    const firstId = primary?.id || ''
    const firstName = primary?.name || form.account_name || ''
    const firstAmt = amountNum > 0 ? amountNum.toFixed(2) : ''
    onFormChange({
      ...form,
      payment_split_enabled: true,
      account_name: '',
      payment_splits: [
        { account_id: firstId, account_name: firstName, amount: firstAmt },
        { account_id: '', account_name: '', amount: '0.00' }
      ]
    })
  }

  const patchPaymentSplitRow = (
    index: number,
    patch: Partial<{ account_id: string; account_name: string; amount: string }>
  ) => {
    const next = form.payment_splits.map((row, i) => (i === index ? { ...row, ...patch } : row))
    if (patch.account_id !== undefined) {
      next[index] = {
        ...next[index],
        account_name: accountNameById.get(patch.account_id) || ''
      }
    }
    onFormChange({ ...form, payment_splits: next })
  }

  const autoFillPaymentSplit = () => {
    if (form.payment_splits.length < 2) return
    const others = form.payment_splits.slice(0, -1).reduce((sum, row) => sum + (Number(row.amount) || 0), 0)
    const last = Math.max(0, Math.round((amountNum - others) * 100) / 100)
    onFormChange({
      ...form,
      payment_splits: form.payment_splits.map((row, i) =>
        i === form.payment_splits.length - 1 ? { ...row, amount: last.toFixed(2) } : row
      )
    })
  }

  const colCount = 8 + (showCreatorColumn ? 1 : 0) + (showLedgerColumn ? 1 : 0)

  return (
    <>
      {/* dialogOnlyMode: 全局编辑容器复用本 panel 的 Dialog + picker 联动,
          不渲染列表 / 分页。 */}
      {dialogOnlyMode ? null : (
        <div className="rounded-xl border border-border/50 bg-card">
          <TransactionList
            items={rows}
            tags={tags}
            categories={categories}
            iconPreviewUrlByFileId={iconPreviewUrlByFileId}
            variant="default"
            showCreator={showCreator}
            currentUserId={currentUserId}
            noteDisplayMode={noteDisplayMode}
            canManage={canWrite}
            lockCutoff={lockCutoff}
            onEdit={(row) => {
              onEdit(row)
              setOpen(true)
            }}
            onDelete={onDelete}
            onSelect={onSelect}
            onPreviewAttachment={onPreviewAttachment}
            resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl}
            selectionMode={selectionMode}
            selectedIds={selectedIds}
            onToggleSelect={onToggleSelect}
          />
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle>{form.editingId ? t('transactions.button.update') : t('transactions.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <fieldset disabled={saving || selectingImages} className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
              <Label>{t('shell.ledger')}</Label>
              <Select value={writeLedgerId || undefined} onValueChange={onWriteLedgerIdChange} disabled={Boolean(form.editingId) || saving}>
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
              {isRefund ? (
                <Input disabled value={t('enum.txType.refund')} />
              ) : (
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
              )}
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.amount')}</Label>
              <Input
                placeholder={t('transactions.placeholder.amount')}
                value={form.amount}
                onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
              />
              {/* v30 多币种:币种另起一行,全宽显示币种全名+国旗(挨金额太窄会截断);
                  选非本位币 → 账户下拉按币种过滤 + 已选账户清空(币种优先联动,
                  transfer 不支持)。 */}
              {form.tx_type !== 'transfer' ? (
                <CurrencySelectorTrigger
                  value={form.currency || baseCurrency}
                  onChange={(code) =>
                    onFormChange({
                      ...form,
                      currency:
                        code.toUpperCase() === baseCurrency.toUpperCase()
                          ? ''
                          : code,
                      account_name: ''
                    })
                  }
                  ratesToBase={currencyRates}
                  rateBase={baseCurrency}
                />
              ) : null}
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.time')}</Label>
              <Input
                type="datetime-local"
                step={1}
                value={isoToDatetimeLocal(form.happened_at)}
                onChange={(e) =>
                  onFormChange({
                    ...form,
                    happened_at: datetimeLocalToIso(e.target.value, form.happened_at)
                  })
                }
              />
            </div>
            {isRefund ? null : (
            <div className="space-y-1">
              <Label>{t('transactions.table.category')}</Label>
              {isTransfer ? (
                <Input disabled value={t('common.none')} />
              ) : (
                // 跟同行的 SelectTrigger 视觉对齐:h-10 + bg-muted + border-input,
                // 图标用 h-6 w-6 圆形塞得进 40px 高度,不撑大行高。
                <button
                  type="button"
                  disabled={dictionariesLoading}
                  onClick={() => setCategoryPickerOpen(true)}
                  className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {selectedCategoryRow ? (
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
                      <CategoryIcon
                        icon={selectedCategoryRow.icon}
                        iconType={selectedCategoryRow.icon_type}
                        iconCloudFileId={selectedCategoryRow.icon_cloud_file_id}
                        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                        size={16}
                        className="text-primary"
                      />
                    </span>
                  ) : null}
                  <span
                    className={`flex-1 truncate ${
                      categoryValue ? '' : 'text-muted-foreground'
                    }`}
                  >
                    {categoryValue || t('transactions.placeholder.categoryName')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              )}
            </div>
            )}

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
                      {accountOptionsWithPinned(form.from_account_name).map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                          {hiddenAccountNames.has(name) ? (
                            <span className="ml-1 text-xs text-muted-foreground">
                              {t('accounts.hidden.optionSuffix')}
                            </span>
                          ) : null}
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
                      {accountOptionsWithPinned(form.to_account_name).map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                          {hiddenAccountNames.has(name) ? (
                            <span className="ml-1 text-xs text-muted-foreground">
                              {t('accounts.hidden.optionSuffix')}
                            </span>
                          ) : null}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="md:col-span-2 rounded-lg border border-border/60 bg-muted/10 p-3">
                  <button
                    type="button"
                    className="flex w-full items-center justify-between text-left text-sm font-medium"
                    onClick={() => setFeeExpanded((v) => !v)}
                  >
                    <span>{t('transactions.transferFee.title')}</span>
                    <span className="text-xs text-muted-foreground">
                      {feeExpanded ? t('transactions.transferFee.collapse') : t('transactions.transferFee.expand')}
                    </span>
                  </button>
                  {feeExpanded ? (
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <div className="space-y-1">
                        <Label>{t('transactions.transferFee.feeAmount')}</Label>
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          placeholder="0.00"
                          value={form.transfer_fee_amount}
                          onChange={(e) => onFormChange({ ...form, transfer_fee_amount: e.target.value })}
                        />
                        <div className="flex flex-wrap gap-1.5 pt-1">
                          {['1', '2', '5', '10', '20'].map((amt) => (
                            <button
                              key={amt}
                              type="button"
                              className="rounded border border-border/70 bg-background px-2 py-0.5 text-xs hover:bg-accent"
                              onClick={() => onFormChange({ ...form, transfer_fee_amount: amt })}
                            >
                              {amt}
                            </button>
                          ))}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <Label>{t('transactions.transferFee.discountAmount')}</Label>
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          placeholder="0.00"
                          value={form.transfer_discount_amount}
                          onChange={(e) => onFormChange({ ...form, transfer_discount_amount: e.target.value })}
                        />
                      </div>
                      <div className="space-y-1 md:col-span-2">
                        <Label>{t('transactions.transferFee.feeAccount')}</Label>
                        <Select
                          value={form.transfer_fee_account_name ? form.transfer_fee_account_name : '__from__'}
                          onValueChange={(value) =>
                            onFormChange({
                              ...form,
                              transfer_fee_account_name: value === '__from__' ? '' : value,
                            })
                          }
                        >
                          <SelectTrigger>
                            <SelectValue placeholder={t('transactions.transferFee.feeAccountFrom')} />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__from__">
                              <span className="text-muted-foreground">
                                {t('transactions.transferFee.feeAccountFrom')}
                              </span>
                            </SelectItem>
                            {accountOptionsWithPinned(form.transfer_fee_account_name).map((name) => (
                              <SelectItem key={name} value={name}>
                                {name}
                                {hiddenAccountNames.has(name) ? (
                                  <span className="ml-1 text-xs text-muted-foreground">
                                    {t('accounts.hidden.optionSuffix')}
                                  </span>
                                ) : null}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">{t('transactions.transferFee.hint')}</p>
                      </div>
                    </div>
                  ) : null}
                </div>
              </>
            ) : (
              !(form.tx_type === 'expense' && form.payment_split_enabled) ? (
                <div className="space-y-1">
                  <Label>{t('transactions.table.account')}</Label>
                  <Select
                    // Radix SelectItem 不允许 value=""(undefined-state 由 placeholder
                    // 渲染),所以用 sentinel "__none__" 表示"不选账户"。和 form 的
                    // 真实空串状态在 value 和 onValueChange 两处来回翻译。
                    value={form.account_name ? form.account_name : '__none__'}
                    disabled={dictionariesLoading}
                    onValueChange={(value) =>
                      onFormChange({ ...form, account_name: value === '__none__' ? '' : value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('transactions.placeholder.accountName')} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">
                        <span className="text-muted-foreground">
                          {t('transactions.placeholder.noAccount')}
                        </span>
                      </SelectItem>
                      {accountOptionsWithPinned(form.account_name).map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                          {hiddenAccountNames.has(name) ? (
                            <span className="ml-1 text-xs text-muted-foreground">
                              {t('accounts.hidden.optionSuffix')}
                            </span>
                          ) : null}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null
            )}

            <div className="space-y-1">
              <Label>{t('tags.title')}</Label>
              {/* tag 多选改用 TagPickerDialog —— mobile 风格的 chip 选择,带搜索
                  + 颜色块,比 DropdownMenu 直观。trigger 按钮里把已选标签缩略
                  显示成彩色 chip,空时占位文案。视觉上跟同行的 SelectTrigger 同高。 */}
              <button
                type="button"
                disabled={dictionariesLoading}
                onClick={() => setTagPickerOpen(true)}
                className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="flex flex-1 items-center gap-1 overflow-hidden">
                  {selectedTags.length === 0 ? (
                    <span className="text-muted-foreground">
                      {t('common.none')}
                    </span>
                  ) : (
                    <span className="flex flex-wrap items-center gap-1 overflow-hidden">
                      {selectedTags.slice(0, 3).map((name) => {
                        const color = tagColorByName.get(name.toLowerCase()) || '#94a3b8'
                        const fg = tagTextColorOn(color)
                        return (
                          <span
                            key={name}
                            className="inline-flex h-5 max-w-[120px] items-center rounded-full px-1.5 text-[11px] leading-none"
                            style={{ background: color, color: fg }}
                            title={name}
                          >
                            <span className="truncate">{name}</span>
                          </span>
                        )
                      })}
                      {selectedTags.length > 3 ? (
                        <span className="text-[11px] text-muted-foreground">
                          +{selectedTags.length - 3}
                        </span>
                      ) : null}
                    </span>
                  )}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>
            <div className="space-y-1 md:col-span-2">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                placeholder={t('transactions.placeholder.note')}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>
            {form.tx_type === 'expense' ? (
              <div className="space-y-1 md:col-span-2">
                <Label>{t('transactions.expenseDiscount.amount')}</Label>
                <Input
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="0.00"
                  value={form.discount_amount}
                  onChange={(e) => onFormChange({ ...form, discount_amount: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">{t('transactions.expenseDiscount.hint')}</p>
              </div>
            ) : null}
            {/* 成员关联 / 多人分摊(0024)。仅 income / expense 显示;转账不支持。 */}
            {form.tx_type !== 'transfer' ? (
              <div className="space-y-2 rounded-lg border border-border/60 bg-muted/10 px-3 py-2 md:col-span-2">
                <div className="flex items-center justify-between gap-2">
                  <Label className="text-sm font-medium">{t('members.title')}</Label>
                  <button
                    type="button"
                    onClick={toggleSplit}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      form.split_enabled
                        ? 'bg-primary/15 text-primary'
                        : 'bg-muted text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {form.split_enabled ? t('members.splitOff') : t('members.splitOn')}
                  </button>
                </div>

                {!form.split_enabled ? (
                  <Select
                    value={form.member_id || '__none__'}
                    onValueChange={(value) =>
                      onFormChange({ ...form, member_id: value === '__none__' ? '' : value })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('members.none')} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">{t('members.none')}</SelectItem>
                      {members.map((m) => (
                        <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <div className="space-y-2">
                    {form.member_splits.map((row, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <div className="min-w-0 flex-1">
                          <Select
                            value={row.member_id || '__none__'}
                            onValueChange={(value) =>
                              patchSplitRow(index, {
                                member_id: value === '__none__' ? '' : value
                              })
                            }
                          >
                            <SelectTrigger>
                              <SelectValue placeholder={t('members.pick')} />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__none__">{t('members.pick')}</SelectItem>
                              {members.map((m) => (
                                <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          placeholder="0.00"
                          className="w-28 font-mono tabular-nums"
                          value={row.amount}
                          onChange={(e) => patchSplitRow(index, { amount: e.target.value })}
                        />
                        <button
                          type="button"
                          disabled={form.member_splits.length <= 1}
                          onClick={() =>
                            onFormChange({
                              ...form,
                              member_splits: form.member_splits.filter((_, i) => i !== index)
                            })
                          }
                          className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                        >
                          {t('common.delete')}
                        </button>
                      </div>
                    ))}
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          onFormChange({
                            ...form,
                            member_splits: [
                              ...form.member_splits,
                              { member_id: '', name: '', amount: '' }
                            ]
                          })
                        }
                        className="rounded border border-border/60 px-2 py-1 text-xs hover:bg-accent/40"
                      >
                        + {t('members.addSplit')}
                      </button>
                      <button
                        type="button"
                        onClick={splitEvenly}
                        className="rounded border border-border/60 px-2 py-1 text-xs hover:bg-accent/40"
                      >
                        {t('members.splitEvenly')}
                      </button>
                      <span className={`ml-auto font-mono text-xs tabular-nums ${splitMismatch ? 'text-destructive' : 'text-muted-foreground'}`}>
                        {t('members.splitTotal')} {splitTotal.toFixed(2)} / {amountNum.toFixed(2)}
                        {splitMismatch ? ` · ${t('members.splitMismatch')}` : ''}
                      </span>
                    </div>
                  </div>
                )}
                {members.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{t('members.emptyHint')}</p>
                ) : null}
              </div>
            ) : null}
            {/* 支出组合付款(0028,仅 expense):总额恒等,多账户分摊自动补差。 */}
            {form.tx_type === 'expense' ? (
              <div className="space-y-2 rounded-lg border border-border/60 bg-muted/10 px-3 py-2 md:col-span-2">
                <div className="flex items-center justify-between gap-2">
                  <Label className="text-sm font-medium">{t('paymentSplit.title')}</Label>
                  <button
                    type="button"
                    onClick={togglePaymentSplit}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      form.payment_split_enabled
                        ? 'bg-primary/15 text-primary'
                        : 'bg-muted text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {form.payment_split_enabled ? t('paymentSplit.off') : t('paymentSplit.on')}
                  </button>
                </div>

                {!form.payment_split_enabled ? (
                  <p className="text-xs text-muted-foreground">{t('paymentSplit.hint')}</p>
                ) : (
                  <div className="space-y-2">
                    {form.payment_splits.map((row, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <div className="min-w-0 flex-1">
                          <Select
                            value={row.account_id || '__none__'}
                            onValueChange={(value) =>
                              patchPaymentSplitRow(index, {
                                account_id: value === '__none__' ? '' : value
                              })
                            }
                          >
                            <SelectTrigger>
                              <SelectValue placeholder={t('transactions.placeholder.accountName')} />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__none__">{t('transactions.placeholder.noAccount')}</SelectItem>
                              {accounts
                                .filter((a) => a.id && a.name && !a.hidden)
                                .map((a) => (
                                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                                ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          placeholder="0.00"
                          className="w-28 font-mono tabular-nums"
                          value={row.amount}
                          onChange={(e) => patchPaymentSplitRow(index, { amount: e.target.value })}
                        />
                        <button
                          type="button"
                          disabled={form.payment_splits.length <= 2}
                          onClick={() =>
                            onFormChange({
                              ...form,
                              payment_splits: form.payment_splits.filter((_, i) => i !== index)
                            })
                          }
                          className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                        >
                          {t('common.delete')}
                        </button>
                      </div>
                    ))}
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          onFormChange({
                            ...form,
                            payment_splits: [
                              ...form.payment_splits,
                              { account_id: '', account_name: '', amount: '' }
                            ]
                          })
                        }
                        className="rounded border border-border/60 px-2 py-1 text-xs hover:bg-accent/40"
                      >
                        + {t('paymentSplit.add')}
                      </button>
                      <button
                        type="button"
                        onClick={autoFillPaymentSplit}
                        className="rounded border border-border/60 px-2 py-1 text-xs hover:bg-accent/40"
                      >
                        {t('paymentSplit.autoFill')}
                      </button>
                      <span className={`ml-auto font-mono text-xs tabular-nums ${paymentSplitMismatch || paymentSplitDuplicate ? 'text-destructive' : 'text-muted-foreground'}`}>
                        {t('paymentSplit.total')} {paymentSplitTotal.toFixed(2)} / {amountNum.toFixed(2)}
                        {paymentSplitMismatch ? ` · ${t('paymentSplit.mismatch')}` : ''}
                        {paymentSplitDuplicate ? ` · ${t('paymentSplit.error.duplicateAccount')}` : ''}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">{t('paymentSplit.hint')}</p>
                  </div>
                )}
              </div>
            ) : null}
            {/* §三 标记开关 — 按当前 type 条件显示:
                  不计入收支:income / expense(转账本就不进收支,隐藏)
                  不计入预算:仅 expense(预算只统计支出) */}
            {form.tx_type !== 'transfer' ? (
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <p className="text-sm font-medium">{t('txFlagExcludeFromStats')}</p>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.exclude_from_stats}
                  aria-label={t('txFlagExcludeFromStats') as string}
                  onClick={() =>
                    onFormChange({ ...form, exclude_from_stats: !form.exclude_from_stats })
                  }
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                    form.exclude_from_stats ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.exclude_from_stats ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            ) : null}
            {form.tx_type === 'expense' ? (
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <p className="text-sm font-medium">{t('txFlagExcludeFromBudget')}</p>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.exclude_from_budget}
                  aria-label={t('txFlagExcludeFromBudget') as string}
                  onClick={() =>
                    onFormChange({ ...form, exclude_from_budget: !form.exclude_from_budget })
                  }
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                    form.exclude_from_budget ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.exclude_from_budget ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            ) : null}
            {/* 报销(0035)：仅支出显示。勾选后这笔账单进入报销页"待报销"，
                服务端强制不记收支/不记预算。 */}
            {form.tx_type === 'expense' ? (
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <div>
                  <p className="text-sm font-medium">{t('txFlagIsReimbursement')}</p>
                  <p className="text-xs text-muted-foreground">{t('txFlagIsReimbursementHint')}</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.is_reimbursement}
                  aria-label={t('txFlagIsReimbursement') as string}
                  onClick={() =>
                    onFormChange({
                      ...form,
                      is_reimbursement: !form.is_reimbursement,
                      // 报销支出强制不记收支/预算，与后端口径一致
                      ...(form.is_reimbursement
                        ? {}
                        : { exclude_from_stats: true, exclude_from_budget: true }),
                    })
                  }
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                    form.is_reimbursement ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.is_reimbursement ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            ) : null}
            {/* 报销公司维度(0037)：勾选报销后公司必填，来源为报销页的公司档案。 */}
            {form.tx_type === 'expense' && form.is_reimbursement ? (
              <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <Label>
                  {t('txFlagReimbursementCompany')} <span className="text-destructive">*</span>
                </Label>
                <Select
                  value={form.reimbursement_company_id || '__none__'}
                  onValueChange={(v) =>
                    onFormChange({
                      ...form,
                      reimbursement_company_id: v === '__none__' ? '' : v,
                      reimbursement_company_name: companies.find((c) => c.id === v)?.name || '',
                    })
                  }
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('txFlagReimbursementCompanyNone')}</SelectItem>
                    {companies.map((c) => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className={`mt-1 text-xs ${companies.length === 0 ? 'text-destructive' : 'text-muted-foreground'}`}>
                  {companies.length === 0
                    ? t('txFlagReimbursementCompanyEmpty')
                    : t('txFlagReimbursementCompanyHint')}
                </p>
              </div>
            ) : null}
            {/* 关联物品(0044):仅支出/收入显示。选中后该交易成为这件物品的
                一条流水(物品的收支统计与净成本自动包含它)。 */}
            {(form.tx_type === 'expense' || form.tx_type === 'income') ? (
              <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <Label>{t('txFieldLinkedItem')}</Label>
                <Select
                  value={form.item_id || '__none__'}
                  onValueChange={(v) =>
                    onFormChange({ ...form, item_id: v === '__none__' ? '' : v })
                  }
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('txFieldLinkedItemNone')}</SelectItem>
                    {items.map((it) => (
                      <SelectItem key={it.id} value={it.id}>
                        {it.name} · ¥{Number(it.price || 0).toFixed(2)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-xs text-muted-foreground">
                  {items.length === 0
                    ? t('txFieldLinkedItemEmpty')
                    : t('txFieldLinkedItemHint')}
                </p>
              </div>
            ) : null}
            {open ? <TransactionAttachmentEditor
              key={`${writeLedgerId}:${form.editingId || 'new'}`}
              ref={attachmentEditor}
              attachments={form.attachments}
              disabled={!canWrite || saving || selectingImages || !writeLedgerId}
              onSelectingChange={setSelectingImages}
              upload={onUploadAttachment}
              resolvePreview={resolveAttachmentPreviewUrl}
            /> : null}
          </fieldset>
          </div>
          <DialogFooter className="shrink-0 border-t border-border/60 bg-card px-6 py-4">
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => {
                onReset()
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canWrite || !canSubmit || saving || selectingImages}
              onClick={() =>
                guard(async () => {
                  const refs = await attachmentEditor.current?.prepare()
                  if (!refs) return
                  const success = await onSave(refs)
                  if (success) {
                    onDialogOpenChange(false)
                  }
                })
              }
            >
              {form.editingId ? t('transactions.button.update') : t('transactions.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 标签 picker —— chip 多选,跟 TagsPanel 卡片视觉一致。 */}
      <TagPickerDialog
        open={tagPickerOpen}
        onClose={() => setTagPickerOpen(false)}
        tags={tags}
        selectedNames={selectedTags}
        onChange={(names) => onFormChange({ ...form, tags: names })}
        onClearAll={() => onFormChange({ ...form, tags: [] })}
      />

      {/* 分类 picker —— 跟 mobile category_selector_dialog 同样的网格 + 子级
          展开交互。expense / income 切换跟随 form.tx_type;转账类型不开 picker。
          移除"未分类"footer —— 非转账交易必选分类(对齐 mobile transaction_editor_page,
          page 层 onSaveTransaction 也会再 guard 一次)。 */}
      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind={form.tx_type === 'income' ? 'income' : 'expense'}
        rows={categories as WorkspaceCategory[]}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={selectedCategoryRow?.id}
        title={t('transactions.placeholder.categoryName')}
        onSelect={(cat) => {
          onFormChange({
            ...form,
            category_name: cat.name.trim(),
            category_kind: form.tx_type === 'refund' ? 'expense' : form.tx_type,
          })
        }}
      />
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
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
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
