import type { AttachmentRef, ReadTag, ReadTransaction } from '@beecount/api-client'
import { useT } from '@beecount/ui'

export type TransactionRowVariant = 'default' | 'compact'

type CommonProps = {
  row: ReadTransaction
  variant?: TransactionRowVariant
  /** 标签配色字典：tagName.lowercase → color，渲染 tag badge 用。 */
  tagColorByName?: Map<string, string>
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
  /** 额外的 className，外层可以加边距 / 分隔线。 */
  className?: string
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
  tagColorByName,
  onEdit,
  onDelete,
  canManage = true,
  onPreviewAttachment,
  onClickTag,
  className
}: CommonProps) {
  const t = useT()
  const attachments = Array.isArray(row.attachments) ? row.attachments : []

  const typeBadge = (() => {
    switch (row.tx_type) {
      case 'income':
        return (
          <span className="inline-flex items-center rounded-full bg-income/15 px-2 py-0.5 text-[10px] font-semibold text-income">
            {t('enum.txType.income')}
          </span>
        )
      case 'expense':
        return (
          <span className="inline-flex items-center rounded-full bg-expense/15 px-2 py-0.5 text-[10px] font-semibold text-expense">
            {t('enum.txType.expense')}
          </span>
        )
      case 'transfer':
        return (
          <span className="inline-flex items-center rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-600 dark:text-sky-400">
            {t('enum.txType.transfer')}
          </span>
        )
      default:
        return null
    }
  })()

  const amountTone = row.tx_type === 'expense' ? 'negative' : row.tx_type === 'income' ? 'positive' : 'default'
  const sign = row.tx_type === 'expense' ? '-' : row.tx_type === 'income' ? '+' : ''
  const categoryText = row.category_name || (row.tx_type === 'transfer' ? t('enum.txType.transfer') : '-')
  const accountText =
    row.tx_type === 'transfer'
      ? `${row.from_account_name || '-'} → ${row.to_account_name || '-'}`
      : row.account_name || '-'

  const isCompact = variant === 'compact'

  const hasAttachments = attachments.length > 0 && Boolean(onPreviewAttachment)
  const firstAttachment = attachments[0]

  return (
    <div
      className={`group relative flex items-start gap-3 py-2.5 ${
        isCompact ? 'px-3' : 'px-4'
      } transition-colors hover:bg-accent/30 ${className || ''}`}
    >
      <div className="min-w-0 flex-1">
        {/* 标题行：左 类型徽章 + 分类；右 hover 动作 + 金额。动作放在金额左
            侧同一 flex 行里，不再 absolute 悬浮，避免与金额重叠。 */}
        <div className="flex items-baseline justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            {typeBadge}
            <span className="truncate text-sm font-medium">{categoryText}</span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {(onEdit || onDelete) && !isCompact ? (
              <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                {onEdit ? (
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={(event) => {
                      event.stopPropagation()
                      onEdit(row)
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
            <span className={`font-mono tabular-nums font-bold ${
              amountTone === 'positive'
                ? 'text-income'
                : amountTone === 'negative'
                  ? 'text-expense'
                  : 'text-foreground'
            } ${isCompact ? 'text-sm' : 'text-base'}`}>
              {sign}
              {row.amount.toLocaleString('zh-CN', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
              })}
            </span>
          </div>
        </div>

        {/* 元信息行：时间 · 账户 · 备注 · 标签 · 附件 chip。全部在同一行，
            不论有无附件行高一致；附件是个小 chip，点击触发预览。 */}
        <div className={`mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 ${
          isCompact ? 'text-[11px]' : 'text-xs'
        } text-muted-foreground`}>
          <span className="font-mono tabular-nums">{formatDateTime(row.happened_at)}</span>
          {accountText && accountText !== '-' ? (
            <span className="truncate">· {accountText}</span>
          ) : null}
          {row.note ? <span className="truncate">· {row.note}</span> : null}

          {row.tags_list && row.tags_list.length > 0
            ? row.tags_list.map((tagName) => {
                const color = tagColorByName?.get(tagName.trim().toLowerCase())
                const style = color
                  ? {
                      color,
                      borderColor: `${color}66`,
                      background: `${color}1a`
                    }
                  : undefined
                const clickable = Boolean(onClickTag)
                return (
                  <span
                    key={tagName}
                    className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none ${
                      clickable ? 'cursor-pointer hover:brightness-110' : ''
                    }`}
                    style={style}
                    onClick={(event) => {
                      if (!clickable) return
                      event.stopPropagation()
                      onClickTag?.(tagName)
                    }}
                  >
                    {tagName}
                  </span>
                )
              })
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
        </div>
      </div>
    </div>
  )
}

/**
 * 把 tag 数组 → lowercase-keyed color map 的小工具。外部可以一次算完复用给
 * TransactionList / TransactionRow，不必每个 row 算一遍。
 */
export function buildTagColorMap(tags: Array<Pick<ReadTag, 'name' | 'color'>>): Map<string, string> {
  const map = new Map<string, string>()
  for (const tag of tags) {
    if (tag.color) {
      map.set(tag.name.trim().toLowerCase(), tag.color)
    }
  }
  return map
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

