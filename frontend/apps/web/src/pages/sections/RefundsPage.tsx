import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  createRefund,
  deleteRefund,
  fetchReadRefunds,
  fetchWorkspaceAccounts,
  fetchWorkspaceTransactions,
  updateRefund,
  type ReadRefund,
  type RefundUpdatePayload,
  type WorkspaceAccount,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  RefundsPanel,
  refundDefaults,
  refundLegsToWire,
  validateRefundInput,
  type RefundForm,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { dispatchOpenDetailTx } from '../../lib/txDialogEvents'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 交易退款页(0022,App 端 issue #465 退款功能)。
 *
 * 跟 ItemReturnsPage 同构:read 投影拉退款列表 + workspace accounts 做下拉 +
 * workspace 支出交易做原单选择器候选。累计不超原单由服务端权威校验,
 * 前端 validateRefundInput 只做即时预提示。
 */

/** 原单选择器一次性拉的支出交易上限(按日期倒序取最近 N 笔)。 */
const TX_PICKER_LIMIT = 200

export function RefundsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [rows, setRows] = usePageCache<ReadRefund[]>(`refunds:${bucket}:rows`, [])
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('refunds:accounts', [])
  const [txs, setTxs] = usePageCache<WorkspaceTransaction[]>(`refunds:${bucket}:txs`, [])
  const [form, setForm] = useState<RefundForm>(refundDefaults())

  // 从交易详情「查看退款 · N」跳过来时带 ?tx=<原单 id>:用它预填搜索
  // (自动过滤出该原单的退款)并高亮这些行;读完即从 URL 清掉(replace,
  // 不留历史),刷新不再复现,用户可手动改搜索词。
  const [searchParams, setSearchParams] = useSearchParams()
  const [highlightTxId, setHighlightTxId] = useState<string | null>(
    () => searchParams.get('tx'),
  )
  useEffect(() => {
    if (!searchParams.get('tx')) return
    const next = new URLSearchParams(searchParams)
    next.delete('tx')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setRows([])
      setTxs([])
      return
    }
    try {
      const [list, accts, txPage] = await Promise.all([
        fetchReadRefunds(token, activeLedgerId),
        fetchWorkspaceAccounts(token, {}),
        fetchWorkspaceTransactions(token, {
          ledgerId: activeLedgerId,
          txType: 'expense',
          limit: TX_PICKER_LIMIT,
        }),
      ])
      setRows(list)
      setAccounts(accts)
      setTxs(txPage.items || [])
    } catch (err) {
      notifyError(err)
    }
    // setRows / setAccounts / setTxs 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  const accountOptions = useMemo(
    () =>
      accounts
        .filter((a) => (a.name || '').trim())
        .map((a) => ({ id: a.id, name: (a.name || '').trim() })),
    [accounts],
  )

  const accountIdByName = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of accountOptions) map.set(a.name.toLowerCase(), a.id)
    return map
  }, [accountOptions])

  const buildPayload = useCallback(
    (f: RefundForm) => {
      const amount = Number(f.amount || 0)
      const refund_mode = f.refund_mode || 'unified'
      const refund_legs =
        refund_mode === 'split' ? refundLegsToWire(f.refund_legs, accountIdByName) : null
      return {
        original_tx_id: f.original_tx_id.trim(),
        amount: Number.isFinite(amount) ? amount : 0,
        currency: f.currency.trim() || null,
        account_id: f.account_id || null,
        account_name: f.account_name || null,
        refund_mode,
        refund_legs,
        happened_at: f.happened_at,
        reason: f.reason.trim() || null,
        status: f.status,
        note: f.note.trim() || null,
      }
    },
    [accountIdByName],
  )

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    // 跟面板预校验同规则;面板已禁提交,这里双保险并给出 localized toast。
    const tx = txs.find((x) => x.id === form.original_tx_id)
    const others = rows
      .filter(
        (r) =>
          r.status !== 'cancelled' &&
          r.id !== form.editingId &&
          r.original_tx_id === form.original_tx_id,
      )
      .reduce((sum, r) => sum + (r.amount || 0), 0)
    const originalAmount = tx ? Number(tx.amount) || 0 : Number(form.original_amount) || 0
    const error = validateRefundInput({
      originalTxId: form.original_tx_id,
      amount: Number(form.amount),
      remainingRefundable: Math.max(originalAmount - others, 0),
    })
    if (error) {
      toast.error(t(`refunds.error.${error}`), t('notice.error'))
      return false
    }
    try {
      const payload = buildPayload(form)
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateRefund(token, activeLedgerId, form.editingId!, base, payload),
        )
        notifySuccess(t('refunds.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createRefund(token, activeLedgerId, base, payload),
        )
        notifySuccess(t('refunds.notice.created'))
      }
      setForm(refundDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onAdvanceStatus = async (
    row: ReadRefund,
    nextStatus: RefundUpdatePayload['status'],
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateRefund(token, activeLedgerId, row.id, base, { status: nextStatus }),
      )
      notifySuccess(t('refunds.notice.statusAdvanced'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onDelete = async (row: ReadRefund): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteRefund(token, activeLedgerId, row.id, base),
      )
      notifySuccess(t('refunds.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  /**
   * 0028:退款 → 关联交易。就地打开原单详情弹窗(GlobalEntityDialogs 在
   * AppShell 顶层监听,任何页面都能弹),不跳页面、也不打断当前筛选。
   *
   * 原单优先取本页已缓存的 txs(最近 TX_PICKER_LIMIT 笔支出);不在缓存里
   * (更老的支出)时按 txSyncId 单独拉一笔再打开,拉不到 = 原单已删除。
   */
  const onOpenOriginalTx = useCallback(
    async (txId: string) => {
      if (!txId) return
      const cached = txs.find((x) => x.id === txId)
      if (cached) {
        dispatchOpenDetailTx(cached)
        return
      }
      if (!activeLedgerId) {
        toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
        return
      }
      try {
        const page = await fetchWorkspaceTransactions(token, {
          ledgerId: activeLedgerId,
          txSyncId: txId,
          limit: 1,
        })
        const found = page.items?.[0]
        if (!found) {
          toast.error(t('refunds.notice.txNotFound'), t('notice.error'))
          return
        }
        dispatchOpenDetailTx(found)
      } catch (err) {
        notifyError(err)
      }
    },
    [txs, activeLedgerId, token, toast, t, notifyError],
  )

  // 布局对齐流水页(TransactionsPage):外层不套 Card,顶部直接是面板
  // 自带的一行 toolbar(搜索 + 状态筛选 + 新建),列表是行式紧凑排布。
  return (
    <div className="space-y-4 pb-20 md:pb-0">
      {!activeLedgerId ? (
        <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
      ) : (
        <RefundsPanel
          refunds={rows}
          txs={txs}
          accountOptions={accountOptions}
          currency={currency}
          form={form}
          onFormChange={setForm}
          onSubmit={onSubmit}
          onAdvanceStatus={onAdvanceStatus}
          onDelete={onDelete}
          onOpenOriginalTx={(txId) => void onOpenOriginalTx(txId)}
          canManage={Boolean(activeLedgerId)}
          initialQuery={highlightTxId ?? ''}
          highlightTxId={highlightTxId}
        />
      )}
    </div>
  )
}
