import { useEffect, useMemo, useState } from 'react'
import { resolveApiUrl, type AttachmentRef, type WorkspaceTag, type WorkspaceTransaction } from '@beecount/api-client'
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

import { useAttachmentPreview } from '../../context/AttachmentPreviewContext'
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
              <span className={`text-4xl font-bold tabular-nums ${tone}`}>
                {sign}
                {tx.amount.toLocaleString('zh-CN', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
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
