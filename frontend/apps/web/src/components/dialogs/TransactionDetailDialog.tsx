import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  createRefund,
  fetchReadRefunds,
  fetchWorkspaceAccounts,
  resolveApiUrl,
  type AttachmentRef,
  type ReadRefund,
  type RefundCreatePayload,
  type TxDiscount,
  type TxMemberShare,
  type TxPaymentLeg,
  type TxTransferFee,
  type WorkspaceAccount,
  type WorkspaceTag,
  type WorkspaceTransaction,
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
  useToast,
} from '@beecount/ui'
import {
  buildTagColorMap,
  refundCardSplit,
  refundCardSplitFromList,
  refundDefaults,
  refundLegsFromPaymentLegs,
  refundLegsToWire,
  refundTxLabel,
  isFullyRefundedFromSplit,
  RefundDestinationFields,
  TagChip,
  validateRefundInput,
  type RefundForm,
} from '@beecount/web-features'
import {
  BadgePercent,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Edit3,
  Gift,
  Hash,
  ImageOff,
  Layers,
  Package,
  Receipt,
  RotateCcw,
  Tag,
  User,
  Users,
  Wallet,
  X,
} from 'lucide-react'

import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useAttachmentPreview } from '../../context/AttachmentPreviewContext'
import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

import { RefundDetailDialog } from './RefundDetailDialog'
interface Props {
  tx: WorkspaceTransaction | null
  /** 当前用户对该交易是否有写权限(决定 edit 按钮是否启用) */
  canManage?: boolean
  /** ledger tags 列表 — 用于按 name 查 color 给 chip 上色,跟 TransactionRow 一致 */
  tags?: WorkspaceTag[]
  onClose: () => void
  onEdit: (tx: WorkspaceTransaction) => void
}

/**
 * 交易详情弹窗 — 只读视图 + Edit / Delete 入口。
 *
 * 跟 mobile 端的 TxDetailPage 对齐:不直接 inline 编辑表单(跟编辑弹窗
 * 重复),而是作为一个聚合页,展示完整字段 + 让用户按需进编辑。
 *
 * 信息层级:
 *   1) 头部:大金额 + 类型色 + 日期
 *   2) 主体:分类 / 账户 / 备注 / 标签 / 附件 / 创建者
 *   3) 底部:删除(左下,destructive)+ 关闭 / 编辑(右下)
 */
export function TransactionDetailDialog({
  tx,
  canManage = true,
  tags,
  onClose,
  onEdit,
}: Props) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  // tag name(lowercased)→ color 字典,跟 TransactionRow 用同样模式
  const tagColorByName = useMemo(() => buildTagColorMap(tags), [tags])

  const open = Boolean(tx)
  /** 退款只对支出有意义(后端校验原单必须是 expense)。 */
  const isExpense = tx?.tx_type === 'expense'
  /** 退款挂在 tx 所属账本上(详情可能跨账本打开),读写都以它为准。 */
  const txLedgerId = tx?.ledger_id || null

  // --- 退款(0022):挂在「这笔支出」上的退款汇总 ---------------------------- #
  // 拉账本退款列表后按 original_tx_id 筛出本笔的,展示「已退 / 原单」。
  // 登记走下面的 RefundCreateDialog,创建成功后 refresh 刷新这里。
  const [refunds, setRefunds] = useState<ReadRefund[]>([])
  const [refundCreateOpen, setRefundCreateOpen] = useState(false)
  /** 0028:退款就地弹窗 —— 点「查看退款 · N」直接看明细,不再跳退款管理页。 */
  const [refundDetailOpen, setRefundDetailOpen] = useState(false)

  const txId = tx?.id
  const loadRefunds = useCallback(async () => {
    if (!txId || !txLedgerId) {
      setRefunds([])
      return
    }
    try {
      const list = await fetchReadRefunds(token, txLedgerId)
      setRefunds(list.filter((r) => r.original_tx_id === txId))
    } catch {
      // 拉不到就当暂无退款,不阻断详情展示
      setRefunds([])
    }
  }, [token, txLedgerId, txId])

  useEffect(() => {
    if (!open || !isExpense) {
      setRefunds([])
      return
    }
    void loadRefunds()
  }, [open, isExpense, loadRefunds])

  const refundedSoFar = useMemo(
    () =>
      refunds
        .filter((r) => r.status !== 'cancelled')
        .reduce((sum, r) => sum + (r.amount || 0), 0),
    [refunds],
  )
  // 0022 退款去向分解(卡维度):退至其他卡的钱不回本卡余额,本卡支出仍成立
  // —— 与流水删除线(refunded_to_account)同一口径,避免不同维度结论相反。
  // 从退款记录现算:刚登记的退款这里立刻反映,不等交易行数据刷新。
  const refundSplit = useMemo(
    () => refundCardSplitFromList(refunds, tx?.account_id),
    [refunds, tx?.account_id],
  )
  // 退款列表还没拉回来 / 拉取失败时退化到交易行快照(refunded_to_account),
  // 避免「已全额退款」的支出先闪一下正常色、或拉不到退款就不置灰。
  const strikeSplit = useMemo(
    () =>
      refundSplit.total > 0
        ? refundSplit
        : refundCardSplit({
            refunded_total: tx?.refunded_total,
            refunded_to_account: tx?.refunded_to_account,
          }),
    [refundSplit, tx],
  )
  const originalAmount = tx ? Number(tx.amount) || 0 : 0
  const remainingRefundable = Math.max(originalAmount - refundedSoFar, 0)
  // 0028 全额退款(卡维度):退回本卡的累计 ≥ 原单金额 → 大金额置灰 + 删除线,
  // 与流水行同口径(跨卡退款本卡支出仍成立,不画线),tooltip 说明口径。
  const isFullyRefunded =
    isExpense && isFullyRefundedFromSplit(originalAmount, strikeSplit)
  const sign = tx?.tx_type === 'expense'
      ? '−'
      : tx?.tx_type === 'income'
        ? '+'
        : ''
  const tone = tx?.tx_type === 'expense'
      ? 'text-expense'
      : tx?.tx_type === 'income'
        ? 'text-income'
        : 'text-foreground'
  const typeLabel = tx ? t(`enum.txType.${tx.tx_type}`) : ''
  const accountText = tx
    ? tx.tx_type === 'transfer'
      ? `${tx.from_account_name || '-'} → ${tx.to_account_name || '-'}`
      : tx.account_name || '-'
    : '-'
  const attachments = Array.isArray(tx?.attachments) ? tx.attachments : []
  const tagsList =
    tx?.tags_list && tx.tags_list.length > 0
      ? tx.tags_list
      : (tx?.tags || '')
          .split(',')
          .map((s) => s.trim())
          .filter((s) => s.length > 0)

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[90dvh] max-w-md flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="text-sm font-medium text-muted-foreground">
            {t('detail.transaction.title')}
          </DialogTitle>
        </DialogHeader>

        {tx ? (
          <div className="min-h-0 overflow-y-auto">
            {/* 大金额 */}
            <div className="flex flex-col items-center gap-1 border-b border-border/60 bg-muted/20 px-6 py-6">
              <span className="text-[11px] uppercase tracking-widest text-muted-foreground">
                {typeLabel}
              </span>
              {/* 全额退款:置灰 + 删除线优先于支出红 / 收入绿,tooltip 说明
                  「已全额退回本卡」口径(跨卡退款不画)。 */}
              <span
                className={`text-4xl font-bold tabular-nums ${
                  isFullyRefunded ? 'text-muted-foreground/70' : tone
                }`}
                title={
                  isFullyRefunded ? t('transactions.refundedStrikeHint') : undefined
                }
              >
                {/* 删除线只画在数字上,不涂到旁边的币种标签 */}
                <span className={isFullyRefunded ? 'line-through decoration-2' : undefined}>
                  {sign}
                  {tx.amount.toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
                {tx.currency_code && tx.native_amount != null && tx.native_amount !== tx.amount ? (
                  <span className="ml-2 align-middle text-sm font-medium text-muted-foreground">
                    {tx.currency_code}
                  </span>
                ) : null}
              </span>
              {/* 交易级多币种:外币交易显示折账本本位币快照(记账时汇率) */}
              {tx.currency_code && tx.native_amount != null && tx.native_amount !== tx.amount ? (
                <span
                  className="text-sm tabular-nums text-muted-foreground"
                  title={t('transactions.convertedToBase')}
                >
                  ≈ {tx.native_amount.toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              ) : null}
              <span className="text-xs text-muted-foreground">
                <Calendar className="mr-1 inline h-3 w-3" />
                {formatDateTime(tx.happened_at)}
              </span>
            </div>

            {/* 字段列表 */}
            <div className="flex flex-col divide-y divide-border/40 px-6">
              <DetailRow
                icon={<Hash className="h-4 w-4" />}
                label={t('detail.transaction.category')}
                value={tx.category_name || '—'}
              />
              <DetailRow
                icon={<Wallet className="h-4 w-4" />}
                label={
                  tx.tx_type === 'transfer'
                    ? t('detail.transaction.transferRoute')
                    : t('detail.transaction.account')
                }
                value={accountText}
              />
              {/* §0021 扩展字段:优惠 / 手续费 / 多账户分摊 — 账单完整信息 */}
              {tx.tx_type === 'expense' && tx.discount ? (
                <DetailRow
                  icon={<BadgePercent className="h-4 w-4" />}
                  label={t('detail.transaction.discount')}
                  value={<DiscountValue discount={tx.discount} />}
                />
              ) : null}
              {tx.tx_type === 'transfer' && tx.transfer_fee ? (
                <DetailRow
                  icon={tx.transfer_fee.mode === 'discount' ? <Gift className="h-4 w-4" /> : <Receipt className="h-4 w-4" />}
                  label={
                    tx.transfer_fee.mode === 'discount'
                      ? t('detail.transaction.transferDiscount')
                      : t('detail.transaction.transferFee')
                  }
                  value={<TransferFeeValue fee={tx.transfer_fee} />}
                />
              ) : null}
              {tx.tx_type === 'expense' && tx.payment_legs && tx.payment_legs.length > 0 ? (
                <DetailRow
                  icon={<Layers className="h-4 w-4" />}
                  label={t('detail.transaction.paymentLegs')}
                  value={<PaymentLegsValue legs={tx.payment_legs} />}
                />
              ) : null}
              {/* 0024 成员分摊:仅支出。按人拆账,不动账户余额。 */}
              {tx.tx_type === 'expense' && tx.member_shares && tx.member_shares.length > 0 ? (
                <DetailRow
                  icon={<Users className="h-4 w-4" />}
                  label={t('detail.transaction.memberShares')}
                  value={<MemberSharesValue shares={tx.member_shares} />}
                />
              ) : null}
              {/* 0029 归物:这笔支出挂在哪个物品上(金额计入该物品累计成本)。 */}
              {tx.item_id ? (
                <DetailRow
                  icon={<Package className="h-4 w-4" />}
                  label={t('items.field.linkedItem')}
                  value={tx.item_name || tx.item_id}
                />
              ) : null}
              {tx.note ? (
                <DetailRow
                  icon={<Edit3 className="h-4 w-4" />}
                  label={t('detail.transaction.note')}
                  value={tx.note}
                />
              ) : null}
              {tagsList.length > 0 ? (
                <DetailRow
                  icon={<Tag className="h-4 w-4" />}
                  label={t('detail.transaction.tags')}
                  value={
                    <div className="flex flex-wrap justify-end gap-1">
                      {tagsList.map((name) => (
                        <TagChip
                          key={name}
                          name={name}
                          color={tagColorByName.get(name.trim().toLowerCase())}
                        />
                      ))}
                    </div>
                  }
                />
              ) : null}
              {attachments.length > 0 ? (
                <AttachmentRow attachments={attachments} />
              ) : null}
              {/* 0022 退款汇总:仅支出交易。点底部「登记退款」给这笔支出记一笔;
                  已有退款时,汇总旁的「查看退款 · N」就地弹出明细(不跳页面)。 */}
              {isExpense ? (
                <div className="flex items-center justify-between gap-3 py-3">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="text-muted-foreground/70">
                      <RotateCcw className="h-4 w-4" />
                    </span>
                    <span>{t('refunds.detail.sectionLabel')}</span>
                  </div>
                  <div className="text-right text-sm">
                    {refunds.length === 0 ? (
                      <span className="text-muted-foreground">
                        {t('refunds.detail.noRefund')}
                      </span>
                    ) : (
                      <div className="flex flex-col items-end gap-1">
                        <span className="tabular-nums text-income">
                          {t('refunds.detail.summary')
                            .replace('{refunded}', refundedSoFar.toFixed(2))
                            .replace('{original}', originalAmount.toFixed(2))}
                        </span>
                        {/* 卡维度分解:有退款退到其他卡时,本卡支出仍成立
                            (余额也没回来),明示去向避免与流水删除线矛盾。 */}
                        {refundSplit.toOtherCards > 0.011 ? (
                          <span className="text-[11px] text-muted-foreground">
                            {refundSplit.backToCard > 0.011
                              ? t('refunds.detail.backSplit')
                                  .replace('{back}', refundSplit.backToCard.toFixed(2))
                                  .replace('{other}', refundSplit.toOtherCards.toFixed(2))
                              : t('refunds.detail.expenseStands').replace(
                                  '{other}',
                                  refundSplit.toOtherCards.toFixed(2),
                                )}
                          </span>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setRefundDetailOpen(true)}
                          className="inline-flex items-center gap-0.5 rounded text-[11px] font-medium text-primary transition-colors hover:text-primary/80 hover:underline"
                        >
                          {t('refunds.detail.viewRefunds').replace(
                            '{count}',
                            String(refunds.length),
                          )}
                          <ChevronRight className="h-3 w-3" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ) : null}
              {tx.created_by_email ? (
                <DetailRow
                  icon={<User className="h-4 w-4" />}
                  label={t('detail.transaction.createdBy')}
                  value={
                    <UserBadge
                      displayName={tx.created_by_display_name}
                      email={tx.created_by_email}
                      avatarUrl={tx.created_by_avatar_url}
                    />
                  }
                />
              ) : null}
              {/* §7 共享账本:tx 被他人编辑过(last_edited_by_user_id 不等于
                  creator)就额外显示一行。同一人创建并编辑则不冗余显示。 */}
              {tx.last_edited_by_email &&
              tx.last_edited_by_user_id &&
              tx.last_edited_by_user_id !== tx.created_by_user_id ? (
                <DetailRow
                  icon={<Edit3 className="h-4 w-4" />}
                  label={t('detail.transaction.lastEditedBy')}
                  value={
                    <UserBadge
                      displayName={tx.last_edited_by_display_name}
                      email={tx.last_edited_by_email}
                      avatarUrl={tx.last_edited_by_avatar_url}
                    />
                  }
                />
              ) : null}
            </div>
          </div>
        ) : null}

        <DialogFooter className="flex flex-row items-center justify-between gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
          {/* 0022 退款入口:仅支出可退,放左下;关闭 / 编辑在右下。 */}
          {isExpense ? (
            <Button
              size="sm"
              variant="outline"
              disabled={!canManage || !txLedgerId}
              onClick={() => setRefundCreateOpen(true)}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" />
              {t('refunds.detail.add')}
            </Button>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              {t('dialog.cancel')}
            </Button>
            <Button
              size="sm"
              disabled={!canManage}
              onClick={() => tx && onEdit(tx)}
            >
              <Edit3 className="mr-1 h-3.5 w-3.5" />
              {t('common.edit')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>

      {/* 登记退款弹窗(嵌套 Dialog):原单固定为当前这笔支出 */}
      <RefundCreateDialog
        open={refundCreateOpen}
        tx={tx}
        token={token}
        ledgerId={txLedgerId}
        retryOnConflict={retryOnConflict}
        isWriteConflict={isWriteConflict}
        onOpenChange={setRefundCreateOpen}
        onCreated={() => void loadRefunds()}
      />

      {/* 退款明细弹窗(嵌套 Dialog):读已挂在本单上的退款,不做编辑。 */}
      <RefundDetailDialog
        open={refundDetailOpen}
        refunds={refunds}
        originalAmount={originalAmount}
        currency={tx?.currency_code || ''}
        canManage={canManage}
        onAdd={canManage ? () => setRefundCreateOpen(true) : undefined}
        onOpenChange={setRefundDetailOpen}
      />
    </Dialog>
  )
}

/**
 * 共享账本 tx 创建人 / 最后编辑人 — 头像 + 名字,hover 显示邮箱。
 * 头像 fallback 到首字母色块,跟 SharedLedgerStatsDialog 风格一致。
 */
function UserBadge({
  displayName,
  email,
  avatarUrl,
}: {
  displayName: string | null | undefined
  email: string | null | undefined
  avatarUrl: string | null | undefined
}) {
  const name = displayName || email?.split('@')[0] || ''
  const resolved = resolveApiUrl(avatarUrl)
  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={email || undefined}
    >
      {resolved ? (
        <img
          src={resolved}
          alt={name}
          className="h-5 w-5 rounded-full object-cover"
        />
      ) : (
        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-[10px] font-semibold text-primary">
          {(name[0] || '?').toUpperCase()}
        </span>
      )}
      <span className="text-sm">{name}</span>
    </span>
  )
}

function DetailRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="text-muted-foreground/70">{icon}</span>
        <span>{label}</span>
      </div>
      <div className="max-w-[60%] text-right text-sm text-foreground">
        {typeof value === 'string' ? <span className="break-all">{value}</span> : value}
      </div>
    </div>
  )
}

/** §0021 优惠(仅 expense):减免额 / 折扣率 + 原价→实付。 */
function DiscountValue({ discount }: { discount: TxDiscount }) {
  const t = useT()
  const fmt = (n: number) =>
    n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  let primary: string
  if (discount.mode === 'amount') {
    primary = `−${fmt(discount.discountAmount ?? discount.value ?? 0)}`
  } else {
    const ratePct = Math.round((1 - (discount.value ?? 1)) * 100)
    primary = `${ratePct}% · ${t('detail.transaction.discountRate')}`
  }
  return (
    <div className="space-y-0.5 text-right">
      <div className="text-sm tabular-nums text-income">{primary}</div>
      {discount.originalAmount != null && discount.finalAmount != null ? (
        <div className="text-[11px] text-muted-foreground tabular-nums">
          {fmt(discount.originalAmount)} → {fmt(discount.finalAmount)}
        </div>
      ) : null}
      {discount.couponName ? (
        <div className="text-[11px] text-muted-foreground">{discount.couponName}</div>
      ) : null}
    </div>
  )
}

/** §0021 转账手续费 / §0026 转账优惠(仅 transfer):金额 + 扣费/优惠方式。 */
function TransferFeeValue({ fee }: { fee: TxTransferFee }) {
  const t = useT()
  const isDiscount = fee.mode === 'discount'
  const modeLabel = isDiscount
    ? t('detail.transaction.transferDiscountMode')
    : fee.mode === 'deduct_from_source'
      ? t('detail.transaction.transferFeeFromSource')
      : fee.mode === 'deduct_from_target'
        ? t('detail.transaction.transferFeeFromTarget')
        : t('detail.transaction.transferFeeSeparate')
  const fmtAmount = (n: number) =>
    n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (
    <div className="space-y-0.5 text-right">
      <div className="text-sm tabular-nums">
        {isDiscount ? '-' : '+'}
        {fmtAmount(fee.amount)}
      </div>
      <div className="text-[11px] text-muted-foreground">
        {modeLabel}
        {fee.mode === 'separate_account' && fee.feeAccountName
          ? ` · ${fee.feeAccountName}`
          : ''}
      </div>
    </div>
  )
}

/** §0021 多账户分摊(仅 expense):每条腿 账户 → 金额。 */
function PaymentLegsValue({ legs }: { legs: TxPaymentLeg[] }) {
  const fmt = (n: number) =>
    n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (
    <div className="space-y-1 text-right">
      {legs.map((leg, i) => (
        <div key={`${leg.accountId}-${i}`} className="flex items-center justify-end gap-2 text-sm">
          <span className="text-muted-foreground">{leg.accountName || leg.accountId}</span>
          <span className="tabular-nums">{fmt(leg.amount)}</span>
        </div>
      ))}
    </div>
  )
}

/** §0024 成员分摊(仅 expense):每条份额 成员 → 金额(带占比)。 */
function MemberSharesValue({ shares }: { shares: TxMemberShare[] }) {
  const fmt = (n: number) =>
    n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const billTotal = shares.reduce((sum, s) => sum + (Number(s.amount) || 0), 0)
  return (
    <div className="space-y-1 text-right">
      {shares.map((share, i) => {
        const pct =
          billTotal > 0 ? Math.round(((Number(share.amount) || 0) / billTotal) * 1000) / 10 : 0
        return (
          <div
            key={`${share.memberId}-${i}`}
            className="flex items-center justify-end gap-2 text-sm"
          >
            <span className="text-muted-foreground">
              {share.memberName || share.memberId}
            </span>
            <span className="tabular-nums">{fmt(Number(share.amount) || 0)}</span>
            <span className="w-12 text-right text-[11px] tabular-nums text-muted-foreground">
              {pct ? `${pct}%` : ''}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd} ${hh}:${mi}`
}

/**
 * 附件展示行 — 缩略图 grid + 点开切换到大图覆盖层。
 *
 * 复用全局 AttachmentCache(同 fileId 进程内只下载一次,blob URL 缓存),所以
 * 在交易列表 / 详情弹窗 / 分类详情等多处场景都不会重复下载。
 *
 * cloudFileId 缺失的附件(还在 mobile 端没上传完)显示占位灰块 + 文件名,
 * 不抢眼但能看出来。
 */
function AttachmentRow({ attachments }: { attachments: AttachmentRef[] }) {
  const t = useT()
  const preview = useAttachmentPreview()
  const { previewMap, ensureLoadedMany } = useAttachmentCache()
  const ids = useMemo(() => attachments.map(ref => ref.cloudFileId || '').filter(Boolean), [attachments])
  useEffect(() => { ensureLoadedMany(ids) }, [ids, ensureLoadedMany])
  return <div className="space-y-2 py-3"><div className="text-xs text-muted-foreground">{t('detail.transaction.attachmentsCount', { count: attachments.length })}</div><div className="grid grid-cols-3 gap-2 sm:grid-cols-4">{attachments.map((ref, index) => <button type="button" key={`${ref.cloudFileId || ref.fileName}-${index}`} className="aspect-square overflow-hidden rounded-md border border-border/60 bg-muted/40 hover:border-primary" aria-label={`${t('transactions.attachment.preview')} ${ref.originalName || ref.fileName}`} onClick={() => void preview(attachments, index)}>{previewMap[ref.cloudFileId || ''] ? <img src={previewMap[ref.cloudFileId || '']} alt={ref.originalName || ref.fileName} className="h-full w-full object-cover" /> : <ImageOff className="mx-auto h-5 w-5 text-muted-foreground" />}</button>)}</div></div>
}

/** ISO(UTC) → datetime-local 输入框需要的本地 'YYYY-MM-DDTHH:mm'(与 RefundsPanel 一致)。 */
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

/**
 * 交易详情内的「登记退款」弹窗(0022)。
 *
 * 复用退款后端(POST /write/ledgers/{id}/refunds)与原单校验规则:
 *  - 原单固定为当前这笔支出,所以省掉 RefundsPanel 里的原单选择器
 *  - 金额默认预填「剩余可退」,用户可改成部分退;同单可退多次
 *  - 累计不超原单由服务端最终校验,这里 validateRefundInput 只做即时提示
 */
function RefundCreateDialog({
  open,
  tx,
  token,
  ledgerId,
  retryOnConflict,
  isWriteConflict,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  tx: WorkspaceTransaction | null
  token: string
  ledgerId: string | null
  retryOnConflict: <T,>(
    ledgerId: string,
    submit: (baseChangeId: number) => Promise<T>,
  ) => Promise<T>
  isWriteConflict: (err: unknown) => boolean
  onOpenChange: (next: boolean) => void
  onCreated: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [form, setForm] = useState<RefundForm>(refundDefaults())
  const [accounts, setAccounts] = useState<WorkspaceAccount[]>([])
  const [alreadyRefunded, setAlreadyRefunded] = useState(0)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  /** 用户手动改过金额后,不再用「剩余可退」覆盖它。 */
  const amountTouched = useRef(false)

  /** 拉账户下拉 + 本笔已退金额,返回已退金额(给金额预填用)。 */
  const loadData = useCallback(
    async (target: WorkspaceTransaction): Promise<number> => {
      setLoading(true)
      try {
        const accts = await fetchWorkspaceAccounts(token, {})
        setAccounts(accts)
        if (!ledgerId) return 0
        const list = await fetchReadRefunds(token, ledgerId)
        return list
          .filter((r) => r.original_tx_id === target.id && r.status !== 'cancelled')
          .reduce((sum, r) => sum + (r.amount || 0), 0)
      } catch {
        // 拉不到就按 0 处理,服务端仍是权威
        return 0
      } finally {
        setLoading(false)
      }
    },
    [token, ledgerId],
  )

  // 打开时重置并预填:原单 = 当前 tx,默认退回原支出账户,金额先按原单填。
  useEffect(() => {
    if (!open || !tx) return
    amountTouched.current = false
    setSubmitting(false)
    setAccounts([])
    setAlreadyRefunded(0)
    const original = Number(tx.amount) || 0
    // 原单用合并付款(两账户分摊)时,默认「分别退款」并按原分摊等比预填腿。
    const initialLegs =
      tx.payment_legs && tx.payment_legs.length
        ? refundLegsFromPaymentLegs(tx.payment_legs, original)
        : []
    const useSplit = initialLegs.length > 0
    setForm({
      ...refundDefaults(),
      original_tx_id: tx.id,
      original_tx_label: refundTxLabel(tx),
      original_amount: String(original),
      currency: tx.currency_code || '',
      account_id: tx.account_id || '',
      account_name: tx.account_name || '',
      refund_mode: useSplit ? 'split' : 'unified',
      refund_legs: useSplit ? initialLegs : [],
      amount: original > 0 ? String(original) : '',
    })
    void loadData(tx).then((refunded) => {
      setAlreadyRefunded(refunded)
      if (amountTouched.current) return
      const remaining = Math.max((Number(tx.amount) || 0) - refunded, 0)
      setForm((prev) => {
        // 若已默认 split,按「剩余可退」等比缩放各腿,保持 sum(legs) == 退款金额。
        const legs =
          prev.refund_mode === 'split' && tx.payment_legs && tx.payment_legs.length
            ? refundLegsFromPaymentLegs(tx.payment_legs, remaining)
            : prev.refund_legs
        return {
          ...prev,
          amount: remaining > 0 ? String(remaining) : '',
          refund_legs: legs,
        }
      })
    })
  }, [open, tx, loadData])

  const originalAmount = tx ? Number(tx.amount) || 0 : 0
  const remaining = Math.max(originalAmount - alreadyRefunded, 0)
  const formError = validateRefundInput({
    originalTxId: form.original_tx_id,
    amount: Number(form.amount),
    remainingRefundable: remaining,
    refundMode: form.refund_mode,
    refundLegs: form.refund_legs,
  })

  const accountOptions = useMemo(
    () =>
      accounts
        .filter((a) => (a.name || '').trim())
        .map((a) => ({ id: a.id, name: (a.name || '').trim() })),
    [accounts],
  )

  const accountIdByName = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.name.toLowerCase(), a.id)
    return map
  }, [accountOptions])

  const handleSubmit = async () => {
    if (!tx || !ledgerId || formError) return
    setSubmitting(true)
    try {
      const refund_mode = form.refund_mode || 'unified'
      const refund_legs =
        refund_mode === 'split' ? refundLegsToWire(form.refund_legs, accountIdByName) : null
      const payload: RefundCreatePayload = {
        original_tx_id: form.original_tx_id,
        amount: Number(form.amount),
        currency: form.currency.trim() || null,
        account_id: form.account_id || null,
        account_name: form.account_name || null,
        refund_mode,
        refund_legs,
        happened_at: form.happened_at,
        reason: form.reason.trim() || null,
        status: form.status,
        note: form.note.trim() || null,
      }
      await retryOnConflict(ledgerId, (base) => createRefund(token, ledgerId, base, payload))
      toast.success(t('refunds.notice.created'), t('notice.success'))
      onCreated()
      onOpenChange(false)
    } catch (err) {
      // 冲突:server 状态已变,重拉一次已退金额再让用户重试
      if (isWriteConflict(err)) await loadData(tx)
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSubmitting(false)
    }
  }

  const errorText = formError ? t(`refunds.error.${formError}`) : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('refunds.detail.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">{t('refunds.detail.hint')}</p>

          {/* 原单固定为当前这笔支出,只读展示 */}
          <div className="space-y-1">
            <Label>{t('refunds.field.originalTx')}</Label>
            <div className="flex h-10 w-full items-center rounded-md border border-input bg-muted px-3 py-2 text-sm">
              <span className="truncate">{form.original_tx_label}</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              {remaining > 0
                ? t('refunds.detail.refundable').replace('{remaining}', remaining.toFixed(2))
                : t('refunds.detail.fullyRefunded')}
            </p>
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
                onChange={(e) => {
                  amountTouched.current = true
                  setForm({ ...form, amount: e.target.value })
                }}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('refunds.field.happenedAt')}</Label>
              <Input
                type="datetime-local"
                value={isoToDatetimeLocal(form.happened_at)}
                onChange={(e) =>
                  setForm({ ...form, happened_at: datetimeLocalToIso(e.target.value, form.happened_at) })
                }
              />
            </div>
          </div>

          {/* 退款去向:统一退至一张卡 / 分别退至多张卡(0025) */}
          <RefundDestinationFields
            form={form}
            onFormChange={setForm}
            accountOptions={accountOptions}
            originalPaymentLegs={tx?.payment_legs ?? null}
          />

          <div className="space-y-1">
            <Label>{t('refunds.field.status')}</Label>
            <Select
              value={form.status}
              onValueChange={(value) =>
                setForm({ ...form, status: value as RefundForm['status'] })
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
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
            />
          </div>

          <div className="space-y-1">
            <Label>{t('refunds.field.note')}</Label>
            <Textarea
              rows={2}
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
            />
          </div>

          {errorText ? <p className="text-xs text-destructive">{errorText}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={submitting} onClick={() => onOpenChange(false)}>
            {t('dialog.cancel')}
          </Button>
          <Button
            disabled={submitting || formError !== null}
            onClick={() => void handleSubmit()}
          >
            {t('refunds.button.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
