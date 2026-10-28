import { useMemo } from 'react'

import { RotateCcw } from 'lucide-react'

import { Badge, Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, useT } from '@beecount/ui'
import type { ReadRefund } from '@beecount/api-client'
import { Amount, refundDetailBreakdown } from '@beecount/web-features'

interface Props {
  open: boolean
  /** 挂在这笔支出上的退款记录(含 cancelled,这里按状态区分展示)。 */
  refunds: readonly ReadRefund[]
  /** 原单金额(支出金额)。 */
  originalAmount: number
  /** 原单币种,缺省时走退款记录自己的 currency。 */
  currency: string
  canManage: boolean
  /** 继续登记:可退额度 > 0 时展示,交给外层打开登记弹窗。 */
  onAdd?: () => void
  onOpenChange: (next: boolean) => void
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  pending: 'secondary',
  received: 'default',
  cancelled: 'destructive',
}

/**
 * 退款详情弹窗 —— 交易详情里点「查看退款 · N」就地弹出,不再跳转退款管理页。
 *
 * 只读视图:按时间正序列出这笔支出上的每一笔退款(部分退 / 多次退场景会有
 * 多条),顶部给出「已退 / 原单 / 剩余可退」进度。已取消的记录置灰但不隐藏
 * —— 用户需要看到自己撤掉过哪一笔。
 *
 * 不提供编辑 / 删除:就地改退款涉及冲突重试与额度校验,交给退款管理页处理,
 * 这里保持一致的单入口语义。
 */
export function RefundDetailDialog({
  open,
  refunds,
  originalAmount,
  currency,
  canManage,
  onAdd,
  onOpenChange,
}: Props) {
  const t = useT()

  // 排序 / 已退合计 / 剩余可退 / 比例统一走 lib,可单测(见 refunds.test.ts)。
  const { ordered, refunded, remaining, ratio } = useMemo(
    () => refundDetailBreakdown(refunds, originalAmount),
    [refunds, originalAmount],
  )
  const canAdd = canManage && remaining > 0.011 && Boolean(onAdd)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] max-w-md flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="text-sm font-medium text-muted-foreground">
            {t('refunds.viewer.title')}
          </DialogTitle>
        </DialogHeader>

        <div className="min-h-0 overflow-y-auto">
          {/* 顶部进度:已退 / 原单 / 剩余可退 */}
          <div className="border-b border-border/60 bg-muted/20 px-6 py-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {t('refunds.viewer.refunded')}
              </span>
              <span className="text-lg font-semibold tabular-nums text-income">
                {refunded.toLocaleString('zh-CN', {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </span>
            </div>
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.round(ratio * 100)}%` }}
              />
            </div>
            <div className="mt-2 flex items-baseline justify-between gap-3 text-[11px] text-muted-foreground">
              <span>
                {t('refunds.viewer.original')}{' '}
                <span className="tabular-nums">
                  {originalAmount.toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              </span>
              <span>
                {t('refunds.viewer.remaining')}{' '}
                <span className="tabular-nums">
                  {Math.max(remaining, 0).toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              </span>
            </div>
          </div>

          {/* 逐笔退款 */}
          <div className="divide-y divide-border/60 px-6">
            {ordered.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {t('refunds.viewer.empty')}
              </div>
            ) : (
              ordered.map((row) => (
                <RefundRecord
                  key={row.id}
                  row={row}
                  currency={row.currency || currency}
                />
              ))
            )}
          </div>
        </div>

        <DialogFooter className="flex flex-row items-center justify-between gap-2 border-t border-border/60 bg-muted/20 px-6 py-3">
          {canAdd ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                onOpenChange(false)
                onAdd?.()
              }}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" />
              {t('refunds.viewer.add')}
            </Button>
          ) : (
            <span />
          )}
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t('dialog.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 单笔退款记录:金额 + 状态 + 日期 / 去向 / 原因 / 备注。 */
function RefundRecord({ row, currency }: { row: ReadRefund; currency: string }) {
  const t = useT()
  const cancelled = row.status === 'cancelled'
  return (
    <div className={`py-3 ${cancelled ? 'opacity-60' : ''}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-sm font-medium">
              {row.reason || t('refunds.untitled')}
            </span>
            <Badge
              variant={STATUS_VARIANT[row.status] || 'outline'}
              className="text-[11px]"
            >
              {t(`refunds.status.${row.status}`)}
            </Badge>
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {formatRefundDate(row.happened_at)}
          </div>
        </div>
        <div
          className={`shrink-0 text-sm font-semibold tabular-nums text-income ${
            cancelled ? 'line-through decoration-1' : ''
          }`}
        >
          <Amount value={Number(row.amount) || 0} currency={currency} compact={false} />
        </div>
      </div>

      <div className="mt-2 space-y-1 text-[11px]">
        {/* 分别退款(0025):逐条腿列出「账户 + 金额」(合计 = 本笔退款);
            统一退款只有单一去向,一行就够。 */}
        {row.refund_mode === 'split' && row.refund_legs && row.refund_legs.length ? (
          <div className="flex items-start justify-end gap-1.5">
            <span className="shrink-0 text-muted-foreground">
              {t('refunds.viewer.legs')}
            </span>
            <span className="text-right">
              {row.refund_legs.map((leg, i) => (
                <span key={`${leg.accountId}-${i}`} className="tabular-nums">
                  {i > 0 ? '、' : ''}
                  {leg.accountName || leg.accountId}{' '}
                  {(Number(leg.amount) || 0).toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              ))}
            </span>
          </div>
        ) : (
          <FieldRow
            label={t('refunds.field.refundMode')}
            value={destinationText(row)}
          />
        )}
        {row.note ? (
          <FieldRow label={t('refunds.field.note')} value={row.note} />
        ) : null}
      </div>
    </div>
  )
}

function FieldRow({ label, value }: { label: string; value: string }) {
  if (!value) return null
  return (
    <div className="flex items-start justify-end gap-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[70%] text-right break-all">{value}</span>
    </div>
  )
}

/** 去向:分别退款列出各卡名,否则退回账户名(账户被删时回退 id)。 */
function destinationText(row: ReadRefund): string {
  if (row.refund_mode === 'split' && row.refund_legs && row.refund_legs.length) {
    return (
      row.refund_legs
        .map((l) => l.accountName || l.accountId)
        .filter(Boolean)
        .join('、') || ''
    )
  }
  return row.account_name || row.account_id || ''
}

/** 退款日期:纯日期只显示日期,带时间则补上 HH:MM。 */
function formatRefundDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = value.slice(0, 10)
  if (!value.includes('T')) return date
  return `${date} ${value.slice(11, 16)}`
}
