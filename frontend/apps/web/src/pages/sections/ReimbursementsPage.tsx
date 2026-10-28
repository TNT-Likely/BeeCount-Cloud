import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createReimbursement,
  deleteReimbursement,
  fetchReadReimbursements,
  fetchWorkspaceAccounts,
  updateReimbursement,
  type ReadReimbursement,
  type ReimbursementUpdatePayload,
  type WorkspaceAccount,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import {
  ReimbursementsPanel,
  reimbursementDefaults,
  validateReimbursementInput,
  type ReimbursementForm,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 报销页(0023)。
 *
 * read 投影拉报销列表 + workspace accounts 做下拉。金额/必填/枚举校验由服务端
 * 权威校验,前端 validateReimbursementInput 只做即时预提示。
 */

export function ReimbursementsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [rows, setRows] = usePageCache<ReadReimbursement[]>(`reimbursements:${bucket}:rows`, [])
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('reimbursements:accounts', [])
  const [form, setForm] = useState<ReimbursementForm>(reimbursementDefaults())

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
      return
    }
    try {
      const [list, accts] = await Promise.all([
        fetchReadReimbursements(token, activeLedgerId),
        fetchWorkspaceAccounts(token, {}),
      ])
      setRows(list)
      setAccounts(accts)
    } catch (err) {
      notifyError(err)
    }
    // setRows / setAccounts 来自 usePageCache,引用稳定
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

  const buildPayload = useCallback((f: ReimbursementForm) => {
    const amount = Number(f.amount || 0)
    return {
      title: f.title.trim(),
      payer: f.payer.trim() || null,
      reimburser: f.reimburser.trim() || null,
      amount: Number.isFinite(amount) ? amount : 0,
      currency: f.currency.trim() || null,
      category_id: f.category_id || null,
      category_name: f.category_name || null,
      happened_at: f.happened_at,
      status: f.status,
      account_id: f.account_id || null,
      account_name: f.account_name || null,
      related_tx_id: f.related_tx_id.trim() || null,
      note: f.note.trim() || null,
    }
  }, [])

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const error = validateReimbursementInput({
      title: form.title,
      amount: Number(form.amount),
    })
    if (error) {
      toast.error(t(`reimbursements.error.${error}`), t('notice.error'))
      return false
    }
    try {
      const payload = buildPayload(form)
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateReimbursement(token, activeLedgerId, form.editingId!, base, payload),
        )
        notifySuccess(t('reimbursements.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createReimbursement(token, activeLedgerId, base, payload),
        )
        notifySuccess(t('reimbursements.notice.created'))
      }
      setForm(reimbursementDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onAdvanceStatus = async (
    row: ReadReimbursement,
    nextStatus: ReimbursementUpdatePayload['status'],
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateReimbursement(token, activeLedgerId, row.id, base, { status: nextStatus }),
      )
      notifySuccess(t('reimbursements.notice.statusAdvanced'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onDelete = async (row: ReadReimbursement): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteReimbursement(token, activeLedgerId, row.id, base),
      )
      notifySuccess(t('reimbursements.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.reimbursements')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <ReimbursementsPanel
            reimbursements={rows}
            accountOptions={accountOptions}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onAdvanceStatus={onAdvanceStatus}
            onDelete={onDelete}
            canManage={Boolean(activeLedgerId)}
          />
        )}
      </CardContent>
    </Card>
  )
}
