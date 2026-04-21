import { useCallback, useEffect, useState } from 'react'

import {
  createAccount,
  fetchWorkspaceAccounts,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  updateAccount,
  type ReadAccount,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  AccountsPanel,
  accountDefaults,
  type AccountForm,
} from '@beecount/web-features'

import { AccountDetailDialog } from '../../components/dialogs/AccountDetailDialog'
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
  const [rows, setRows] = usePageCache<ReadAccount[]>('accounts:rows', [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('accounts:tags', [])
  const [form, setForm] = useState<AccountForm>(accountDefaults())

  const [detail, setDetail] = useState<ReadAccount | null>(null)
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
    try {
      const payload = {
        name: form.name,
        account_type: form.account_type || null,
        currency: form.currency || null,
        initial_balance: Number(form.initial_balance || 0),
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
          })
        }}
        onClickAccount={(row) => {
          setDetail(row)
          setDetailTx([])
          setDetailTotal(0)
          setDetailOffset(0)
          void loadDetailPage(row.name, 0)
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
    </>
  )
}
