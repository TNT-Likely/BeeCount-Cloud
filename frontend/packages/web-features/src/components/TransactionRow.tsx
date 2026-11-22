import type { AttachmentRef, ReadCategory, ReadTag, ReadTransaction } from '@beecount/api-client'
import { Copy, Edit3, Trash2 } from 'lucide-react'

import { CategoryIcon } from './CategoryIcon'
import { TagChip } from './TagChip'
import { currencySymbol } from '../lib/currencies'
import {
  flowIconKeyForTx,
  resolveFlowIcon,
  resolveFlowIconColor,
  type FlowIconKey,
} from '../lib/flowIcons'
import { useFlowIconColorPrefs, useFlowIconPrefs } from '../lib/FlowIconsContext'
import { useT } from '@beecount/ui'
import { composeTransactionRowTitle, type NoteDisplayMode } from '../lib/transactionRowTitle'

/** 金额 / 余额统一格式:千分位 + 两位小数(与账本页金额展示一致)。 */
const formatMoney = (value: number) =>
  value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })

/**
 * 借款引擎(contacts_loans loan-engine)自动生成的流水分类名 —— 后端硬编码
 * 中文标签,不走 i18n,前端按此集合识别并渲染专用 HandCoins 图标。
 * 再叠加 exclude_from_stats=true 条件,避免用户自建同名分类误命中。
 */
const LOAN_FLOW_CATEGORY_NAMES = new Set(['借出', '借入', '收回借款', '偿还借款'])

export type TransactionRowVariant = 'default' | 'compact'

type CommonProps = {
  row: ReadTransaction
  variant?: TransactionRowVariant
  /** §7 共享账本:开启后会在 meta 行渲染"XX 创建 · YY 编辑"chip(带头像)。
   *  默认 false(单人账本不需要显示创建人)。 */
  showCreator?: boolean
  /** §7 共享账本:当前 caller user_id,用来过滤掉"自己创建+自己编辑"的 tx —
   *  这种 tx 在共享账本里也不显示 chip,避免噪声。 */
  currentUserId?: string | null
  /** 标签配色字典：tagName.lowercase → color，渲染 tag badge 用。 */
  tagColorByName?: Map<string, string>
  /** 分类字典：category_id → ReadCategory，渲染分类图标用。不传 → 不渲染图标。 */
  categoryById?: Map<string, ReadCategory>
  /** 自定义分类图标的预签预览 URL 字典(`icon_cloud_file_id → blob URL`)。
   *  透传给 CategoryIcon,custom icon 才能出图;material icon 不需要。 */
  iconPreviewUrlByFileId?: Record<string, string>
  /** 点编辑 / 删除 / 复制 的回调；不传则隐藏对应按钮。 */
  onEdit?: (row: ReadTransaction) => void
  onDelete?: (row: ReadTransaction) => void
  onCopy?: (row: ReadTransaction) => void
  canManage?: boolean
  /** 锁账截止时刻(UTC ISO)。该笔交易 happened_at <= cutoff 时禁用编辑/删除。 */
  lockCutoff?: string | null
  /** 附件预览入口：点行内 📎 chip 时触发。接收整组 attachments + 起始 index，
   *  让预览 Dialog 能做 prev/next 轮播。单附件时传 [attachment], 0 即可。 */
  onPreviewAttachment?: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
  /** 点标签可以 emit 让外层打开标签详情弹窗或过滤。 */
  onClickTag?: (tagName: string) => void
  /** 行整体点击(空白处)→ 打开详情弹窗。Edit / Delete / Tag / Attachment
   *  按钮已 stopPropagation,不会触发本回调。 */
  onSelect?: (row: ReadTransaction) => void
  /** 额外的 className，外层可以加边距 / 分隔线。 */
  className?: string
  /** 批量选择模式 —— 行首渲染 checkbox,点行整体切换选中状态而不是 onSelect。 */
  selectionMode?: boolean
  /** 当前是否选中(selectionMode=true 时生效)。 */
  selected?: boolean
  /** 切换选中。event 透传给上层判断 shift / meta 键(范围选 / 增量选)。 */
  onToggleSelect?: (row: ReadTransaction, event: React.MouseEvent) => void
  /** 跨账本场景(详情弹窗 scope='all')—— 在 meta 行追加账本名 chip,帮用户
   *  区分同一分类/标签/账户在不同账本里的记录。同账本场景不传,避免噪声。 */
  showLedger?: boolean
  /** 备注显示方式:'note' = 备注优先(有备注显示备注);默认 'category' = 分类 + 备注括号。 */
  noteDisplayMode?: NoteDisplayMode
  /** 账户详情中的转账金额按该账户显示正负方向；不传时显示交易金额。 */
  currentAccountId?: string
  /** 特殊流水图标偏好 overrides(来自 user profile 的 appearance.flow_icons)。
   *  不传时回落到 FlowIconsContext(web AppShell 顶层注入),再缺省时用
   *  flowIcons.ts 的 DEFAULT_FLOW_ICONS。 */
  flowIconOverrides?: Record<string, string> | null
}

/**
 * 通用交易行组件。支持两种 variant：
 *  - `default`: 用于交易页列表 —— 顶部一行时间 + 金额，下面一行分类·账户 +
 *    tag badges，再下面附件缩略图（如有）。hover 出现编辑/删除。
 *  - `compact`: 用于弹窗（例如标签详情）—— 信息密度更高，附件用小图标代替
 *    缩略图。
 *
 * 刻意不展示账本名 / 创建人邮箱：用户明确说首页场景下不需要这两列。
 */
export function TransactionRow({
  row,
  variant = 'default',
  showCreator = false,
  currentUserId,
  tagColorByName,
  categoryById,
  iconPreviewUrlByFileId,
  onEdit,
  onDelete,
  onCopy,
  canManage = true,
  lockCutoff = null,
  onPreviewAttachment,
  onClickTag,
  onSelect,
  className,
  selectionMode = false,
  selected = false,
  onToggleSelect,
  showLedger = false,
  noteDisplayMode = 'category',
  currentAccountId,
  flowIconOverrides
}: CommonProps) {
  const t = useT()
  const attachments = Array.isArray(row.attachments) ? row.attachments : []
  const rowLocked = Boolean(
    lockCutoff && row.happened_at && new Date(row.happened_at).getTime() <= new Date(lockCutoff).getTime()
  )
  const rowCanManage = canManage && !rowLocked
  /** 顶层(AppShell)注入的全局偏好;props 优先于 Context。 */
  const ctxFlowPrefs = useFlowIconPrefs()
  const ctxFlowColorPrefs = useFlowIconColorPrefs()
  const effectiveFlowPrefs = flowIconOverrides ?? ctxFlowPrefs

  const editableOnEdit = onEdit
  const isRefund = row.tx_type === 'refund'
  // 退款(0025):原支出被全额 / 部分退款 → 用印章颜色区分。
  const refundStatus: 'none' | 'partial' | 'full' =
    row.tx_type === 'expense' &&
    typeof row.refunded_amount === 'number' &&
    row.refunded_amount > 0 &&
    typeof row.amount === 'number' &&
    row.amount > 0
      ? row.refunded_amount >= row.amount - 0.005
        ? 'full'
        : 'partial'
      : 'none'
  // 退款(0025)展示口径:
  //  - 全额退款 → 整行置灰 + 原金额删除线(作废观感),维持不变;
  //  - 部分退款 → 行不置灰,原金额缩成灰色删除线小字,主金额显示未退款金额
  //    (原金额 − 已退),支出色 —— 这才是这笔支出当前的真实有效金额。
  const isPartialRefund = refundStatus === 'partial'
  const isFullRefund = refundStatus === 'full'
  const remainingAmount = isPartialRefund
    ? Math.max(0, Math.round((row.amount - (row.refunded_amount ?? 0)) * 100) / 100)
    : 0
  const amountTone = isRefund
    ? 'refund'
    : row.tx_type === 'expense'
    ? 'negative'
    : row.tx_type === 'income'
    ? 'positive'
    : 'default'
  const transferOut = Boolean(currentAccountId && row.from_account_id === currentAccountId)
  const transferIn = Boolean(currentAccountId && row.to_account_id === currentAccountId)
  const sign = row.tx_type === 'expense' ? '-' : row.tx_type === 'income' ? '+'
    : transferOut !== transferIn ? (transferOut ? '-' : '+') : ''
  // 交易级多币种:折算快照存在且 ≠ 原币值 → 外币交易,金额旁标币种 + ≈ 折算行。
  // 同币种交易 native === amount 恒成立,自然不显示;无需引入账本本位币 prop。
  const isForeignCurrency =
    !!row.currency_code &&
    row.native_amount != null &&
    row.native_amount !== row.amount
  const categoryText = row.category_name || (row.tx_type === 'transfer' ? t('enum.txType.transfer') : '-')
  const rowTitle = composeTransactionRowTitle({
    mode: noteDisplayMode,
    categoryName: row.category_name,
    categoryText,
    note: row.note,
  })
  const accountText =
    row.tx_type === 'transfer'
      ? `${row.from_account_name || '-'} → ${row.to_account_name || '-'}`
      : row.account_name || '-'

  const isCompact = variant === 'compact'

  // 特殊流水(转账 / 借款四态 / 退款 / 存款利息)→ 功能图标。判定口径与后端
  // 分类名生成规则严格对齐(见 flowIcons.flowIconKeyForTx);借款流水额外叠加
  // exclude_from_stats 条件,避免用户自建同名分类误命中。
  const isLoanFlow =
    Boolean(row.exclude_from_stats) && LOAN_FLOW_CATEGORY_NAMES.has(row.category_name || '')
  const flowIconKey: FlowIconKey | null = isLoanFlow
    ? flowIconKeyForTx(row)
    : row.tx_type === 'refund' || row.tx_type === 'transfer'
      ? flowIconKeyForTx(row)
      : row.category_name === '存款利息' || row.category_name === '存款利息(转存)'
        ? flowIconKeyForTx(row)
        : null
  const flowIconName = flowIconKey ? resolveFlowIcon(flowIconKey, effectiveFlowPrefs) : ''
  const flowIconColor = flowIconKey
    ? resolveFlowIconColor(flowIconKey, ctxFlowColorPrefs)
    : ''
  const flowIconTitle = flowIconKey
    ? flowIconKey === 'refund'
      ? (t('refund.badge') as string)
      : flowIconKey === 'transfer'
        ? (t('enum.txType.transfer') as string)
        : (t('loans.flowIcon') as string)
    : ''

  // 分类图标:优先按 category_id 精确匹配;匹配不到(跨账本 id 冲突 /
  // 脏数据)退化到按 name+kind 兜底,避免一整列空白。
  const categoryEntry = (() => {
    if (!categoryById) return null
    const byId = row.category_id ? categoryById.get(row.category_id) : null
    if (byId) return byId
    if (!row.category_name) return null
    for (const cat of categoryById.values()) {
      if (cat.name === row.category_name && cat.kind === row.category_kind) return cat
    }
    return null
  })()

  const hasAttachments = attachments.length > 0 && Boolean(onPreviewAttachment)
  const firstAttachment = attachments[0]
  const hasTransferAdjustments =
    row.tx_type === 'transfer' &&
    ((row.transfer_fee_amount ?? 0) > 0 || (row.transfer_discount_amount ?? 0) > 0)
  // 支出优惠(0023):列表行尾追加灰绿小字标识,金额仍是实付/入账金额。
  // 优惠前原价 = 实付金额 + 优惠额,在金额左侧用灰色删除线展示,主金额即优惠后金额。
  const hasExpenseDiscount =
    row.tx_type === 'expense' && (row.discount_amount ?? 0) > 0
  const originalAmount = hasExpenseDiscount ? row.amount + (row.discount_amount ?? 0) : 0
  // 该笔流水之后的账户余额(读接口按账户时间线现算);历史/异常数据为 null 则不展示。
  const balanceAfter = row.account_balance_after
  const counterBalanceAfter = row.counter_account_balance_after
  const hasBalance = typeof balanceAfter === 'number' && Number.isFinite(balanceAfter)
  const hasCounterBalance =
    typeof counterBalanceAfter === 'number' && Number.isFinite(counterBalanceAfter)
  const balanceTitle =
    row.tx_type === 'transfer'
      ? t('transactions.balance.transferTooltip', {
          from: row.from_account_name || '-',
          to: row.to_account_name || '-'
        })
      : t('transactions.balance.tooltip', { account: row.account_name || '-' })

  // 选择模式优先于 onSelect:点行 = 切换选中,不打开详情
  const handleRowClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (selectionMode && onToggleSelect) {
      onToggleSelect(row, event)
      return
    }
    if (onSelect) onSelect(row)
  }
  const isInteractive = selectionMode || Boolean(onSelect)

  return (
    <div
      onClick={isInteractive ? handleRowClick : undefined}
      role={isInteractive ? 'button' : undefined}
      tabIndex={isInteractive ? 0 : undefined}
      onKeyDown={
        isInteractive
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                if (selectionMode && onToggleSelect) {
                  onToggleSelect(row, e as unknown as React.MouseEvent)
                } else if (onSelect) {
                  onSelect(row)
                }
              }
            }
          : undefined
      }
      className={`group relative flex items-start gap-3 py-2.5 ${
        isCompact ? 'px-3' : 'px-4'
      } transition-colors hover:bg-accent/30 ${
        isInteractive ? 'cursor-pointer' : ''
      } ${selectionMode && selected ? 'bg-primary/8' : ''} ${
        isFullRefund ? 'bg-muted/40' : ''
      } ${className || ''}`}
    >
      {selectionMode ? (
        <div className="flex shrink-0 items-center pt-1">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => undefined}
            onClick={(e) => {
              // 由父级 handleRowClick 统一处理点击 / shift / meta;阻止冒泡到行,
              // 否则 checkbox 自身 onChange 跟 row click 会双触发。
              e.stopPropagation()
              if (onToggleSelect) onToggleSelect(row, e)
            }}
            aria-label={t('common.select') as string}
            className="h-4 w-4 cursor-pointer accent-primary"
          />
        </div>
      ) : null}
      {/* 2×2 grid layout — 左右各 2 行:
            ┌──────────────────┬──────────────┐
            │ 分类 / 备注       │ hover + 金额  │  顶
            ├──────────────────┼──────────────┤
            │ 时间·账户·tag·附件│ chip(头像)    │  底
            └──────────────────┴──────────────┘
          顶/底行因为同 grid row 自然底对齐;showCreator=false 时 chip
          单元为空(row 高度由顶行决定),tile 跟单人账本完全一致;
          有 chip 时 row 高度被撑,左下 meta 自然贴底,跟 chip 水平对齐。 */}
      <div
        className="min-w-0 flex-1 grid gap-x-3"
        style={{ gridTemplateColumns: '1fr auto', gridTemplateRows: 'auto 1fr' }}
      >
        {/* 左上:分类 + 备注 + 账本名(showLedger,主题色) — self-start 钉到 row 顶部 */}
        <div className="flex min-w-0 items-center gap-2 self-start">
          {/* 转账交易没有分类(cat_id/name/kind 全空),categoryEntry 必然为 null。
              以前这里直接不渲染 → 转账行左侧永远空一块。补一个专用图标占位,
              跟分类图标同尺寸,视觉上与收入/支出对齐。特殊流水统一走
              flowIcons 的功能图标(可用 appearance.flow_icons 自定义)。 */}
          {flowIconKey ? (
            <span
              className="inline-flex shrink-0 items-center justify-center rounded-md"
              style={{
                width: isCompact ? 18 : 20,
                height: isCompact ? 18 : 20,
                backgroundColor: `${flowIconColor}26`,
                color: flowIconColor,
              }}
              title={flowIconTitle}
              aria-label={flowIconTitle}
            >
              <CategoryIcon
                icon={flowIconName}
                iconType="material"
                size={isCompact ? 12 : 14}
                color={flowIconColor}
              />
            </span>
          ) : categoryEntry ? (
            <CategoryIcon
              icon={categoryEntry.icon}
              iconType={categoryEntry.icon_type}
              iconCloudFileId={categoryEntry.icon_cloud_file_id}
              iconPreviewUrlByFileId={iconPreviewUrlByFileId}
              size={isCompact ? 16 : 18}
              className="shrink-0 text-muted-foreground"
            />
          ) : null}
          <span className={`truncate text-sm font-medium ${isFullRefund ? 'line-through text-muted-foreground' : ''}`}>{rowTitle.primary}</span>
          {rowTitle.parenNote ? (
            <span className="truncate text-xs text-muted-foreground">
              ({rowTitle.parenNote})
            </span>
          ) : null}
          {isRefund ? (
            <span
              className="inline-flex shrink-0 items-center rounded bg-purple-500/15 px-1.5 py-0.5 text-[11px] font-medium leading-none text-purple-700 dark:text-purple-300"
              title={t('refund.detail.title') as string}
            >
              {t('refund.badge')}
            </span>
          ) : null}
          {refundStatus !== 'none' ? (
            <>
              {/* 退款印章(0025):全额退款 = 红章、部分退款 = 琥珀章,
                  旋转 + 双描边 + 半透明,模拟橡皮印章;整行已置灰 + 删除线,
                  印章保持原色以便一眼区分两种退款状态。 */}
              <span
                className={[
                  'inline-flex shrink-0 items-center rounded border-2 px-1.5 py-0.5',
                  'text-[11px] font-bold leading-none -rotate-6 select-none',
                  'ring-2 ring-inset tracking-wide',
                  refundStatus === 'full'
                    ? 'border-red-500 text-red-600 ring-red-500/40 dark:text-red-400 dark:border-red-400 dark:ring-red-400/40'
                    : 'border-amber-500 text-amber-600 ring-amber-500/40 dark:text-amber-400 dark:border-amber-400 dark:ring-amber-400/40'
                ].join(' ')}
                title={
                  refundStatus === 'full'
                    ? t('refund.stamp.fullTitle', { amount: formatMoney(row.amount) })
                    : t('refund.stamp.partialTitle', {
                        refunded: formatMoney(row.refunded_amount as number),
                        total: formatMoney(row.amount)
                      })
                }
              >
                {refundStatus === 'full' ? t('refund.stamp.full') : t('refund.stamp.partial')}
              </span>
              {refundStatus === 'partial' ? (
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {t('refund.refundedBadge', { amount: formatMoney(row.refunded_amount as number) })}
                </span>
              ) : null}
            </>
          ) : null}
          {hasTransferAdjustments ? (
            <span
              className="inline-flex shrink-0 items-center rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium leading-none text-amber-700 dark:text-amber-300"
              title={t('transactions.transferFee.title') as string}
            >
              {row.transfer_fee_amount && row.transfer_fee_amount > 0
                ? t('transactions.transferFee.badge')
                : t('transactions.transferFee.discountBadge')}
            </span>
          ) : null}
          {showLedger && row.ledger_name ? (
            <span
              className="inline-flex shrink-0 items-center rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium leading-none text-primary"
              title={row.ledger_name}
            >
              {row.ledger_name}
            </span>
          ) : null}
        </div>

        {/* 右上:hover 动作 + 金额 + 该笔之后的账户余额 — self-start 钉顶 */}
        <div className="flex shrink-0 flex-col items-end gap-0.5 self-start">
          <div className="flex items-center justify-end gap-2">
            {(editableOnEdit || onDelete || onCopy) && !isCompact && !selectionMode ? (
              <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                {onCopy ? (
                  <button
                    type="button"
                    disabled={!rowCanManage}
                    onClick={(event) => {
                      event.stopPropagation()
                      onCopy(row)
                    }}
                    className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-sky-500/15 hover:text-sky-600"
                    title={t('common.copy') as string}
                  >
                    <Copy className="h-3 w-3" />
                  </button>
                ) : null}
                {editableOnEdit ? (
                  <button
                    type="button"
                    disabled={!rowCanManage}
                    onClick={(event) => {
                      event.stopPropagation()
                      editableOnEdit(row)
                    }}
                    className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-primary/15 hover:text-primary"
                    title={t('common.edit') as string}
                  >
                    <Edit3 className="h-3 w-3" />
                  </button>
                ) : null}
                {onDelete ? (
                  <button
                    type="button"
                    disabled={!rowCanManage}
                    onClick={(event) => {
                      event.stopPropagation()
                      onDelete(row)
                    }}
                    className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    title={t('common.delete') as string}
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                ) : null}
              </div>
            ) : null}
            {/* 支出优惠:优惠前原价用灰色删除线,主金额即优惠后实付金额 */}
            {hasExpenseDiscount ? (
              <span
                className={`font-mono tabular-nums font-normal text-muted-foreground/70 line-through decoration-muted-foreground/70 ${
                  isCompact ? 'text-[11px]' : 'text-xs'
                }`}
                title={t('transactions.expenseDiscount.originalAmount') as string}
              >
                {formatMoney(originalAmount)}
              </span>
            ) : null}
            {/* 部分退款:原金额灰色删除线小字,主金额显示未退款金额 */}
            {isPartialRefund ? (
              <span
                className={`font-mono tabular-nums font-normal text-muted-foreground/70 line-through decoration-muted-foreground/70 ${
                  isCompact ? 'text-[11px]' : 'text-xs'
                }`}
                title={
                  t('refund.stamp.partialTitle', {
                    refunded: formatMoney(row.refunded_amount as number),
                    total: formatMoney(row.amount)
                  }) as string
                }
              >
                {formatMoney(row.amount)}
              </span>
            ) : null}
            <span className={`font-mono tabular-nums font-bold ${
              isFullRefund
                ? 'text-muted-foreground'
                : amountTone === 'positive'
                  ? 'text-income'
                  : amountTone === 'negative'
                    ? 'text-expense'
                    : amountTone === 'refund'
                      ? 'text-muted-foreground'
                      : 'text-foreground'
            } ${isFullRefund ? 'line-through' : ''} ${isCompact ? 'text-sm' : 'text-base'}`}
              title={
                isPartialRefund
                  ? (t('refund.remainingAmount') as string)
                  : hasExpenseDiscount
                    ? (t('transactions.expenseDiscount.afterAmount') as string)
                    : undefined
              }
            >
              {sign}
              {/* 外币显示其币种符号(JP¥/US$…,与本位币一眼区分);本位币维持纯数字 */}
              {isForeignCurrency ? currencySymbol(row.currency_code as string) : ''}
              {formatMoney(isPartialRefund ? remainingAmount : row.amount)}
            </span>
          </div>
          {hasBalance ? (
            <span
              className={`font-mono tabular-nums text-muted-foreground/80 ${
                isCompact ? 'text-[10px]' : 'text-[11px]'
              }`}
              title={balanceTitle as string}
            >
              {row.tx_type === 'transfer' && hasCounterBalance
                ? t('transactions.balance.transfer', {
                    from: formatMoney(balanceAfter as number),
                    to: formatMoney(counterBalanceAfter as number)
                  })
                : t('transactions.balance.after', { value: formatMoney(balanceAfter as number) })}
            </span>
          ) : null}
        </div>

        {/* 左下:时间·账户·标签·附件 — self-end 钉到 row 底部 */}
        <div className={`mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 self-end ${
          isCompact ? 'text-[11px]' : 'text-xs'
        } text-muted-foreground`}>
          <span className="font-mono tabular-nums">{formatDateTime(row.happened_at)}</span>
          {accountText && accountText !== '-' ? (
            <span className="truncate">· {accountText}</span>
          ) : null}
          {row.tags_list && row.tags_list.length > 0
            ? row.tags_list.map((tagName) => (
                <TagChip
                  key={tagName}
                  name={tagName}
                  color={tagColorByName?.get(tagName.trim().toLowerCase())}
                  onClick={onClickTag}
                />
              ))
            : null}
          {hasAttachments && firstAttachment ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                void onPreviewAttachment?.(attachments, 0)
              }}
              className="inline-flex items-center gap-1 rounded border border-border/60 bg-muted/30 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary"
              title={firstAttachment.originalName || firstAttachment.fileName || t('attachment.default')}
            >
              <span aria-hidden>📎</span>
              <span className="font-mono tabular-nums">{attachments.length}</span>
            </button>
          ) : null}
          {/* §4.2 标记小灰字 — 不改金额、不加图标,只在第二排尾部追加灰字标签 */}
          {row.exclude_from_stats ? (
            <span>· {t('txFlagExcludedTag')}</span>
          ) : null}
          {row.exclude_from_budget ? (
            <span>· {t('txFlagBudgetExcludedTag')}</span>
          ) : null}
          {hasTransferAdjustments ? (
            <span className="text-amber-700/80 dark:text-amber-300/80">
              · {row.transfer_fee_amount && row.transfer_fee_amount > 0
                ? t('transactions.transferFee.title')
                : t('transactions.transferFee.discountBadge')}
              {(row.transfer_fee_amount ?? 0) > 0 ? ` ${row.transfer_fee_amount}` : ''}
              {(row.transfer_discount_amount ?? 0) > 0 ? ` / ${t('transactions.transferFee.discountAmount')} ${row.transfer_discount_amount}` : ''}
            </span>
          ) : null}
          {hasExpenseDiscount ? (
            <span className="text-emerald-700/80 dark:text-emerald-300/80">
              · {t('transactions.expenseDiscount.badge')} {formatMoney(row.discount_amount ?? 0)}
              {' · '}{t('transactions.expenseDiscount.afterBadge', { value: formatMoney(row.amount) })}
            </span>
          ) : null}
          {/* 成员归属(0024):单成员显示姓名,多人分摊显示「分摊 姓名:金额 …」 */}
          {row.member_splits && row.member_splits.length > 0 ? (
            <span
              className="inline-flex items-center gap-1 text-indigo-700/80 dark:text-indigo-300/80"
              title={row.member_splits
                .map((s) => `${s.name || s.member_id} ${formatMoney(s.amount)}`)
                .join(' / ')}
            >
              · {row.member_splits.length > 1
                ? t('members.splitBadge')
                : t('members.singleBadge')}
              {' '}{row.member_names || row.member_splits.map((s) => s.name || s.member_id).join('、')}
              {row.member_splits.length > 1
                ? ` (${row.member_splits.map((s) => `${s.name || s.member_id} ${formatMoney(s.amount)}`).join(' / ')})`
                : ''}
            </span>
          ) : null}
          {/* 支出组合付款(0028):显示各付款账户及金额 */}
          {row.payment_splits && row.payment_splits.length >= 2 ? (
            <span
              className="inline-flex items-center gap-1 text-sky-700/80 dark:text-sky-300/80"
              title={row.payment_splits
                .map((s) => `${s.account_name || s.account_id} ${formatMoney(s.amount)}`)
                .join(' / ')}
            >
              · {t('paymentSplit.title')}
              {' '}({row.payment_splits.map((s) => `${s.account_name || s.account_id} ${formatMoney(s.amount)}`).join(' / ')})
            </span>
          ) : null}
        </div>

        {/* 右下:≈折算(反馈14:与左下时间行平齐)+ 创建/编辑头像 chip —
            self-end 钉到 row 底部,跟左下 meta 同 grid row,水平自动对齐 */}
        <div className="mt-1 flex shrink-0 items-center justify-end gap-2 self-end">
          {showCreator ? (
            <CreatorEditorChip row={row} currentUserId={currentUserId} t={t} />
          ) : null}
          {isForeignCurrency ? (
            <span
              className={`font-mono tabular-nums text-muted-foreground ${
                isCompact ? 'text-[11px]' : 'text-xs'
              }`}
              title={t('transactions.convertedToBase')}
            >
              ≈{(row.native_amount as number).toLocaleString('zh-CN', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
              })}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/**
 * 共享账本 tx tile 右下角"参与者"头像组。
 *
 * 显示规则(按用户最新意图):
 * - 都是自己(creator + editor) → 不显示
 * - 同一人参与(creator == editor):
 *     - 是自己 → 不显示
 *     - 别人 → 显示 1 个头像,tooltip 「{name} 创建并编辑」
 * - 两个不同人参与:显示 2 个头像,各 tooltip
 *     - 第 1 个:「{creator} 创建」(自己则 tooltip 写"我 创建")
 *     - 第 2 个:「{editor} 最后编辑」
 * - 头像:server avatar_url 失败 fallback 首字母色块。hover 才显示 label
 *   减少视觉噪声;label 走 native title (浏览器 tooltip)。
 */
function CreatorEditorChip({
  row,
  currentUserId,
  t,
}: {
  row: ReadTransaction
  currentUserId?: string | null
  t: (key: string, vars?: Record<string, string>) => string
}) {
  const creatorUid = row.created_by_user_id || ''
  const editorUid = row.last_edited_by_user_id || creatorUid
  if (!creatorUid && !editorUid) return null

  const isCreatorMe = !!currentUserId && creatorUid === currentUserId
  const isEditorMe = !!currentUserId && editorUid === currentUserId
  if (isCreatorMe && isEditorMe) return null

  const creatorName =
    row.created_by_display_name ||
    (row.created_by_email || '').split('@')[0] ||
    '?'
  const editorName =
    row.last_edited_by_display_name ||
    (row.last_edited_by_email || '').split('@')[0] ||
    creatorName

  const sameUser = creatorUid && editorUid && creatorUid === editorUid

  // 同一人参与(此时必然不是自己,前面已 early-return) → 单头像 + 创建并编辑 tooltip
  if (sameUser) {
    const title = t('sharedLedger.tileCreatedAndEditedBy', {
      name: creatorName,
    })
    return (
      <span title={title} className="inline-flex items-center">
        <UserMiniAvatar
          avatarUrl={row.created_by_avatar_url}
          name={creatorName}
          size={24}
        />
      </span>
    )
  }

  // 两个不同人参与 → 双头像,各自 tooltip
  return (
    <span className="inline-flex items-center -space-x-1.5">
      <span
        title={t('sharedLedger.tileCreatedBy', { name: creatorName })}
        className="inline-flex items-center ring-1 ring-background rounded-full"
      >
        <UserMiniAvatar
          avatarUrl={row.created_by_avatar_url}
          name={creatorName}
          size={24}
        />
      </span>
      <span
        title={t('sharedLedger.tileEditedBy', { name: editorName })}
        className="inline-flex items-center ring-1 ring-background rounded-full"
      >
        <UserMiniAvatar
          avatarUrl={row.last_edited_by_avatar_url}
          name={editorName}
          size={24}
        />
      </span>
    </span>
  )
}

function UserMiniAvatar({
  avatarUrl,
  name,
  size = 16,
}: {
  avatarUrl?: string | null
  name: string
  size?: number
}) {
  const letter = (name || '?').trim().slice(0, 1).toUpperCase()
  // letter 字号约头像高度的 45%,28px 头像 ~ 12.5px 字
  const style: React.CSSProperties = {
    width: size,
    height: size,
    fontSize: Math.round(size * 0.45),
  }
  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt={name}
        style={{ width: size, height: size }}
        className="rounded-full object-cover"
        onError={(e) => {
          // 头像 404 时退化成首字母,避免 broken-image 图标
          ;(e.currentTarget as HTMLImageElement).style.display = 'none'
        }}
      />
    )
  }
  return (
    <span
      style={style}
      className="inline-flex items-center justify-center rounded-full bg-primary/20 font-semibold text-primary"
    >
      {letter}
    </span>
  )
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
