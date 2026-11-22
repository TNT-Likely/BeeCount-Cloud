import { useEffect, useMemo, useState } from 'react'
import { resolveApiUrl, type AttachmentRef, type FlowLink, type WorkspaceTag, type WorkspaceTransaction } from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import { buildTagColorMap, TagChip } from '@beecount/web-features'
import { ArrowLeftRight, Calendar, ChevronLeft, ChevronRight, Copy, Edit3, Hash, ImageOff, Layers, Link2, RotateCcw, Tag, Undo2, User, Users, Wallet, X } from 'lucide-react'

import { useAttachmentPreview } from '../../context/AttachmentPreviewContext'
import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useLock } from '../../context/LockContext'

const formatMoney = (value: number) =>
  value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })

interface Props {
  tx: WorkspaceTransaction | null
  /** 当前用户对该交易是否有写权限(决定 edit 按钮是否启用) */
  canManage?: boolean
  /** ledger tags 列表 — 用于按 name 查 color 给 chip 上色,跟 TransactionRow 一致 */
  tags?: WorkspaceTag[]
  onClose: () => void
  onEdit: (tx: WorkspaceTransaction) => void
  /** 复制账单：把原账单字段复制为新账单，仅时间改为当前。 */
  onCopy?: (tx: WorkspaceTransaction) => void
  /** 退款入口(0025):仅原支出(expense)可退,且仍有可退余额时回调。 */
  onRefund?: (tx: WorkspaceTransaction) => void
  /** 取消退款:仅退款流水(refund)可用,回调由外层二次确认并删除该退款流水。 */
  onCancelRefund?: (tx: WorkspaceTransaction) => void
  /** 退款(0025):关联的原消费流水(由外层按 refund_of_sync_id 拉取后传入)。 */
  originalTx?: WorkspaceTransaction | null
  /** 退款(0025):点击「查看原单」时回调,外层跳转到原消费流水详情。 */
  onOpenOriginal?: (syncId: string, ledgerId: string) => void
  /** 合并还款(0039):被合并的各笔原始借款流水(外层按 merged_from_sync_ids 拉取后传入)。 */
  mergedOriginals?: Record<string, WorkspaceTransaction>
  /** 关联记录(0043):引用这笔流水的业务实体(借款/储蓄罐/物品/报销/定期存款)。 */
  flowLinks?: FlowLink[]
  /** 点击关联记录时回调,外层跳转到对应业务详情。 */
  onOpenFlowLink?: (link: FlowLink) => void
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
  onCopy,
  onRefund,
  onCancelRefund,
  originalTx,
  onOpenOriginal,
  mergedOriginals = {},
  flowLinks = [],
  onOpenFlowLink,
}: Props) {
  const t = useT()
  const { isTxLocked } = useLock()

  const isRefund = tx?.tx_type === 'refund'
  // 合并还款(0039):被合并的原始借款流水 sync_id 列表。
  const mergedFromIds = tx?.merged_from_sync_ids ?? []

  // 借款流水(0027 loan-engine 生成):分类名 + 排除统计标记 → 不可退款
  // (借出/偿还借款是 expense,但没有真实消费可退;金额应去借款页修改)。
  const isLoanFlow =
    Boolean(tx?.exclude_from_stats) &&
    typeof tx?.category_name === 'string' &&
    ['借出', '借入', '收回借款', '偿还借款'].includes(tx.category_name)

  // 退款入口可用性:仅支出(expense)且仍有可退余额(已退 < 原金额)时允许;
  // 借款流水不提供退款入口(请到借款页还款/调整)。
  const refundedAmount =
    tx && typeof tx.refunded_amount === 'number' ? tx.refunded_amount : 0
  const isExpense = tx?.tx_type === 'expense'
  const hasRefundable = isExpense && !isLoanFlow && refundedAmount < (tx?.amount ?? 0)
  // 退款(0025):原支出被全额 / 部分退款 → 印章区分(跟流水行一致)。
  const refundStatus: 'none' | 'partial' | 'full' =
    isExpense && refundedAmount > 0
      ? refundedAmount >= (tx?.amount ?? 0) - 0.005
        ? 'full'
        : 'partial'
      : 'none'
  // 部分退款:大金额显示未退款金额(原金额 − 已退),原金额灰色删除线小字,
  // 跟列表行口径一致;全额退款维持原金额删除线的作废观感。
  const isPartialRefund = refundStatus === 'partial'
  const refundRemaining = isPartialRefund
    ? Math.max(0, Math.round(((tx?.amount ?? 0) - refundedAmount) * 100) / 100)
    : 0

  // tag name(lowercased)→ color 字典,跟 TransactionRow 用同样模式
  const tagColorByName = useMemo(() => buildTagColorMap(tags), [tags])

  const open = Boolean(tx)
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
  // 支出优惠:大金额是优惠后实付金额,优惠前原价用灰色删除线展示。
  const hasExpenseDiscount =
    tx?.tx_type === 'expense' && (tx.discount_amount ?? 0) > 0
  const originalAmount = (tx?.amount ?? 0) + (hasExpenseDiscount ? (tx?.discount_amount ?? 0) : 0)
  // 该笔流水之后的账户余额(读接口现算)
  const balanceAfter = tx?.account_balance_after
  const counterBalanceAfter = tx?.counter_account_balance_after
  const hasBalance = typeof balanceAfter === 'number' && Number.isFinite(balanceAfter)
  const hasCounterBalance =
    typeof counterBalanceAfter === 'number' && Number.isFinite(counterBalanceAfter)
  const attachments = Array.isArray(tx?.attachments) ? tx.attachments : []
  const tagsList =
    tx?.tags_list && tx.tags_list.length > 0
      ? tx.tags_list
      : (tx?.tags || '')
          .split(',')
          .map((s) => s.trim())
          .filter((s) => s.length > 0)

  const effectiveCanManage = canManage && !isTxLocked(tx?.happened_at)

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
              <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-widest text-muted-foreground">
                {tx.tx_type === 'transfer' ? (
                  <ArrowLeftRight className="h-3.5 w-3.5 text-sky-700 dark:text-sky-300" />
                ) : null}
                {typeLabel}
                {isRefund ? (
                  <span
                    className="inline-flex items-center rounded bg-purple-500/15 px-1.5 py-0.5 text-[10px] font-medium leading-none text-purple-700 dark:text-purple-300"
                    title={t('refund.detail.title') as string}
                  >
                    {t('refund.badge')}
                  </span>
                ) : null}
                {/* 转账优惠印章:仅优惠(无手续费)时显示「含优惠」章,与退款印章同级 */}
                {tx.tx_type === 'transfer' &&
                  (tx.transfer_discount_amount ?? 0) > 0 &&
                  !((tx.transfer_fee_amount ?? 0) > 0) ? (
                  <span
                    className="inline-flex items-center rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium leading-none text-emerald-700 dark:text-emerald-300"
                    title={t('transactions.transferFee.discountBadge') as string}
                  >
                    {t('transactions.transferFee.discountBadge')}
                  </span>
                  ) : null}
                {/* 退款印章(0025):全额退款 = 红章、部分退款 = 琥珀章,跟流水行一致 */}
                {refundStatus !== 'none' ? (
                  <span
                    className={[
                      'inline-flex items-center rounded border-2 px-1.5 py-0.5',
                      'text-[10px] font-bold leading-none -rotate-6 select-none',
                      'ring-2 ring-inset tracking-wide',
                      refundStatus === 'full'
                        ? 'border-red-500 text-red-600 ring-red-500/40 dark:text-red-400 dark:border-red-400 dark:ring-red-400/40'
                        : 'border-amber-500 text-amber-600 ring-amber-500/40 dark:text-amber-400 dark:border-amber-400 dark:ring-amber-400/40'
                    ].join(' ')}
                    title={
                      refundStatus === 'full'
                        ? t('refund.stamp.fullTitle', { amount: formatMoney(tx.amount) })
                        : t('refund.stamp.partialTitle', {
                            refunded: formatMoney(refundedAmount),
                            total: formatMoney(tx.amount)
                          })
                    }
                  >
                    {refundStatus === 'full' ? t('refund.stamp.full') : t('refund.stamp.partial')}
                  </span>
                ) : null}
              </span>
              {/* 支出优惠:优惠前原价灰色删除线,下面大金额即优惠后金额 */}
              {hasExpenseDiscount ? (
                <span className="font-mono text-base font-normal tabular-nums text-muted-foreground/70 line-through decoration-muted-foreground/70">
                  {formatMoney(originalAmount)}
                </span>
              ) : null}
              {/* 部分退款:原金额灰色删除线小字,大金额显示未退款金额 */}
              {isPartialRefund ? (
                <span
                  className="font-mono text-base font-normal tabular-nums text-muted-foreground/70 line-through decoration-muted-foreground/70"
                  title={
                    t('refund.stamp.partialTitle', {
                      refunded: formatMoney(refundedAmount),
                      total: formatMoney(tx.amount)
                    }) as string
                  }
                >
                  {formatMoney(tx.amount)}
                </span>
              ) : null}
              <span
                className={`text-4xl font-bold tabular-nums ${refundStatus === 'full' ? 'text-muted-foreground line-through' : tone}`}
                title={isPartialRefund ? (t('refund.remainingAmount') as string) : undefined}
              >
                {sign}
                {(isPartialRefund ? refundRemaining : tx.amount).toLocaleString('zh-CN', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
                {tx.currency_code && tx.native_amount != null && tx.native_amount !== tx.amount ? (
                  <span className="ml-2 align-middle text-sm font-medium text-muted-foreground">
                    {tx.currency_code}
                  </span>
                ) : null}
              </span>
              {isPartialRefund ? (
                <span className="text-[11px] text-muted-foreground">
                  {t('refund.remainingAmount')}
                </span>
              ) : null}
              {hasExpenseDiscount ? (
                <span className="text-[11px] text-muted-foreground">
                  {t('transactions.expenseDiscount.afterAmount')}
                </span>
              ) : null}
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
              {/* 退款(0025):展示关联的原消费流水,点击直接跳转其详情 */}
              {isRefund && tx.refund_of_sync_id ? (
                <DetailRow
                  icon={<Undo2 className="h-4 w-4" />}
                  label={t('refund.original')}
                  value={
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded border border-purple-300 bg-purple-500/10 px-2 py-0.5 text-xs font-medium text-purple-700 transition-colors hover:bg-purple-500/20 dark:text-purple-300"
                      onClick={() =>
                        onOpenOriginal?.(
                          tx.refund_of_sync_id as string,
                          tx.ledger_id || '',
                        )
                      }
                    >
                      {originalTx
                        ? `${originalTx.note || originalTx.category_name || t('common.none')} · ${formatMoney(originalTx.amount)}`
                        : t('refund.viewOriginal')}
                      <span aria-hidden>→</span>
                    </button>
                  }
                />
              ) : null}
              {/* 合并还款(0039):一笔实际收付款合并结清多笔借款,这里列出被合并的
                  各笔原始借款单,点击可跳转查看原单。 */}
              {mergedFromIds.length > 0 ? (
                <DetailRow
                  icon={<Layers className="h-4 w-4" />}
                  label={t('loans.merged.originalLabel')}
                  value={
                    <div className="flex flex-col items-end gap-1">
                      {mergedFromIds.map((sid) => {
                        const src = mergedOriginals[sid]
                        return (
                          <button
                            key={sid}
                            type="button"
                            className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-500/20 dark:text-amber-300"
                            onClick={() => onOpenOriginal?.(sid, tx.ledger_id || '')}
                          >
                            {src
                              ? `${src.note || src.category_name || t('common.none')} · ${formatMoney(src.amount)}`
                              : t('loans.merged.viewOriginal')}
                            <span aria-hidden>→</span>
                          </button>
                        )
                      })}
                    </div>
                  }
                />
              ) : null}
              {/* 关联记录(0043):所有引用这笔流水的业务实体(借款/储蓄罐/物品/
                  报销/定期存款),点击跳转到对应详情,实现流水之间互查。 */}
              {flowLinks.length > 0 ? (
                <DetailRow
                  icon={<Link2 className="h-4 w-4" />}
                  label={t('detail.transaction.relatedRecords')}
                  value={
                    <div className="flex flex-col items-end gap-1">
                      {flowLinks.map((link, idx) => (
                        <button
                          key={`${link.kind}-${idx}`}
                          type="button"
                          className="inline-flex max-w-full items-center gap-1 rounded border border-sky-300 bg-sky-500/10 px-2 py-0.5 text-xs font-medium text-sky-700 transition-colors hover:bg-sky-500/20 dark:text-sky-300"
                          onClick={() => onOpenFlowLink?.(link)}
                        >
                          <span className="truncate">{link.title}</span>
                          {link.subtitle ? (
                            <span className="shrink-0 text-[10px] font-normal text-muted-foreground">
                              {link.subtitle}
                            </span>
                          ) : null}
                          <span aria-hidden>→</span>
                        </button>
                      ))}
                    </div>
                  }
                />
              ) : null}
              <DetailRow
                icon={<Wallet className="h-4 w-4" />}
                label={
                  tx.tx_type === 'transfer'
                    ? t('detail.transaction.transferRoute')
                    : t('detail.transaction.account')
                }
                value={accountText}
              />
              {/* 该笔流水之后的账户余额;转账展示转出 / 转入两张卡 */}
              {hasBalance ? (
                <DetailRow
                  icon={<Wallet className="h-4 w-4" />}
                  label={
                    tx.tx_type === 'transfer'
                      ? t('transactions.balance.transferLabel')
                      : t('transactions.balance.label')
                  }
                  value={
                    <span className="font-mono tabular-nums">
                      {tx.tx_type === 'transfer' && hasCounterBalance
                        ? `${formatMoney(balanceAfter as number)} → ${formatMoney(counterBalanceAfter as number)}`
                        : formatMoney(balanceAfter as number)}
                    </span>
                  }
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
              {tx.tx_type === 'transfer' && ((tx.transfer_fee_amount ?? 0) > 0 || (tx.transfer_discount_amount ?? 0) > 0) ? (
                <>
                  {(tx.transfer_fee_amount ?? 0) > 0 ? (
                    <DetailRow
                      icon={<Wallet className="h-4 w-4" />}
                      label={t('transactions.transferFee.feeAmount')}
                      value={tx.transfer_fee_amount?.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    />
                  ) : null}
                  {(tx.transfer_discount_amount ?? 0) > 0 ? (
                    <DetailRow
                      icon={<Wallet className="h-4 w-4" />}
                      label={t('transactions.transferFee.discountAmount')}
                      value={tx.transfer_discount_amount?.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    />
                  ) : null}
                  {(tx.transfer_fee_amount ?? 0) > 0 ? (
                    <DetailRow
                      icon={<Wallet className="h-4 w-4" />}
                      label={t('transactions.transferFee.feeAccount')}
                      value={tx.transfer_fee_account_name || t('transactions.transferFee.feeAccountFrom')}
                    />
                  ) : null}
                </>
              ) : null}
              {/* 支出优惠(0023):展示优惠金额与优惠前金额,amount 仍为实际入账金额 */}
              {tx.tx_type === 'expense' && (tx.discount_amount ?? 0) > 0 ? (
                <>
                  <DetailRow
                    icon={<Wallet className="h-4 w-4" />}
                    label={t('transactions.expenseDiscount.amount')}
                    value={tx.discount_amount?.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  />
                  <DetailRow
                    icon={<Wallet className="h-4 w-4" />}
                    label={t('transactions.expenseDiscount.originalAmount')}
                    value={
                      <span className="font-mono tabular-nums text-muted-foreground/70 line-through decoration-muted-foreground/70">
                        {formatMoney(tx.amount + (tx.discount_amount ?? 0))}
                      </span>
                    }
                  />
                </>
              ) : null}
              {/* 成员归属 / 分摊明细(0024) */}
              {tx.member_splits && tx.member_splits.length > 0 ? (
                <DetailRow
                  icon={<Users className="h-4 w-4" />}
                  label={
                    tx.member_splits.length > 1
                      ? t('members.splitBadge')
                      : t('members.singleBadge')
                  }
                  value={
                    <span className="flex flex-wrap items-center justify-end gap-x-2 gap-y-0.5 font-mono tabular-nums">
                      {tx.member_splits.map((s) => (
                        <span key={s.member_id}>
                          {s.name || s.member_id}
                          {tx.member_splits && tx.member_splits.length > 1
                            ? ` ${formatMoney(s.amount)}`
                            : ''}
                        </span>
                      ))}
                    </span>
                  }
                />
              ) : null}
              {/* 支出组合付款明细(0028) */}
              {tx.payment_splits && tx.payment_splits.length >= 2 ? (
                <DetailRow
                  icon={<Wallet className="h-4 w-4" />}
                  label={t('paymentSplit.detail')}
                  value={
                    <span className="flex flex-wrap items-center justify-end gap-x-2 gap-y-0.5 font-mono tabular-nums">
                      {tx.payment_splits.map((s) => (
                        <span key={s.account_id}>
                          {(s.account_name || s.account_id)} {formatMoney(s.amount)}
                        </span>
                      ))}
                    </span>
                  }
                />
              ) : null}
              {attachments.length > 0 ? (
                <AttachmentRow attachments={attachments} />
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

        <DialogFooter className="flex flex-row items-center justify-end gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
          {hasRefundable ? (
            <Button
              variant="outline"
              size="sm"
              className="mr-auto"
              disabled={!effectiveCanManage}
              onClick={() => tx && onRefund?.(tx)}
            >
              <Undo2 className="mr-1 h-3.5 w-3.5" />
              {t('refund.action')}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          {/* 取消退款(0025):退款详情用 destructive「取消退款」替代常规编辑 ——
              退款金额/关联不宜自由改,撤销整笔退款走二次确认删除。 */}
          {isRefund ? (
            <Button
              size="sm"
              variant="destructive"
              disabled={!effectiveCanManage || !onCancelRefund}
              onClick={() => tx && onCancelRefund?.(tx)}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" />
              {t('refund.cancelRefund')}
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={!effectiveCanManage}
                onClick={() => tx && onCopy?.(tx)}
                title={t('common.copy')}
              >
                <Copy className="mr-1 h-3.5 w-3.5" />
                {t('common.copy')}
              </Button>
              <Button
                size="sm"
                disabled={!effectiveCanManage}
                onClick={() => tx && onEdit(tx)}
              >
                <Edit3 className="mr-1 h-3.5 w-3.5" />
                {t('common.edit')}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
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

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
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
