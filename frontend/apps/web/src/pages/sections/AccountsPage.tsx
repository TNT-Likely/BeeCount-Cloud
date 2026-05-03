import { useCallback, useEffect, useState } from 'react'

import {
  createAccount,
  deleteAccount,
  fetchWorkspaceAccounts,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  updateAccount,
  type ReadAccount,
  type WorkspaceAccount,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  AccountsPanel,
  ConfirmDialog,
  accountDefaults,
  type AccountForm,
} from '@beecount/web-features'

import { AccountDetailDialog } from '../../components/dialogs/AccountDetailDialog'
import { onOpenDetailAccount } from '../../lib/txDialogEvents'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

const ACCOUNT_DETAIL_PAGE_SIZE = 20

/**
 * 账户 / 资产页 —— 账户列表 + CRUD(无 delete,web 只支持创建/编辑)
 * + 账户详情 dialog(点卡片弹出该账户的交易列表,无限滚动)。
 *
 * tags 独立 fetch 一份,只为 AccountDetailDialog 里 TransactionList 渲染
 * tag chip 用 —— 不跟其它 page 共享,每次进入该页现拉。
 *
 * 已知回归:AccountDetailDialog 的附件预览(resolveAttachmentPreviewUrl /
 * onPreviewAttachment)本轮留空,预览功能待 "附件预览共享 hook" 独立 task。
 */
export function AccountsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  // 主要数据走 PageDataCache —— 切走再切回来立刻显示上次的值,不闪烁。
  // rows 用 WorkspaceAccount(包含 tx_count / balance 等聚合字段),删除前需要
  // 看 tx_count 决定是否提示用户(对齐 mobile account_edit_page._delete)。
  const [rows, setRows] = usePageCache<WorkspaceAccount[]>('accounts:rows', [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('accounts:tags', [])
  const [form, setForm] = useState<AccountForm>(accountDefaults())
  // 删除前的待确认账户。null = 无 pending。WorkspaceAccount 带 tx_count 字段,
  // confirm dialog 直接读它,不再发额外请求。
  const [pendingDelete, setPendingDelete] = useState<WorkspaceAccount | null>(null)
  const [deleting, setDeleting] = useState(false)

  const [detail, setDetail] = useState<ReadAccount | null>(null)
  // 把"打开账户详情"的逻辑提到 useCallback 里,既给本页 onClickAccount 用,
  // 也给 CommandPalette 派发的事件用。复用 = 行为一致 + 维护单点。
  const [detailTx, setDetailTx] = useState<WorkspaceTransaction[]>([])
  const [detailTotal, setDetailTotal] = useState(0)
  const [detailOffset, setDetailOffset] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    try {
      const [accountRows, tagRows] = await Promise.all([
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceTags(token, { limit: 500 }),
      ])
      setRows(accountRows)
      setTags(tagRows)
    } catch (err) {
      notifyError(err)
    }
  }, [token, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  const onSave = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      toast.error(t('accounts.error.nameRequired'), t('notice.error'))
      return false
    }
    // mobile account_edit_page 也禁止重名,跨端一致。编辑自己时跳过。
    const duplicate = rows.find(
      (row) =>
        (row.name || '').trim().toLowerCase() === trimmedName.toLowerCase() &&
        row.id !== form.editingId,
    )
    if (duplicate) {
      toast.error(t('accounts.error.nameDuplicate'), t('notice.error'))
      return false
    }
    const initialBalanceNum = Number(form.initial_balance || 0)
    if (!Number.isFinite(initialBalanceNum)) {
      toast.error(t('accounts.error.balanceInvalid'), t('notice.error'))
      return false
    }
    // 信用卡日期校验:1-31,空字符串视作未填(null)。其他类型不要这两个字段,
    // 走 onFormChange 切换类型时已经清空,这里再 guard 一次。
    const billingDayNum =
      form.account_type === 'credit_card' && form.billing_day.trim()
        ? Math.round(Number(form.billing_day))
        : null
    if (billingDayNum !== null && (!Number.isFinite(billingDayNum) || billingDayNum < 1 || billingDayNum > 31)) {
      toast.error(t('accounts.error.billingDayInvalid'), t('notice.error'))
      return false
    }
    const paymentDueDayNum =
      form.account_type === 'credit_card' && form.payment_due_day.trim()
        ? Math.round(Number(form.payment_due_day))
        : null
    if (paymentDueDayNum !== null && (!Number.isFinite(paymentDueDayNum) || paymentDueDayNum < 1 || paymentDueDayNum > 31)) {
      toast.error(t('accounts.error.paymentDueDayInvalid'), t('notice.error'))
      return false
    }
    const creditLimitRaw = form.credit_limit.trim()
    const creditLimitNum =
      form.account_type === 'credit_card' && creditLimitRaw ? Number(creditLimitRaw) : null
    if (creditLimitNum !== null && (!Number.isFinite(creditLimitNum) || creditLimitNum < 0)) {
      toast.error(t('accounts.error.creditLimitInvalid'), t('notice.error'))
      return false
    }
    try {
      const isCreditCard = form.account_type === 'credit_card'
      const isBankOrCredit = isCreditCard || form.account_type === 'bank_card'
      const payload = {
        name: trimmedName,
        account_type: form.account_type || null,
        currency: form.currency || null,
        initial_balance: initialBalanceNum,
        // 扩展字段:non-credit_card 类型显式传 null 清空 server 上残留的值;
        // bank_card / credit_card 才有 bank_name / card_last_four。
        note: form.note.trim() || null,
        credit_limit: isCreditCard ? creditLimitNum : null,
        billing_day: isCreditCard ? billingDayNum : null,
        payment_due_day: isCreditCard ? paymentDueDayNum : null,
        bank_name: isBankOrCredit ? form.bank_name.trim() || null : null,
        card_last_four: isBankOrCredit ? form.card_last_four.trim() || null : null,
      }
      await retryOnConflict(activeLedgerId, (base) =>
        form.editingId
          ? updateAccount(token, activeLedgerId, form.editingId, base, payload)
          : createAccount(token, activeLedgerId, base, payload)
      )
      setForm(accountDefaults())
      await refresh()
      notifySuccess(form.editingId ? t('notice.accountUpdated') : t('notice.accountCreated'))
      return true
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
        notifyError(err)
        return false
      }
      notifyError(err)
      return false
    }
  }

  const loadDetailPage = useCallback(
    async (accountName: string, offset: number) => {
      setDetailLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          accountName,
          limit: ACCOUNT_DETAIL_PAGE_SIZE,
          offset,
        })
        setDetailTx((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setDetailTotal(page.total)
        setDetailOffset(offset + page.items.length)
      } catch (err) {
        notifyError(err)
      } finally {
        setDetailLoading(false)
      }
    },
    [token, notifyError]
  )

  const closeDetail = () => {
    setDetail(null)
    setDetailTx([])
    setDetailTotal(0)
    setDetailOffset(0)
  }

  const openDetail = useCallback(
    (row: ReadAccount) => {
      setDetail(row)
      setDetailTx([])
      setDetailTotal(0)
      setDetailOffset(0)
      void loadDetailPage(row.name, 0)
    },
    [loadDetailPage],
  )

  // 监听 CommandPalette 派发的「打开账户详情」事件
  useEffect(() => {
    return onOpenDetailAccount((account) => {
      openDetail(account)
    })
  }, [openDetail])

  // 删除流程:点删除按钮 → 弹 ConfirmDialog,dialog 里根据 tx_count 决定文案。
  // 跟 mobile account_edit_page._delete 对齐:有交易则警示总条数 + 红色按钮。
  const onConfirmDelete = async () => {
    if (!pendingDelete) return
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    setDeleting(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteAccount(token, activeLedgerId, pendingDelete.id, base),
      )
      setPendingDelete(null)
      await refresh()
      notifySuccess(t('notice.accountDeleted'))
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
      }
      notifyError(err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <AccountsPanel
        form={form}
        rows={rows}
        canManage
        onFormChange={setForm}
        onSave={onSave}
        onReset={() => setForm(accountDefaults())}
        onEdit={(row) => {
          setForm({
            editingId: row.id,
            editingOwnerUserId: row.created_by_user_id || '',
            name: row.name,
            account_type: row.account_type || '',
            currency: row.currency || '',
            initial_balance: String(row.initial_balance ?? 0),
            note: row.note ?? '',
            credit_limit: row.credit_limit !== null && row.credit_limit !== undefined
              ? String(row.credit_limit)
              : '',
            billing_day: row.billing_day !== null && row.billing_day !== undefined
              ? String(row.billing_day)
              : '',
            payment_due_day: row.payment_due_day !== null && row.payment_due_day !== undefined
              ? String(row.payment_due_day)
              : '',
            bank_name: row.bank_name ?? '',
            card_last_four: row.card_last_four ?? '',
          })
        }}
        onClickAccount={openDetail}
        onDelete={(row) => {
          // 严格策略:有关联交易直接拒绝,不弹"是否强制删除"。先要求用户在
          // 详情页/交易页把这些交易改/删/迁走,账户回到 0 笔再来删。比 mobile
          // 现在的"warn + allow orphan"更严格 —— 避免误删导致一堆 ungrouped
          // 交易污染 ledger。
          const ws = rows.find((r) => r.id === row.id) || (row as WorkspaceAccount)
          if ((ws.tx_count ?? 0) > 0) {
            toast.error(
              t('accounts.delete.blockedByTransactions', {
                name: ws.name,
                count: ws.tx_count ?? 0,
              }),
              t('notice.error'),
            )
            return
          }
          setPendingDelete(ws)
        }}
      />
      <AccountDetailDialog
        account={detail}
        transactions={detailTx}
        total={detailTotal}
        offset={detailOffset}
        loading={detailLoading}
        tags={tags}
        onClose={closeDetail}
        onLoadMore={(name, offset) => void loadDetailPage(name, offset)}
      />
      {/* 删除确认 — 有 tx 时显示 warning 文案 + count(对齐 mobile);无 tx
          就普通确认。dialog confirm 后调 deleteAccount,server 端会 silent
          orphan 关联交易(snapshot_mutator.delete_account 已实现 strip
          accountName)—— 跟 mobile 同款语义。 */}
      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void onConfirmDelete()}
        loading={deleting}
        title={t('dialog.confirm')}
        description={t('accounts.delete.confirmMessage', { name: pendingDelete?.name || '' })}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </>
  )
}
