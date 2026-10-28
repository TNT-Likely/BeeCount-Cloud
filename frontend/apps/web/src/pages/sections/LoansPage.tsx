import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createLoan,
  deleteLoan,
  fetchReadLoans,
  fetchWorkspaceAccounts,
  updateLoan,
  type ReadLoan,
  type LoanUpdatePayload,
  type WorkspaceAccount,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import {
  LoansPanel,
  loanDefaults,
  validateLoanInput,
  type LoanForm,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 借款页(0023)。
 *
 * read 投影拉借款列表 + workspace accounts 做下拉。金额/枚举/日期校验由服务端
 * 权威校验,前端 validateLoanInput 只做即时预提示。
 */

export function LoansPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [rows, setRows] = usePageCache<ReadLoan[]>(`loans:${bucket}:rows`, [])
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('loans:accounts', [])
  const [form, setForm] = useState<LoanForm>(loanDefaults())

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
        fetchReadLoans(token, activeLedgerId),
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

  const buildPayload = useCallback((f: LoanForm) => {
    const principal = Number(f.principal || 0)
    const interestRate = Number(f.interest_rate || 0)
    const repaidAmount = Number(f.repaid_amount || 0)
    return {
      direction: f.direction,
      counterparty: f.counterparty.trim(),
      principal: Number.isFinite(principal) ? principal : 0,
      currency: f.currency.trim() || null,
      interest_rate: Number.isFinite(interestRate) ? interestRate : 0,
      started_at: f.started_at,
      due_at: f.due_at ? f.due_at : null,
      repaid_amount: Number.isFinite(repaidAmount) ? repaidAmount : 0,
      status: f.status,
      account_id: f.account_id || null,
      account_name: f.account_name || null,
      note: f.note.trim() || null,
    }
  }, [])

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const error = validateLoanInput({
      counterparty: form.counterparty,
      principal: Number(form.principal),
      interestRate: Number(form.interest_rate),
      startedAt: form.started_at,
      dueAt: form.due_at || null,
      repaidAmount: Number(form.repaid_amount),
    })
    if (error) {
      toast.error(t(`loans.error.${error}`), t('notice.error'))
      return false
    }
    try {
      const payload = buildPayload(form)
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateLoan(token, activeLedgerId, form.editingId!, base, payload),
        )
        notifySuccess(t('loans.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createLoan(token, activeLedgerId, base, payload),
        )
        notifySuccess(t('loans.notice.created'))
      }
      setForm(loanDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onAdvanceStatus = async (
    row: ReadLoan,
    nextStatus: LoanUpdatePayload['status'],
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateLoan(token, activeLedgerId, row.id, base, { status: nextStatus }),
      )
      notifySuccess(t('loans.notice.statusAdvanced'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onDelete = async (row: ReadLoan): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteLoan(token, activeLedgerId, row.id, base),
      )
      notifySuccess(t('loans.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.loans')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <LoansPanel
            loans={rows}
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
