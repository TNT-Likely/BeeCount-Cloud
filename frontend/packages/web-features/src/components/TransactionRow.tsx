import type { AttachmentRef, ReadCategory, ReadTag, ReadTransaction } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { CategoryIcon } from './CategoryIcon'
import { TagChip } from './TagChip'

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
  onToggleSelect
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
        {/* 左上:分类 + 备注 — self-start 钉到 row 顶部 */}
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
          <span className="truncate text-sm font-medium">{categoryText}</span>
          {row.note ? (
            <span className="truncate text-xs text-muted-foreground">
              ({row.note})
            </span>
          ) : null}
        </div>

        {/* 右上:hover 动作 + 金额 — self-start 钉顶 */}
        <div className="flex shrink-0 items-center justify-end gap-2 self-start">
          {(onEdit || onDelete) && !isCompact && !selectionMode ? (
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
        </div>

        {/* 右下:创建/编辑头像 chip — self-end 钉到 row 底部,跟左下 meta
            同 grid row,水平 baseline 自动对齐 */}
        <div className="mt-1 flex shrink-0 items-center justify-end self-end">
          {showCreator ? (
            <CreatorEditorChip row={row} currentUserId={currentUserId} t={t} />
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

