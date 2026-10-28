import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createTimeDeposit,
  deleteTimeDeposit,
  fetchReadTimeDepositTransactions,
  fetchReadTimeDeposits,
  fetchWorkspaceAccounts,
  updateTimeDeposit,
  type ReadTimeDeposit,
  type ReadTransaction,
  type WorkspaceAccount,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import {
  TimeDepositsPanel,
  timeDepositDefaults,
  type TimeDepositForm,
} from '@beecount/web-features'
import { rateWireFromPercent } from '@beecount/web-features'
import { payoutPlansToWire } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { dispatchOpenNewTx } from '../../lib/txDialogEvents'

/**
 * 定期存款页(0021,App 端 issue #80 诉求)。
 *
 * 数据流跟 BudgetsPage 同构:read 投影拉列表 + workspace accounts 做账户
 * 下拉;写走通用 write 端点 + useLedgerWrite 的 CAS 重试;派生利息/剩余
 * 天数由服务端按今天现算,前端不存。
 */

export function TimeDepositsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [deposits, setDeposits] = usePageCache<ReadTimeDeposit[]>(
    `time-deposits:${bucket}:rows`,
    [],
  )
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('time-deposits:accounts', [])
  const [form, setForm] = useState<TimeDepositForm>(timeDepositDefaults())

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast],
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setDeposits([])
      return
    }
    try {
      const [rows, accts] = await Promise.all([
        fetchReadTimeDeposits(token, activeLedgerId),
        fetchWorkspaceAccounts(token, {}),
      ])
      setDeposits(rows)
      setAccounts(accts)
    } catch (err) {
      notifyError(err)
    }
    // setDeposits / setAccounts 来自 usePageCache,引用稳定
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

  /** 表单 → wire payload。年利率按「3.25 = 3.25%」口径 /100。 */
  const buildPayload = useCallback(
    (f: TimeDepositForm) => {
      const actualRaw = f.actual_interest.trim()
      const actual = actualRaw === '' ? null : Number(actualRaw)
      return {
        principal: Number(f.principal || 0),
        currency: f.currency.trim() || null,
        annual_rate: rateWireFromPercent(f.annual_rate),
        term_months: Number(f.term_months || 12),
        start_date: f.start_date,
        maturity_date: f.maturity_date.trim() || null,
        status: f.status,
        account_id: f.account_id || null,
        account_name: f.account_name || null,
        auto_renew: f.auto_renew,
        interest_mode: f.interest_mode,
        note: f.note.trim() || null,
        withdrawn_at: f.status === 'withdrawn' ? f.withdrawn_at || null : null,
        actual_interest: f.status === 'withdrawn' ? actual : null,
        // 到息计划(0028,多笔):空数组 = 无计划;无效行由转换器丢掉。
        payout_plans: payoutPlansToWire(f.payout_plans)
      }
    },
    [],
  )

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const principal = Number((form.principal || '').toString().trim())
    if (!Number.isFinite(principal) || principal <= 0) {
      toast.error(t('timeDeposits.error.principalInvalid'), t('notice.error'))
      return false
    }
    const term = Number(form.term_months || 12)
    if (!Number.isFinite(term) || term < 1 || term > 600) {
      toast.error(t('timeDeposits.error.termInvalid'), t('notice.error'))
      return false
    }
    if (!form.start_date.trim()) {
      toast.error(t('timeDeposits.error.startDateRequired'), t('notice.error'))
      return false
    }
    if (form.status === 'withdrawn' && !form.withdrawn_at.trim()) {
      toast.error(t('timeDeposits.error.withdrawnAtRequired'), t('notice.error'))
      return false
    }
    try {
      const payload = buildPayload(form)
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateTimeDeposit(token, activeLedgerId, form.editingId!, base, payload),
        )
        notifySuccess(t('timeDeposits.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createTimeDeposit(token, activeLedgerId, base, payload),
        )
        notifySuccess(t('timeDeposits.notice.created'))
      }
      setForm(timeDepositDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onWithdraw = async (
    deposit: ReadTimeDeposit,
    payload: { withdrawn_at: string; actual_interest: number | null },
  ): Promise<void> => {
    if (!activeLedgerId) return
    if (!payload.withdrawn_at.trim()) {
      toast.error(t('timeDeposits.error.withdrawnAtRequired'), t('notice.error'))
      return
    }
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateTimeDeposit(token, activeLedgerId, deposit.id, base, {
          status: 'withdrawn',
          withdrawn_at: payload.withdrawn_at,
          actual_interest: payload.actual_interest
        }),
      )
      notifySuccess(t('timeDeposits.notice.withdrawn'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onDelete = async (deposit: ReadTimeDeposit): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteTimeDeposit(token, activeLedgerId, deposit.id, base),
      )
      notifySuccess(t('timeDeposits.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  // 0030 利息按键:打开"新建收入交易"并预填到这笔存款(形成关联利息流水)。
  // 预填类型=income、建议金额=预期利息、账户=存款关联账户、timeDepositId=本存款。
  const onAddInterest = (deposit: ReadTimeDeposit) => {
    dispatchOpenNewTx({
      ledgerId: activeLedgerId || undefined,
      txType: 'income',
      amount:
        deposit.expected_interest > 0
          ? String(Math.round(deposit.expected_interest * 100) / 100)
          : '',
      accountName: deposit.account_name || '',
      timeDepositId: deposit.id,
      happenedAt: new Date().toISOString()
    })
  }

  // 0030 拉某笔存款的关联交易(利息流水),供卡片内展开查看。
  const onLoadDepositTransactions = async (
    depositId: string,
  ): Promise<ReadTransaction[]> => {
    if (!activeLedgerId) return []
    return fetchReadTimeDepositTransactions(token, activeLedgerId, depositId)
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.timeDeposits')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <TimeDepositsPanel
            deposits={deposits}
            accountOptions={accountOptions}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onWithdraw={onWithdraw}
            onDelete={onDelete}
            onAddInterest={onAddInterest}
            onLoadDepositTransactions={onLoadDepositTransactions}
            canManage={Boolean(activeLedgerId)}
          />
        )}
      </CardContent>
    </Card>
  )
}
