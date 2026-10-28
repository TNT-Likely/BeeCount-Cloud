import type { AttachmentRef, ReadCategory, ReadTag, ReadTransaction } from '@beecount/api-client'
import { useT } from '@beecount/ui'
import { Ban, BadgePercent, CircleSlash, Clock, Gift, Layers, Paperclip, PiggyBank, Receipt, Tag, Undo2, Users, Wallet } from 'lucide-react'

import { CategoryIcon } from './CategoryIcon'
import { TagChip } from './TagChip'
import { currencySymbol } from '../lib/currencies'
import { dispatchOpenTimeDepositDetail } from '../lib/entityDetailEvents'
import { isFullyRefundedToCard } from '../lib/refunds'
import { composeTransactionRowTitle, type NoteDisplayMode } from '../lib/transactionRowTitle'

export type TransactionRowVariant = 'default' | 'compact'

/** 0024 成员分摊:流水行最多显示几个姓名,超出折叠成「+N」(行宽有限)。 */
const MEMBER_NAME_LIMIT = 2

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
  /** 点编辑 / 删除 的回调；不传则隐藏对应按钮。 */
  onEdit?: (row: ReadTransaction) => void
  onDelete?: (row: ReadTransaction) => void
  canManage?: boolean
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
  canManage = true,
  onPreviewAttachment,
  onClickTag,
  onSelect,
  className,
  selectionMode = false,
  selected = false,
  onToggleSelect,
  showLedger = false,
  noteDisplayMode = 'category',
  currentAccountId
}: CommonProps) {
  const t = useT()
  const attachments = Array.isArray(row.attachments) ? row.attachments : []

  const editableOnEdit = onEdit
  const amountTone =
    row.tx_type === 'expense' ? 'negative' : row.tx_type === 'income' ? 'positive' : 'default'
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
  // 分类为空时不再显示裸「-」,退回交易类型文案(支出/收入/转账),避免空内容难看。
  const categoryText =
    row.category_name ||
    (row.tx_type === 'transfer'
      ? t('enum.txType.transfer')
      : row.tx_type === 'income'
        ? t('enum.txType.income')
        : t('enum.txType.expense'))
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

  // 0021 交易扩展字段展示:优惠减免额 / 转账手续费 / 多账户合并付款。
  const fmt2 = (n?: number | null) =>
    (n ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  // 优惠仅 expense 有意义;discountAmount = 减免额(原价 − 实付)。
  // amount 模式下 discountAmount 可能缺省,退回到 value(减免额)。
  const discountSaved =
    row.discount
      ? (row.discount.discountAmount ??
        (row.discount.mode === 'amount' ? row.discount.value : null))
      : null
  const hasMultiPay = Array.isArray(row.payment_legs) && row.payment_legs.length > 0

  // 0022 退款进度(卡维度):「退回本卡」的退款累计 ≥ 原单金额 → 全额退款,
  // 流水金额画删除线(与服务端 refunded_to_account 口径一致,容差 0.011)。
  // 跨卡退款钱到了别的卡,本卡支出仍成立(余额也没回来),不画线 —— 与卡的
  // 角度(账户余额)保持一致。仅 expense 有意义(退款只挂支出)。
  const isFullyRefunded =
    row.tx_type === 'expense' && isFullyRefundedToCard(row)

  // 0024 成员分摊:流水行只显示姓名(金额/占比在详情弹窗里看),重名按
  // memberId 去重 —— 同一个人被拆成两条腿不该显示两遍。
  const memberNames = (() => {
    const shares = row.member_shares
    if (!Array.isArray(shares) || shares.length === 0) return []
    const out: string[] = []
    const seen = new Set<string>()
    for (const share of shares) {
      const name = share.memberName || share.memberId
      if (!name) continue
      const key = share.memberId || name
      if (seen.has(key)) continue
      seen.add(key)
      out.push(name)
    }
    return out
  })()

  const isCompact = variant === 'compact'

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
      } ${selectionMode && selected ? 'bg-primary/8' : ''} ${className || ''}`}
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
          {categoryEntry ? (
            <CategoryIcon
              icon={categoryEntry.icon}
              iconType={categoryEntry.icon_type}
              iconCloudFileId={categoryEntry.icon_cloud_file_id}
              iconPreviewUrlByFileId={iconPreviewUrlByFileId}
              size={isCompact ? 16 : 18}
              className="shrink-0 text-muted-foreground"
            />
          ) : null}
          <span className="truncate text-sm font-medium">{rowTitle.primary}</span>
          {rowTitle.parenNote ? (
            <span className="truncate text-xs text-muted-foreground">
              ({rowTitle.parenNote})
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

        {/* 右上:hover 动作 + 金额 — self-start 钉顶;下方附"交易后余额"流水行 */}
        <div className="flex shrink-0 flex-col items-end justify-start gap-0.5 self-start">
          <div className="flex items-center justify-end gap-2">
            {(editableOnEdit || onDelete) && !isCompact && !selectionMode ? (
              <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                {editableOnEdit ? (
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={(event) => {
                      event.stopPropagation()
                      editableOnEdit(row)
                    }}
                    className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-primary/15 hover:text-primary"
                  >
                    {t('common.edit')}
                  </button>
                ) : null}
                {onDelete ? (
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={(event) => {
                      event.stopPropagation()
                      onDelete(row)
                    }}
                    className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  >
                    {t('common.delete')}
                  </button>
                ) : null}
              </div>
            ) : null}
            {/* 全额退款(卡维度):金额置灰 + 删除线 —— 置灰优先于支出红/收入绿,
                一眼看出这笔钱已退回本卡、不再计支出。保留删除线是为了跟
                「已退款」语义继续对齐(容差 0.011),tooltip 说明口径。 */}
            <span
              className={`font-mono tabular-nums font-bold ${
                isFullyRefunded
                  ? 'text-muted-foreground/70 line-through decoration-1'
                  : amountTone === 'positive'
                    ? 'text-income'
                    : amountTone === 'negative'
                      ? 'text-expense'
                      : 'text-foreground'
              } ${isCompact ? 'text-sm' : 'text-base'}`}
              title={isFullyRefunded ? t('transactions.refundedStrikeHint') : undefined}
            >
              {sign}
              {/* 外币显示其币种符号(JP¥/US$…,与本位币一眼区分);本位币维持纯数字 */}
              {isForeignCurrency ? currencySymbol(row.currency_code as string) : ''}
              {row.amount.toLocaleString('zh-CN', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
              })}
            </span>
          </div>
          {/* 资产流水余额(server with_balance 计算):
              - 合并付款(0021):分别显示每个腿账户的余额(balance_by_account)。
              - 否则:显示主账户余额(balance_after)。转账默认追踪转出账户;
                账户筛选模式下追踪被筛选账户(双向)。 */}
          {(() => {
            // 账户明细(对账单)场景下只显示「该账户」的交易后余额,不做合并付款
            // 多腿拆分(否则会把另一条腿的账户余额也带出来,反而看不清本卡余额)。
            // 其余列表场景(普通交易流水)保留合并付款的多腿余额展示。
            const legBalances =
              row.balance_by_account && row.payment_legs && !currentAccountId
                ? Object.entries(row.balance_by_account)
                    .map(([aid, bal]) => ({
                      aid,
                      name:
                        row.payment_legs!.find((l) => l.accountId === aid)?.accountName ||
                        aid,
                      bal: bal as number,
                    }))
                    .filter((e) => e.bal != null)
                : []
            if (legBalances.length > 0) {
              return (
                <span
                  className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono tabular-nums text-[11px] text-muted-foreground"
                  title={t('transactions.balanceAfterHint')}
                >
                  {legBalances.map((e, i) => (
                    <span key={e.aid} className="inline-flex items-center gap-1">
                      {i > 0 ? (
                        <span className="text-border">·</span>
                      ) : null}
                      <span className="font-sans">{e.name}</span>
                      <span
                        className={
                          e.bal < 0 ? 'text-expense' : 'text-muted-foreground'
                        }
                      >
                        {t('transactions.balanceAfter')}
                        {e.bal.toLocaleString('zh-CN', {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </span>
                    </span>
                  ))}
                </span>
              )
            }
            if (row.balance_after != null) {
              return (
                <span
                  className={`font-mono tabular-nums text-[11px] ${
                    (row.balance_after as number) < 0
                      ? 'text-expense'
                      : 'text-muted-foreground'
                  }`}
                  title={t('transactions.balanceAfterHint')}
                >
                  {t('transactions.balanceAfter')}
                  {(row.balance_after as number).toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              )
            }
            return null
          })()}
        </div>

        {/* 左下:时间·账户·标签·附件·扩展字段 —— self-end 钉到 row 底部。
            每个元信息前加一个小图标,视觉更整齐("好看一点")。 */}
        <div className={`mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 self-end ${
          isCompact ? 'text-[11px]' : 'text-xs'
        } text-muted-foreground`}>
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3 shrink-0 opacity-60" />
            <span className="font-mono tabular-nums">{formatDateTime(row.happened_at)}</span>
          </span>
          {accountText && accountText !== '-' ? (
            <span className="inline-flex items-center gap-1">
              <Wallet className="h-3 w-3 shrink-0 opacity-60" />
              <span className="truncate">{accountText}</span>
            </span>
          ) : null}
          {row.tags_list && row.tags_list.length > 0 ? (
            <span className="inline-flex items-center gap-1">
              <Tag className="h-3 w-3 shrink-0 opacity-60" />
              <span className="flex flex-wrap items-center gap-1">
                {row.tags_list.map((tagName) => (
                  <TagChip
                    key={tagName}
                    name={tagName}
                    color={tagColorByName?.get(tagName.trim().toLowerCase())}
                    onClick={onClickTag}
                  />
                ))}
              </span>
            </span>
          ) : null}
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
              <Paperclip className="h-3 w-3 shrink-0 opacity-70" />
              <span className="font-mono tabular-nums">{attachments.length}</span>
            </button>
          ) : null}
          {/* 0021 交易扩展字段:优惠减免 / 转账手续费 / 多账户合并付款 —— 仅在有值时展示 */}
          {discountSaved != null && discountSaved !== 0 ? (
            <span className="inline-flex items-center gap-1" title={t('transactions.extDiscountHint')}>
              <BadgePercent className="h-3 w-3 shrink-0 opacity-60" />
              <span>{t('transactions.extDiscount')}</span>
              <span className="font-mono tabular-nums text-foreground">{fmt2(discountSaved)}</span>
            </span>
          ) : null}
          {row.transfer_fee ? (
            row.transfer_fee.mode === 'discount' ? (
              /* 0026 优惠模式:转出方少扣、到账方全收 —— 用「优惠」语义展示 */
              <span className="inline-flex items-center gap-1" title={t('transactions.extTransferDiscountHint')}>
                <Gift className="h-3 w-3 shrink-0 opacity-60" />
                <span>{t('transactions.extTransferDiscount')}</span>
                <span className="font-mono tabular-nums text-foreground">{fmt2(row.transfer_fee.amount)}</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1" title={t('transactions.extFeeHint')}>
                <Receipt className="h-3 w-3 shrink-0 opacity-60" />
                <span>{t('transactions.extFee')}</span>
                <span className="font-mono tabular-nums text-foreground">{fmt2(row.transfer_fee.amount)}</span>
              </span>
            )
          ) : null}
          {hasMultiPay ? (
            <span className="inline-flex items-center gap-1" title={t('transactions.extPaymentLegsHint')}>
              <Layers className="h-3 w-3 shrink-0 opacity-60" />
              <span>{t('transactions.extPaymentLegs')}</span>
              <span className="font-mono tabular-nums text-foreground">
                {row.payment_legs!.map((leg, idx) => (
                  <span key={leg.accountId || idx}>
                    {idx > 0 ? ' + ' : ''}
                    {leg.accountName || '?'}
                    {' '}
                    {fmt2(leg.amount)}
                  </span>
                ))}
              </span>
            </span>
          ) : null}
          {/* 定期存款(0031):利息收入关联的定存 —— 可点开定存详情(0032) */}
          {row.time_deposit_id ? (
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-sm underline decoration-dotted decoration-muted-foreground/50 underline-offset-2 transition-opacity hover:opacity-100 hover:decoration-solid focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              style={{ opacity: 0.85 }}
              title={
                row.time_deposit_name
                  ? t('transactions.extTimeDepositHint').replace('{name}', row.time_deposit_name)
                  : t('transactions.extTimeDeposit')
              }
              onClick={(e) => {
                // 行整体点击是「打开交易详情」,这里 stopPropagation 只开定存详情。
                e.stopPropagation()
                dispatchOpenTimeDepositDetail(row.time_deposit_id as string)
              }}
            >
              <PiggyBank className="h-3 w-3 shrink-0 opacity-60" />
              <span>{t('transactions.extTimeDeposit')}</span>
              {row.time_deposit_name ? (
                <span className="font-mono tabular-nums text-foreground">{row.time_deposit_name}</span>
              ) : null}
            </button>
          ) : null}
          {/* 0024 成员分摊:这笔账算在谁头上 —— 人多时折叠成「+N」 */}
          {memberNames.length > 0 ? (
            <span
              className="inline-flex items-center gap-1"
              title={t('transactions.memberSharesHint').replace('{names}', memberNames.join('、'))}
            >
              <Users className="h-3 w-3 shrink-0 opacity-60" />
              <span>
                {memberNames.slice(0, MEMBER_NAME_LIMIT).join('、')}
                {memberNames.length > MEMBER_NAME_LIMIT
                  ? ` +${memberNames.length - MEMBER_NAME_LIMIT}`
                  : ''}
              </span>
            </span>
          ) : null}
          {/* 退款到账(0032):回显退的是哪张原单 —— 仅在有值时展示 */}
          {row.refund_source_label ? (
            <span
              className="inline-flex items-center gap-1"
              title={t('transactions.extRefundSourceHint').replace(
                '{name}',
                row.refund_source_label
              )}
            >
              <Undo2 className="h-3 w-3 shrink-0 opacity-60" />
              <span>{t('transactions.extRefundSource')}</span>
              <span className="font-mono tabular-nums text-foreground">
                {row.refund_source_label}
              </span>
            </span>
          ) : null}
          {/* §4.2 标记小灰字 —— 加图标,保持最小视觉噪声 */}
          {row.exclude_from_stats ? (
            <span className="inline-flex items-center gap-1">
              <Ban className="h-3 w-3 shrink-0 opacity-60" />
              <span>{t('txFlagExcludedTag')}</span>
            </span>
          ) : null}
          {row.exclude_from_budget ? (
            <span className="inline-flex items-center gap-1">
              <CircleSlash className="h-3 w-3 shrink-0 opacity-60" />
              <span>{t('txFlagBudgetExcludedTag')}</span>
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
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd} ${hh}:${mi}`
}
