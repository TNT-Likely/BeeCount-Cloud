import { Bell } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  ApiError,
  fetchNotifications,
  fetchWorkspaceAccounts,
  fetchWorkspaceTransactions,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from '@beecount/api-client'
import {
  Badge,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  useT,
} from '@beecount/ui'

import { useAuth } from '../context/AuthContext'
import { useSyncEvent } from '../context/SyncSocketContext'
import { dispatchOpenDetailAccount, dispatchOpenDetailTx } from '../lib/txDialogEvents'

const POLL_INTERVAL_MS = 60_000

/**
 * 通知中心入口(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0 web UI)。
 *
 * 跟其余 header 图标不同 —— notifications 是 user-global 的普通 REST 资源,
 * **不**进 `sync_changes`,所以这里靠定时轮询 + "打开面板时立即刷新" 两条
 * 路径拿新数据,不接 useSyncRefresh。唯一的例外是管理者系统公告:发送/撤回
 * 时 server 会推 `notification_changed`,收到就立即重拉,不用等下一轮轮询。
 */
export function NotificationBell() {
  const t = useT()
  const { token, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [loading, setLoading] = useState(false)
  // 通知文字在下拉列表里会被截断(`truncate`),点开先看完整标题/内容,
  // 「查看详情」按钮才真的跳转 —— 不再是点一下列表项就直接跳走。
  const [detailItem, setDetailItem] = useState<NotificationItem | null>(null)
  const [jumping, setJumping] = useState(false)

  const refresh = useCallback(async () => {
    if (!token) return
    try {
      const resp = await fetchNotifications(token, { limit: 20 })
      setItems(resp.items)
      setUnreadCount(resp.unread_count)
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        logout()
        return
      }
      // 轮询失败静默 —— 不打断用户,下一轮再试。
    }
  }, [token, logout])

  useEffect(() => {
    if (!token) return
    void refresh()
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [token, refresh])

  useSyncEvent('notification_changed', () => void refresh())

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (next) void refresh()
  }

  const handleMarkRead = async (item: NotificationItem) => {
    if (item.read_at) return
    setItems((prev) =>
      prev.map((row) => (row.id === item.id ? { ...row, read_at: new Date().toISOString() } : row)),
    )
    setUnreadCount((prev) => Math.max(0, prev - 1))
    try {
      await markNotificationRead(token, item.id)
    } catch {
      void refresh()
    }
  }

  // 点列表项:先标记已读 + 弹出「完整内容」弹窗(列表里文字会被截断,先看
  // 完整标题/内容),不直接跳转 —— 跳转交给弹窗里的「查看详情」按钮。
  const handleItemClick = (item: NotificationItem) => {
    void handleMarkRead(item)
    setOpen(false)
    setDetailItem(item)
  }

  // 是否存在可跳转的目标 —— 决定详情弹窗要不要显示「查看详情」按钮。
  const hasJumpTarget = (item: NotificationItem): boolean => {
    const payload = item.payload
    if (!payload) return false
    return Boolean(
      payload.debtId || payload.txId || payload.installmentPlanId ||
        payload.recurringRuleId || payload.accountId || payload.counterpartyName ||
        payload.pendingDividendId,
    )
  }

  // 通知联动跳转:欠款到期/逾期提醒 → 欠款页(高亮该笔);带 txId 的通知
  // (目前是分期付款结清 —— 结清当下必然新增/关联一笔交易)→ 直接开那笔
  // 交易的详情弹窗,跟 DebtsPanel 还款记录跳转、退款双向勾稽同一招
  // (`dispatchOpenDetailTx`);信用卡帐单到期/逾期/自动扣缴(§2.9 card_due)
  // 带 accountId → 开该帐户的详情弹窗(合併帳單卡片就在里面);週期性收支
  // 续期通知没有单一交易可指,退回对应列表页。§5.5 未結清對象清單
  // (category="debt_unsettled")只带 counterpartyName(對象分組摘要,沒有
  // 單一 debtId)→ 退回欠款頁,不帶 highlight。找不到目标时静默不跳转。
  const handleJumpToDetail = async (item: NotificationItem) => {
    const payload = item.payload
    if (!payload) return
    const debtId = typeof payload.debtId === 'string' ? payload.debtId : null
    const txId = typeof payload.txId === 'string' ? payload.txId : null
    const installmentPlanId =
      typeof payload.installmentPlanId === 'string' ? payload.installmentPlanId : null
    const recurringRuleId = typeof payload.recurringRuleId === 'string' ? payload.recurringRuleId : null
    const accountId = typeof payload.accountId === 'string' ? payload.accountId : null
    const counterpartyName = typeof payload.counterpartyName === 'string' ? payload.counterpartyName : null

    // 股利待確認(category="dividend",股票持股 Phase 2)→ 投資頁的待確認股利區塊。
    // 要排在 accountId 前面:payload 也帶了投資理財帳戶 id。
    if (payload.pendingDividendId !== undefined && payload.pendingDividendId !== null) {
      navigate('/app/investments')
      setDetailItem(null)
      return
    }
    if (debtId) {
      navigate(`/app/debts?highlight=${encodeURIComponent(debtId)}`)
      setDetailItem(null)
      return
    }
    if (txId) {
      try {
        const page = await fetchWorkspaceTransactions(token, { txSyncId: txId, limit: 1 })
        const found = page.items[0]
        if (found) {
          dispatchOpenDetailTx(found)
          setDetailItem(null)
          return
        }
      } catch {
        // 查不到就往下 fallback,不打断使用者
      }
    }
    if (accountId) {
      setJumping(true)
      try {
        // 不带 ledgerId 过滤 —— account 是 user-global 实体,靠 accountId 精确
        // 匹配已经够用;历史上 `ledgerId` 字段被后端误存成内部 PK 而非
        // external_id(2026-08-04 修复),带着它过滤反而会让老通知永远查不到。
        const accounts = await fetchWorkspaceAccounts(token, {})
        const found = accounts.find((a) => a.id === accountId)
        if (found) {
          navigate('/app/accounts')
          dispatchOpenDetailAccount(found, { defaultScope: 'all' })
          setDetailItem(null)
          return
        }
      } catch {
        // 查不到就往下 fallback,不打断使用者
      } finally {
        setJumping(false)
      }
    }
    if (installmentPlanId) {
      navigate('/app/installment-plans')
      setDetailItem(null)
      return
    }
    if (recurringRuleId) {
      navigate('/app/recurring-rules')
      setDetailItem(null)
      return
    }
    if (counterpartyName) {
      navigate('/app/debts')
      setDetailItem(null)
    }
  }

  const handleMarkAllRead = async () => {
    if (unreadCount === 0 || loading) return
    setLoading(true)
    try {
      await markAllNotificationsRead(token)
      setItems((prev) => prev.map((row) => ({ ...row, read_at: row.read_at || new Date().toISOString() })))
      setUnreadCount(0)
    } catch {
      void refresh()
    } finally {
      setLoading(false)
    }
  }

  return (
    <DropdownMenu open={open} onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={t('notifications.title')}
          aria-label={t('notifications.title')}
          className="relative flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-primary/15 hover:text-primary"
        >
          <Bell className="h-4 w-4" />
          {unreadCount > 0 ? (
            <span className="absolute right-0.5 top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-destructive px-1 text-[9px] font-semibold leading-none text-destructive-foreground">
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          ) : null}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 rounded-xl border-border/60 bg-card/95 p-0">
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="text-[13px] font-semibold">{t('notifications.title')}</span>
          <button
            type="button"
            disabled={unreadCount === 0 || loading}
            onClick={() => void handleMarkAllRead()}
            className="text-[11px] text-primary transition-opacity hover:opacity-70 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t('notifications.markAllRead')}
          </button>
        </div>
        <div className="mx-2 h-px bg-border/60" />
        <div className="max-h-96 overflow-y-auto p-1.5">
          {items.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              {t('notifications.empty')}
            </p>
          ) : (
            items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => handleItemClick(item)}
                className={`flex w-full flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent/40 ${
                  item.read_at ? '' : 'bg-primary/5'
                }`}
              >
                <span className="flex w-full items-center gap-1.5">
                  {!item.read_at ? (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                  ) : null}
                  <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">
                    {item.title}
                  </span>
                </span>
                {item.body ? (
                  <span className="w-full truncate text-[11px] text-muted-foreground">
                    {item.body}
                  </span>
                ) : null}
                <span className="text-[10px] text-muted-foreground/70">
                  {formatRelativeTime(item.created_at, t)}
                </span>
              </button>
            ))
          )}
        </div>
      </DropdownMenuContent>

      {/* 列表里文字截断,点开先看完整标题/内容;有可跳转目标才显示「查看详情」。 */}
      <Dialog open={detailItem !== null} onOpenChange={(v) => !v && setDetailItem(null)}>
        <DialogContent className="max-w-sm">
          {detailItem ? (
            <>
              <DialogHeader>
                <DialogTitle>{detailItem.title}</DialogTitle>
              </DialogHeader>
              <div className="space-y-2">
                {detailItem.body ? (
                  <p className="whitespace-pre-wrap text-sm text-muted-foreground">{detailItem.body}</p>
                ) : null}
                <p className="text-[11px] text-muted-foreground/70">
                  {formatRelativeTime(detailItem.created_at, t)}
                </p>
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  className="rounded-md border border-border px-3 py-1.5 text-sm"
                  onClick={() => setDetailItem(null)}
                >
                  {t('dialog.cancel')}
                </button>
                {hasJumpTarget(detailItem) ? (
                  <button
                    type="button"
                    disabled={jumping}
                    className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
                    onClick={() => void handleJumpToDetail(detailItem)}
                  >
                    {t('notifications.viewDetail')}
                  </button>
                ) : null}
              </div>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </DropdownMenu>
  )
}

function formatRelativeTime(iso: string, t: (key: string, params?: Record<string, string | number>) => string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffMs = Date.now() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return t('notifications.time.justNow')
  if (diffMin < 60) return t('notifications.time.minutesAgo', { n: diffMin })
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return t('notifications.time.hoursAgo', { n: diffHour })
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 30) return t('notifications.time.daysAgo', { n: diffDay })
  return d.toLocaleDateString()
}
