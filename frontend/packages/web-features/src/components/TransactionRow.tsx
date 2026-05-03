import type { AttachmentRef, ReadCategory, ReadTag, ReadTransaction } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { CategoryIcon } from './CategoryIcon'

export type TransactionRowVariant = 'default' | 'compact'

type CommonProps = {
  row: ReadTransaction
  variant?: TransactionRowVariant
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
  categoryById,
  iconPreviewUrlByFileId,
  onEdit,
  onDelete,
  canManage = true,
  onPreviewAttachment,
  onClickTag,
  onSelect,
  className
}: CommonProps) {
  const t = useT()
  const attachments = Array.isArray(row.attachments) ? row.attachments : []

  const amountTone = row.tx_type === 'expense' ? 'negative' : row.tx_type === 'income' ? 'positive' : 'default'
  const sign = row.tx_type === 'expense' ? '-' : row.tx_type === 'income' ? '+' : ''
  const categoryText = row.category_name || (row.tx_type === 'transfer' ? t('enum.txType.transfer') : '-')
  const accountText =
    row.tx_type === 'transfer'
      ? `${row.from_account_name || '-'} → ${row.to_account_name || '-'}`
      : row.account_name || '-'

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

  return (
    <div
      onClick={onSelect ? () => onSelect(row) : undefined}
      role={onSelect ? 'button' : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onKeyDown={
        onSelect
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(row)
              }
            }
          : undefined
      }
      className={`group relative flex items-start gap-3 py-2.5 ${
        isCompact ? 'px-3' : 'px-4'
      } transition-colors hover:bg-accent/30 ${
        onSelect ? 'cursor-pointer' : ''
      } ${className || ''}`}
    >
      <div className="min-w-0 flex-1">
        {/* 标题行:左 分类图标 + 分类名 · 备注;右 hover 动作 + 金额。类型徽章
            去掉了 —— 金额正负号 + 颜色已经能明确表达 income/expense/transfer,
            重复的文字标签只会挤位置。动作放在金额左侧同一 flex 行里,不再
            absolute 悬浮,避免与金额重叠。 */}
        <div className="flex items-baseline justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
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
            <span className="truncate text-sm font-medium">{categoryText}</span>
            {row.note ? (
              <span className="truncate text-xs text-muted-foreground">
                ({row.note})
              </span>
            ) : null}
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

        {/* 元信息行:时间 · 账户 · 标签 · 附件 chip。备注迁到了上面标题行和
            分类名连在一起,这里不再重复。 */}
        <div className={`mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 ${
          isCompact ? 'text-[11px]' : 'text-xs'
        } text-muted-foreground`}>
          <span className="font-mono tabular-nums">{formatDateTime(row.happened_at)}</span>
          {accountText && accountText !== '-' ? (
            <span className="truncate">· {accountText}</span>
          ) : null}

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

