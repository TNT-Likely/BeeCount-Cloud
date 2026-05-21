import { useEffect, useState } from 'react'
import type { ReadLedger } from '@beecount/api-client'
import { fetchReadLedgerStats, type ReadLedgerStats } from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import { AlertTriangle, FileText, Image as ImageIcon, Users } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'

interface Props {
  /** 待删除的账本。null = 关闭。 */
  ledger: ReadLedger | null
  /** 操作执行中:disable 按钮防双击 + 阻止用户在网络往返期间关闭。 */
  loading: boolean
  onCancel: () => void
  onConfirm: () => void
}

/**
 * 删除账本确认弹窗 — 独立 dialog,不复用编辑弹窗(per #13 review:
 * "破坏性的、独立的动作不应该塞进编辑弹窗")。
 *
 * 展示用户即将丢失的东西:
 *  - 交易笔数 + 附件张数(拉 /read/ledgers/{id}/stats 实时拿)
 *  - 共享账本:成员数 + 警告"他们将一并失去访问权"
 *  - 明确说"此操作不可撤销" + "包括所有附件和历史"
 *
 * 拿不到 stats(网络失败 / 旧后端)时退化为只展示账本名,不阻塞删除。
 */
export function LedgerDeleteConfirmDialog({
  ledger,
  loading,
  onCancel,
  onConfirm,
}: Props) {
  const t = useT()
  const { token } = useAuth()
  const [stats, setStats] = useState<ReadLedgerStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)

  useEffect(() => {
    if (!ledger) {
      setStats(null)
      return
    }
    let cancelled = false
    setStatsLoading(true)
    fetchReadLedgerStats(token, ledger.ledger_id)
      .then((rows) => {
        if (!cancelled) setStats(rows)
      })
      .catch(() => {
        // 失败静默 — 弹窗仍能用,只是不展示明细
        if (!cancelled) setStats(null)
      })
      .finally(() => {
        if (!cancelled) setStatsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [ledger, token])

  const memberCount = ledger?.member_count ?? 1
  const otherMembers = ledger?.is_shared && memberCount > 1 ? memberCount - 1 : 0

  return (
    <Dialog
      open={Boolean(ledger)}
      onOpenChange={(open) => {
        if (!open && !loading) onCancel()
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            {t('ledgers.delete.title').replace('{name}', ledger?.ledger_name || '')}
          </DialogTitle>
          <DialogDescription>{t('ledgers.delete.description')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {/* 明细 — 让用户清楚 "X 笔交易 + Y 个附件 会一起没"。stats 还在加载
              时整段不显示(避免数字闪烁误导)。 */}
          {statsLoading ? (
            <div className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              {t('ledgers.delete.loadingStats')}
            </div>
          ) : stats ? (
            <div className="rounded-md border border-border/60 bg-muted/30 px-3 py-3 text-sm">
              <div className="mb-2 text-[11px] uppercase tracking-widest text-muted-foreground">
                {t('ledgers.delete.willDelete')}
              </div>
              <ul className="space-y-1.5">
                <li className="flex items-center gap-2">
                  <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-mono tabular-nums">{stats.transaction_count}</span>
                  <span className="text-muted-foreground">
                    {t('ledgers.delete.stats.txs')}
                  </span>
                </li>
                <li className="flex items-center gap-2">
                  <ImageIcon className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-mono tabular-nums">{stats.attachment_count}</span>
                  <span className="text-muted-foreground">
                    {t('ledgers.delete.stats.attachments')}
                  </span>
                </li>
              </ul>
            </div>
          ) : null}

          {/* 共享账本警告 — 只有 owner 进得来这个对话框(server _OWNER_ONLY_ROLES);
              owner 要意识到删账本会把所有 editor / viewer 踢出去。 */}
          {otherMembers > 0 ? (
            <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm">
              <Users className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
              <p className="text-foreground/80">
                {t('ledgers.delete.sharedWarning').replace('{count}', String(otherMembers))}
              </p>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" disabled={loading} onClick={onCancel}>
            {t('dialog.cancel')}
          </Button>
          <Button variant="destructive" disabled={loading} onClick={onConfirm}>
            {t('ledgers.delete.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
