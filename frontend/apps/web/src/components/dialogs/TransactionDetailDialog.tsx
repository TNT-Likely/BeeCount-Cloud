import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import type { AttachmentRef, WorkspaceTag, WorkspaceTransaction } from '@beecount/api-client'
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
import { Calendar, ChevronLeft, ChevronRight, Edit3, Hash, ImageOff, Tag, User, Wallet, X } from 'lucide-react'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'

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

  // tag name(lowercased)→ color 字典,跟 TransactionRow 用同样模式
  const tagColorByName = useMemo(() => buildTagColorMap(tags), [tags])

  const open = Boolean(tx)
  const sign = tx?.tx_type === 'expense' ? '−' : tx?.tx_type === 'income' ? '+' : ''
  const tone =
    tx?.tx_type === 'expense'
      ? 'text-expense'
      : tx?.tx_type === 'income'
        ? 'text-income'
        : 'text-foreground'
  const typeLabel = tx
    ? t(`enum.txType.${tx.tx_type}`)
    : ''
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
      <DialogContent className="flex max-w-md flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="text-sm font-medium text-muted-foreground">
            {t('detail.transaction.title')}
          </DialogTitle>
        </DialogHeader>

        {tx ? (
          <div className="flex flex-col">
            {/* 大金额 */}
            <div className="flex flex-col items-center gap-1 border-b border-border/60 bg-muted/20 px-6 py-6">
              <span className="text-[11px] uppercase tracking-widest text-muted-foreground">
                {typeLabel}
              </span>
              <span className={`text-4xl font-bold tabular-nums ${tone}`}>
                {sign}
                {tx.amount.toLocaleString('zh-CN', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </span>
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
              {tx.created_by_email ? (
                <DetailRow
                  icon={<User className="h-4 w-4" />}
                  label={t('detail.transaction.createdBy')}
                  value={tx.created_by_display_name || tx.created_by_email}
                />
              ) : null}
            </div>
          </div>
        ) : null}

        <DialogFooter className="flex flex-row items-center justify-end gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
  const { previewMap, ensureLoadedMany } = useAttachmentCache()
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)

  // 列表渲染时把所有有 cloudFileId 的附件触发后台下载(去重,只跑一次)
  const fileIds = useMemo(
    () =>
      attachments
        .map((a) => a.cloudFileId?.trim())
        .filter((id): id is string => Boolean(id)),
    [attachments],
  )
  useEffect(() => {
    if (fileIds.length === 0) return
    ensureLoadedMany(fileIds)
  }, [fileIds, ensureLoadedMany])

  // 仅有 cloudFileId 的附件可被预览(没有 cloudFileId = 还没上传到 server)
  const previewables = useMemo(
    () =>
      attachments.filter(
        (a) => typeof a.cloudFileId === 'string' && a.cloudFileId.trim().length > 0,
      ),
    [attachments],
  )

  return (
    <>
      <div className="flex flex-col gap-2 py-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span aria-hidden>📎</span>
          <span>{t('detail.transaction.attachments')}</span>
          <span className="text-[11px] text-muted-foreground/70">
            {t('detail.transaction.attachmentsCount', { count: attachments.length })}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {attachments.map((att, i) => {
            const fileId = att.cloudFileId?.trim() || ''
            const blobUrl = fileId ? previewMap[fileId] : undefined
            const previewableIndex = previewables.findIndex(
              (p) => (p.cloudFileId || '').trim() === fileId && fileId !== '',
            )
            const canPreview = previewableIndex >= 0 && Boolean(blobUrl)
            return (
              <button
                key={`${fileId || att.fileName || 'pending'}-${i}`}
                type="button"
                disabled={!canPreview}
                onClick={() => canPreview && setPreviewIndex(previewableIndex)}
                className={`relative aspect-square overflow-hidden rounded-md border border-border/60 bg-muted/40 transition ${
                  canPreview
                    ? 'cursor-pointer hover:border-primary hover:shadow-md'
                    : 'cursor-default'
                }`}
                title={att.originalName || att.fileName}
              >
                {blobUrl ? (
                  <img
                    src={blobUrl}
                    alt={att.originalName || att.fileName}
                    className="h-full w-full object-cover"
                    loading="lazy"
                  />
                ) : blobUrl === '' ? (
                  // 已探测过但不是图片 / 下载失败
                  <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-muted-foreground/60">
                    <ImageOff className="h-5 w-5" />
                    <span className="line-clamp-1 px-1 text-[9px]">
                      {att.originalName || att.fileName}
                    </span>
                  </div>
                ) : (
                  // 还在加载 / 未上传(cloudFileId 缺失)
                  <div className="flex h-full w-full items-center justify-center">
                    <div className="h-5 w-5 animate-pulse rounded-full bg-muted-foreground/30" />
                  </div>
                )}
              </button>
            )
          })}
        </div>
      </div>
      {previewIndex !== null ? (
        <AttachmentLightbox
          attachments={previewables}
          startIndex={previewIndex}
          onClose={() => setPreviewIndex(null)}
        />
      ) : null}
    </>
  )
}

/**
 * 全屏大图覆盖层 — 跟 TransactionsPage 的预览弹窗体验对齐。
 * 自带 prev/next 切换、Esc / 点击空白处关闭。
 * blob URL 同样从 AttachmentCache 取(同 cache 共享),不重复下载。
 */
function AttachmentLightbox({
  attachments,
  startIndex,
  onClose,
}: {
  attachments: AttachmentRef[]
  startIndex: number
  onClose: () => void
}) {
  const { previewMap } = useAttachmentCache()
  const [index, setIndex] = useState(startIndex)

  // Esc 关闭 + 左右箭头切换 — 全屏 lightbox 的标准操作
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'ArrowLeft' && index > 0) {
        setIndex((i) => i - 1)
      } else if (e.key === 'ArrowRight' && index < attachments.length - 1) {
        setIndex((i) => i + 1)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [index, attachments.length, onClose])

  const current = attachments[index]
  const fileId = current?.cloudFileId?.trim() || ''
  const url = fileId ? previewMap[fileId] : undefined
  const fileName = current?.originalName || current?.fileName || ''

  // 必须 Portal 到 document.body — TransactionDetailDialog 用的 Radix Dialog
  // 内部带 transform(用于居中动画),会创造新的 CSS containing block,导致
  // `fixed inset-0` 元素被困在 Dialog 大小内,看起来"大图只在弹窗里"。
  // Portal 把节点挂到 body,跳出 Dialog 的 containing block,真正占满 viewport。
  if (typeof document === 'undefined') return null
  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 p-4"
      // 仅当**点击点恰好是这个 div 本身**(也就是黑色空白区域)才关闭。
      // 子元素冒泡上来的 click 不会触发 — e.target 是子元素,e.currentTarget
      // 是这个 div,两者不等就跳过。这是 React 模态框的标准模式,不依赖
      // stopPropagation,也不被 disabled button 的浏览器行为坑(disabled button
      // 在某些浏览器里 click 事件会绕过 button 直接冒泡到祖先,普通
      // stopPropagation 拦不住)。
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white transition hover:bg-white/20"
        aria-label="Close preview"
      >
        <X className="h-5 w-5" />
      </button>

      {attachments.length > 1 ? (
        <>
          <button
            type="button"
            disabled={index === 0}
            onClick={() => setIndex((i) => i - 1)}
            className="absolute left-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-white transition hover:bg-white/20 disabled:opacity-30"
            aria-label="Previous"
          >
            <ChevronLeft className="h-6 w-6" />
          </button>
          <button
            type="button"
            disabled={index === attachments.length - 1}
            onClick={() => setIndex((i) => i + 1)}
            className="absolute right-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-white transition hover:bg-white/20 disabled:opacity-30"
            aria-label="Next"
          >
            <ChevronRight className="h-6 w-6" />
          </button>
        </>
      ) : null}

      <div className="flex max-h-full max-w-full flex-col items-center gap-3">
        {url ? (
          <img
            src={url}
            alt={fileName}
            className="max-h-[85vh] max-w-[90vw] rounded object-contain"
          />
        ) : (
          <div className="flex h-64 w-64 items-center justify-center text-white/60">
            <div className="h-8 w-8 animate-pulse rounded-full bg-white/30" />
          </div>
        )}
        <div className="flex items-center gap-3 text-xs text-white/80">
          <span className="line-clamp-1 max-w-[60vw]">{fileName}</span>
          {attachments.length > 1 ? (
            <span className="rounded bg-white/10 px-2 py-0.5">
              {index + 1} / {attachments.length}
            </span>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  )
}
